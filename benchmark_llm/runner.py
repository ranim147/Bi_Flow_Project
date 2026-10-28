"""Client Ollama local, exécutions séquentielles, journal et rapports sans score fictif."""
from __future__ import annotations

from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from urllib import error, parse, request
import uuid

import pandas as pd
import psutil

from .data import export_inputs
from .protocol import MODELS, OPTIONS, SCHEMA, SYSTEM_PROMPT, evaluate, payload_for

# Repères indicatifs ; seul memory_policy="strict" les utilise pour refuser un essai.
MIN_FREE_GIB = {"qwen3:4b": 5.5, "gemma3:4b": 6.5, "granite3.3:2b": 3.5}
MODEL_DOWNLOAD_GB = {"qwen3:4b": 2.5, "gemma3:4b": 3.4, "granite3.3:2b": 1.5}


class OllamaFailure(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(detail)


class OllamaClient:
    def __init__(self, base_url="http://127.0.0.1:11434", timeout=180):
        parsed = parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ce benchmark accepte uniquement un serveur Ollama sur la machine locale.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("URL Ollama invalide.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = request.build_opener(request.ProxyHandler({}))

    def call(self, endpoint, payload=None, timeout=None):
        req = request.Request(self.base_url + "/api/" + endpoint,
                              data=None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as response:
                value = json.load(response)
            if not isinstance(value, dict):
                raise OllamaFailure("invalid_api_response", "La réponse de l'API n'est pas un objet JSON.")
            if isinstance(value, dict) and value.get("error"):
                raise OllamaFailure("server_error", str(value["error"])[:1000])
            return value
        except error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            status = "model_absent" if exc.code == 404 else "http_error"
            if any(word in detail.lower() for word in ["out of memory", "not enough", "insufficient memory", "requires more system memory"]):
                status = "insufficient_memory"
            raise OllamaFailure(status, f"HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            status = "timeout" if isinstance(exc.reason, TimeoutError) else "server_unavailable"
            raise OllamaFailure(status, str(exc.reason)[:1000]) from exc
        except OSError as exc:
            timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
            raise OllamaFailure("timeout" if timed_out else "server_unavailable", str(exc)[:1000]) from exc
        except (ValueError, TypeError) as exc:
            raise OllamaFailure("invalid_api_response", str(exc)[:1000]) from exc

    def installed(self):
        return {m["name"]: m for m in self.call("tags", timeout=5).get("models", [])}


def environment_report(root: Path, model_storage: Path | None = None) -> dict:
    memory = psutil.virtual_memory()
    storage = Path(model_storage or os.getenv("OLLAMA_MODELS", str(Path(root) / "tools/ollama_models")))
    existing = next(p for p in [storage, *storage.parents] if p.exists())
    cli = shutil.which("ollama")
    if not cli and (Path(root) / "tools/ollama/ollama.exe").is_file():
        cli = str(Path(root) / "tools/ollama/ollama.exe")
    gpu = {"status": "unknown", "detail": "nvidia-smi absent ; GPU non NVIDIA ou pilote à vérifier"}
    binary = shutil.which("nvidia-smi")
    if binary:
        try:
            completed = subprocess.run([binary, "--query-gpu=name,memory.total,memory.free", "--format=csv"],
                                       capture_output=True, text=True, timeout=8)
            gpu = {"status": "observed" if completed.returncode == 0 else "unavailable",
                   "detail": completed.stdout.strip() or completed.stderr[:500]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            gpu = {"status": "unknown", "detail": str(exc)[:500]}
    disks = {}
    for drive in {Path(root).anchor, Path(os.environ.get("SystemDrive", "C:") + "/").anchor}:
        if not drive:
            continue
        usage = shutil.disk_usage(drive)
        disks[drive] = {"free_gib": usage.free / 2**30, "total_gib": usage.total / 2**30}
    return {"python": platform.python_version(), "platform": platform.platform(), "cpu": platform.processor(),
            "physical_cores": psutil.cpu_count(logical=False), "logical_cores": psutil.cpu_count(),
            "ram_total_gib": memory.total/2**30, "ram_available_gib": memory.available/2**30,
            "gpu": gpu, "disks": disks, "ollama_cli": cli,
            "model_storage_configured_for_notebook": str(storage),
            "model_storage_free_gib": shutil.disk_usage(existing).free/2**30,
            "storage_note": "Le dossier du serveur déjà lancé doit être configuré séparément via OLLAMA_MODELS.",
            "resource_policy": MIN_FREE_GIB,
            "resource_note": "Estimations prudentes incluant contexte/cache. GPU non requis : CPU ou offload partiel possible."}


def check_registry(models=MODELS) -> list[dict]:
    """Métadonnées seulement. Ne télécharge jamais les poids."""
    results = []
    for model in models:
        name, tag = model.split(":", 1)
        url = f"https://registry.ollama.ai/v2/library/{name}/manifests/{tag}"
        try:
            req = request.Request(url, headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
            with request.urlopen(req, timeout=15) as response:
                metadata = json.load(response)
            results.append({"model": model, "available": True, "url": url,
                            "download_bytes": sum(layer.get("size", 0) for layer in metadata.get("layers", []))})
        except (OSError, ValueError) as exc:
            results.append({"model": model, "available": None, "url": url, "error": str(exc)[:500]})
    return results


def prepare_models(client, root, *, download=False):
    """Les téléchargements restent une étape explicite, après vérification des ressources."""
    if not download:
        return [{"model": model, "download_status": "disabled"} for model in MODELS]
    env = environment_report(root)
    installed = client.installed()
    registry = {r["model"]: r for r in check_registry()}
    results = []
    for model in MODELS:
        if model in installed:
            results.append({"model": model, "download_status": "already_installed"})
            continue
        memory = psutil.virtual_memory().available/2**30
        metadata = registry[model]
        # Prévoir le volume restant et une marge ; actualiser l'espace à chaque pull.
        disk_free = environment_report(root)["model_storage_free_gib"]
        if metadata.get("available") is not True:
            results.append({"model": model, "download_status": "registry_unverified"})
        elif memory < MIN_FREE_GIB[model] or disk_free < 2*metadata["download_bytes"]/2**30 + 2:
            results.append({"model": model, "download_status": "insufficient_resources", "available_ram_gib": memory})
        else:
            # Le serveur choisit son propre stockage. Imposer une configuration explicite.
            if not os.getenv("BIFLOW_SERVER_STORAGE_CONFIRMED") == "1":
                raise ValueError("Confirmer que le serveur stocke bien sur D: avec BIFLOW_SERVER_STORAGE_CONFIRMED=1 avant un pull.")
            result = client.call("pull", {"model": model, "stream": False}, timeout=3600)
            results.append({"model": model, "download_status": result.get("status", "unknown")})
    return results


def new_run_directory(root: Path) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:6]
    path = Path(root) / "outputs/llm_benchmark_local" / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def _json_write(path, obj):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for attempt in range(20):
        try:
            temporary.replace(path)
            break
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 19:
                raise
            time.sleep(.25)  # Indexeur/antivirus Windows : verrou transitoire sur le manifeste.


def release_ollama_memory(client, *, timeout=30, target_model=None):
    """Décharge tous les modèles résidents et vérifie /api/ps avant de poursuivre."""
    resident = client.call("ps", timeout=5).get("models", [])
    names = [m.get("name") or m.get("model") for m in resident]
    if any(not name for name in names):
        raise OllamaFailure("unload_failed", "Modèle résident sans identifiant dans /api/ps.")
    # Un modèle encore en chargement peut ne pas apparaître dans /api/ps.
    if target_model and target_model not in names:
        names.append(target_model)
    for name in names:
        client.call("generate", {"model": name, "keep_alive": 0, "stream": False}, timeout=timeout)
    deadline = time.monotonic() + timeout
    while True:
        remaining = client.call("ps", timeout=5).get("models", [])
        if not remaining:
            gc.collect()
            return {"unloaded_models": names, "ram_available_gib": psutil.virtual_memory().available/2**30}
        if time.monotonic() >= deadline:
            raise OllamaFailure("unload_failed", "Des modèles restent chargés ; le modèle suivant ne sera pas lancé.")
        time.sleep(.25)


def save_checkpoint(cases, runs, manifest, destination, *, model=None):
    """Conserve les scores déjà acquis, même si une campagne est interrompue."""
    destination = Path(destination)
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["status_counts"] = pd.Series([r["status"] for r in runs], dtype="object").value_counts().to_dict()
    _json_write(destination / "manifest.json", manifest)
    if not runs:
        return
    scores, comparison = score_runs(cases, runs, destination)
    write_report(comparison, scores, manifest, destination)
    if model:
        model_runs = [r for r in runs if r["model"] == model]
        if model_runs:
            model_dir = destination / "models" / model.replace(":", "_")
            model_dir.mkdir(parents=True, exist_ok=True)
            score_runs(cases, model_runs, model_dir)
            _json_write(model_dir / "progress.json", {"model": model, "saved_cases": len(model_runs),
                        "planned_cases": len(cases)*manifest["repetitions"],
                        "updated_at_utc": manifest["updated_at_utc"]})


def run_benchmark(root, bundle, cases, client, destination, *, run_models=True, repetitions=1,
                  models=MODELS, memory_policy="attempt"):
    if not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError("repetitions doit être un entier positif.")
    models = tuple(models)
    if not models or len(set(models)) != len(models) or not set(models).issubset(MODELS):
        raise ValueError("Choisir des modèles distincts parmi MODELS.")
    if memory_policy not in {"attempt", "strict"}:
        raise ValueError("memory_policy doit être attempt ou strict.")
    if not cases:
        raise ValueError("Le benchmark doit contenir au moins un cas.")
    destination = Path(destination)
    if (destination / "responses.jsonl").exists():
        raise FileExistsError("Ce dossier contient déjà des réponses. Créer une nouvelle campagne pour les conserver.")
    export_inputs(bundle, cases, destination / "inputs")
    env = environment_report(Path(root))
    installed, server_error, version = {}, None, None
    try:
        version = client.call("version", timeout=5)
        installed = client.installed()
    except OllamaFailure as exc:
        server_error = {"status": exc.status, "detail": str(exc)}
    manifest = {"run_id": destination.name, "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "models": list(models), "options": OPTIONS, "thinking": False, "schema": SCHEMA,
                "system_prompt": SYSTEM_PROMPT, "environment": env, "ollama_version": version,
                "installed_models": installed, "server_error": server_error,
                "repetitions": repetitions, "case_count": len(cases), "run_models": run_models,
                "memory_policy": memory_policy, "memory_events": [], "completed_models": [],
                "campaign_status": "running",
                "data_kind": bundle["kind"], "input_hashes": bundle["hashes"], "warnings": bundle["warnings"],
                "packages": {name: importlib.metadata.version(name) for name in ["pandas", "numpy", "matplotlib", "psutil", "jsonschema", "nbformat", "nbclient", "ipykernel"]},
                "method": "séquentiel ; déchargement vérifié avant/après chaque modèle ; sauvegarde immédiate des réponses et checkpoint par modèle ; un warmup exclu ; pas de fallback ou retry",
                "qualitative_scoring": "humain requis ; les alertes causales lexicales sont indicatives"}
    code_root = Path(__file__).parent
    manifest["code_hashes"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in code_root.glob("*.py")}
    _json_write(destination / "manifest.json", manifest)
    runs = []
    warmups = []
    with (destination / "responses.jsonl").open("x", encoding="utf-8") as journal:
        for model in models:
            model_dir = destination / "models" / model.replace(":", "_")
            model_dir.mkdir(parents=True, exist_ok=True)
            with (model_dir / "responses.jsonl").open("x", encoding="utf-8") as model_journal:
                skip = None
                detail = ""
                fatal = None
                cleanup_needed = False
                print(f"\nModèle {model} — {len(cases)*repetitions} cas prévus", flush=True)
                if not run_models:
                    skip = "execution_disabled"
                elif server_error:
                    skip, detail = server_error["status"], server_error["detail"]
                elif model not in installed:
                    skip = "model_absent"
                try:
                    if skip is None:
                        cleanup_needed = True
                        try:
                            event = release_ollama_memory(client)
                            manifest["memory_events"].append({"model": model, "phase": "before", **event})
                            free = event["ram_available_gib"]
                            if free < MIN_FREE_GIB[model]:
                                message = f"RAM libre : {free:.2f} Gio ; repère indicatif : {MIN_FREE_GIB[model]:.1f} Gio."
                                if memory_policy == "strict":
                                    raise OllamaFailure("insufficient_memory_preflight", message)
                                print(message + " Essai du modèle seul ; Ollama vérifie le chargement réel.", flush=True)
                            _json_write(destination / (model.replace(":", "_") + "_show.json"), client.call("show", {"model": model}))
                            started = time.perf_counter()
                            warm = client.call("chat", {"model": model, "messages": [{"role": "user", "content": 'Réponds uniquement par {"ready":true}'}],
                                                        "format": "json", "stream": False, "think": False,
                                                        "options": OPTIONS, "keep_alive": "2m"})
                            warmups.append({"model": model, "seconds": time.perf_counter()-started, "response": warm})
                        except OllamaFailure as exc:
                            skip, detail = "warmup_" + exc.status, str(exc)
                    for repetition in range(1, repetitions+1):
                        for case in cases:
                            payload = payload_for(model, case)
                            shared_payload = {k: v for k, v in payload.items() if k != "model"}
                            row = {"run_id": destination.name, "model": model, "case_id": case["case_id"],
                                   "data_kind": case["data_kind"], "category": case["category"], "repetition": repetition,
                                   "model_digest": installed.get(model, {}).get("digest"),
                                   "request_hash": hashlib.sha256(json.dumps(shared_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                                   "status": skip or fatal or "pending", "error": detail if skip else "", "latency_seconds": None,
                                   "raw_content": "", "api_response": None, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
                            if not skip and not fatal:
                                started = time.perf_counter()
                                try:
                                    response = client.call("chat", payload)
                                    row["latency_seconds"] = time.perf_counter()-started
                                    row["api_response"] = response
                                    content = response.get("message", {}).get("content", "")
                                    row["raw_content"] = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                                    row["status"] = "success" if content else "empty_response"
                                    if response.get("done") is not True:
                                        row["status"] = "incomplete_response"
                                    if response.get("done_reason") == "length":
                                        row["status"] = "truncated_response"
                                    if response.get("model") != model:
                                        row["status"] = "unexpected_model_identity"
                                    for key in ["total_duration", "load_duration", "prompt_eval_count", "eval_count", "eval_duration"]:
                                        row[key] = response.get(key)
                                except OllamaFailure as exc:
                                    row.update(status=exc.status, error=str(exc), latency_seconds=time.perf_counter()-started)
                                    if exc.status in {"timeout", "insufficient_memory", "server_unavailable"}:
                                        fatal = "remaining_cases_skipped_after_" + exc.status
                            line = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                            for stream in [journal, model_journal]:
                                stream.write(line)
                                stream.flush()
                                os.fsync(stream.fileno())
                            runs.append(row)
                            print(f"  {case['case_id']} ({repetition}/{repetitions}) : {row['status']} — enregistré", flush=True)
                    manifest["completed_models"].append(model)
                except BaseException:
                    manifest["campaign_status"] = "interrupted"
                    raise
                finally:
                    try:
                        if cleanup_needed:
                            try:
                                event = release_ollama_memory(client, target_model=model)
                                manifest["memory_events"].append({"model": model, "phase": "after", **event})
                            except OllamaFailure as exc:
                                manifest["memory_events"].append({"model": model, "phase": "after", "error": str(exc)})
                                server_error = {"status": "cleanup_failed", "detail": str(exc)}
                                manifest["server_error"] = server_error
                    finally:
                        _json_write(destination / "warmups.json", warmups)
                        save_checkpoint(cases, runs, manifest, destination, model=model)
                    print(f"Résultats sauvegardés : {model_dir}", flush=True)
    _json_write(destination / "warmups.json", warmups)
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["campaign_status"] = "completed"
    save_checkpoint(cases, runs, manifest, destination)
    return runs, manifest


def score_runs(cases, runs, destination):
    lookup = {c["case_id"]: c for c in cases}
    rows = []
    for run in runs:
        row = {k: run.get(k) for k in ["run_id", "model", "case_id", "data_kind", "category", "repetition", "status", "error", "latency_seconds", "request_hash"]}
        received = run["status"] in {"success", "truncated_response", "incomplete_response", "empty_response"}
        row.update(evaluate(lookup[run["case_id"]], run["raw_content"], success=received))
        if received and run["status"] != "success":
            row["objective_pass"] = False
            row["errors"].append("generation_status:" + run["status"])
        row["errors"] = json.dumps(row["errors"], ensure_ascii=False)
        rows.append(row)
    scores = pd.DataFrame(rows)
    scores.to_csv(Path(destination) / "scores_detailed.csv", index=False)
    # Les erreurs d'infrastructure ne deviennent pas des scores de qualité égaux à zéro.
    summaries = []
    for (model, kind), group in scores.groupby(["model", "data_kind"], sort=False):
        measured = group.loc[group.evaluation_applicable]
        successful = group.loc[group.status.eq("success")]
        summary = {"model": model, "data_kind": kind, "scheduled": len(group),
                   "responses_received": len(measured), "complete_responses": len(successful),
                   "availability_rate": len(successful)/len(group),
                   "objective_pass_rate_all_scheduled": float(group.objective_pass.sum()/len(group)) if len(measured) else None,
                   "mean_latency_seconds": successful.latency_seconds.mean() if len(successful) else None,
                   "p95_latency_seconds": successful.latency_seconds.quantile(.95) if len(successful) else None,
                   "quality_denominator": len(measured)}
        for metric in ["json_valid", "schema_valid", "objective_pass", "numeric_accuracy", "source_accuracy", "sample_size_accuracy", "scope_accuracy", "required_fact_recall", "decision_ok", "finding_ok", "limits_ok"]:
            summary[metric+"_mean"] = measured[metric].mean() if len(measured) else None
        for metric in ["unsupported_claims", "numeric_mismatches", "prose_numeric_flags", "causal_flags"]:
            summary[metric] = int(measured[metric].sum()) if len(measured) else None
        summaries.append(summary)
    comparison = pd.DataFrame(summaries)
    comparison.to_csv(Path(destination) / "model_comparison.csv", index=False)
    errors = scores.loc[scores.objective_pass.ne(True) | scores.causal_flags.gt(0)].copy()
    errors.to_csv(Path(destination) / "errors_and_hallucination_flags.csv", index=False)
    human = scores.loc[scores.evaluation_applicable, ["run_id", "model", "case_id", "data_kind", "repetition"]].copy()
    for col in ["reviewer", "insight_relevance_0_3", "recommendation_utility_0_3", "causal_caution_0_3", "limits_quality_0_3", "unsupported_causal_claim_yes_no", "notes"]:
        human[col] = ""
    human.to_csv(Path(destination) / "human_review.csv", index=False)
    return scores, comparison


def visualize(comparison, destination):
    import matplotlib.pyplot as plt
    paths = []
    for kind, table in comparison.groupby("data_kind", sort=False):
        fig, axes = plt.subplots(1, 3, figsize=(14, 4))
        for ax, field, title in zip(axes,
                ["availability_rate", "objective_pass_mean", "mean_latency_seconds"],
                ["Réponses complètes / cas planifiés", "Conformité objective / réponses reçues", "Latence moyenne (s)"]):
            values = pd.to_numeric(table[field], errors="coerce")
            valid = values.notna()
            if valid.any():
                ax.bar(table.loc[valid, "model"], values[valid])
            else:
                ax.text(.5, .5, "Aucune mesure disponible", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(title, fontsize=9)
            ax.tick_params(axis="x", rotation=35)
            if "rate" in field or "pass" in field:
                ax.set_ylim(0, 1.05)
        fig.suptitle("BIFlow — " + ("données réelles locales" if kind == "real" else "SIMULATIONS UNIQUEMENT"))
        fig.tight_layout()
        path = Path(destination) / ("comparison_" + kind + ".png")
        fig.savefig(path, dpi=140, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths


def write_report(comparison, scores, manifest, destination):
    lines = ["# BIFlow — benchmark Ollama local", "", "Exécution UTC : " + manifest["started_at_utc"], "",
             "Les mesures de qualité portent sur les réponses reçues. Une infrastructure indisponible n'a aucun score de qualité.",
             "Les cas réels et synthétiques sont présentés séparément. Les alertes causales lexicales exigent une relecture humaine.", "",
             "```text", comparison.to_string(index=False), "```", "", "## Erreurs observées", "",
             scores.groupby(["model", "status"]).size().to_string(), "", "## Choix du modèle et limites", ""]
    real = comparison.loc[comparison.data_kind.eq("real")]
    complete = (len(real) == 3 and real.availability_rate.eq(1).all() and manifest["case_count"] >= 20
                and manifest.get("campaign_status") == "completed"
                and len(scores) == len(MODELS)*manifest["case_count"]*manifest["repetitions"])
    if not complete:
        lines.append("Aucun modèle sélectionné : comparaison réelle complète des trois modèles indisponible. Aucun score n'est inventé.")
    else:
        ordered = real.sort_values(["objective_pass_rate_all_scheduled", "mean_latency_seconds"], ascending=[False, True])
        candidate = ordered.iloc[0]
        if candidate.objective_pass_rate_all_scheduled <= 0:
            lines.append("Aucun candidat : aucun modèle n'a réussi les vérifications objectives.")
        else:
            lines.append(f"Candidat provisoire selon conformité objective puis latence : {candidate.model}. "
                         "La sélection finale pour BIFlow exige la grille humaine complétée ; aucune note qualitative automatique n'est attribuée.")
    lines += ["", "Le téléchargement n'est pas une mesure d'inférence. Warmup exclu ; cache, offload CPU/GPU et charge machine influencent les latences.",
              "Une répétition ne permet pas d'estimer la stabilité. Température nulle et seed ne garantissent pas une reproduction bit à bit entre matériels et versions.",
              "Les contrôles numériques vérifient les valeurs structurées, leurs unités, groupes, sources et effectifs ; les chiffres en prose sont signalés.",
              "La grille humaine reste nécessaire pour l'utilité des actions, les causes implicites et les nombres écrits en lettres.",
              "Les références sont des contrats déterministes. Le champ finding normalise l'expression et ne mesure pas à lui seul la qualité d'analyse.", "", "## Données et environnement", ""]
    lines.extend("- " + w for w in manifest["warnings"])
    lines.append("- Voir manifest.json pour versions, paramètres, empreintes, matériel et disponibilité.")
    path = Path(destination) / "benchmark_report.md"
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return path
