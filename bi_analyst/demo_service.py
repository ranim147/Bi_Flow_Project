"""Local demo boundary: live analysis, honest replay and an explicit injected test."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from jsonschema import Draft202012Validator

from .models import AnalysisContext, BIAnalysisReport
from .orchestrator import analyze


@dataclass
class DemoRun:
    report: BIAnalysisReport
    events: list[dict[str, Any]]
    analysis: dict[str, Any] | None
    mode: str
    origin: str
    path: Path
    verification: dict[str, Any]
    display_metadata: dict[str, Any] = field(default_factory=dict)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _events(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _inside(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError("Le chemin du fichier enregistré sort du dossier autorisé.")
    return target


def _contract(report: BIAnalysisReport, root: Path) -> dict[str, Any]:
    version = "v2" if report.contract_version.endswith("v2") else "v1"
    Draft202012Validator(_read_json(root / "schemas" / f"BIAnalysisReport.{version}.schema.json")).validate(report.model_dump(mode="json"))
    from .consumers import check_consumers
    return {"schema_valid": True, "consumers": check_consumers(report)}


def load_scenarios(root: Path) -> list[dict[str, Any]]:
    return _read_json(Path(root) / "demo/questions.json")


def recordings_root(root: Path) -> Path:
    local = Path(root) / "demo/recordings"
    return local if local.exists() else Path(root) / "archive/demo_recordings"


def latest_cases(root: Path) -> list[dict[str, Any]]:
    path = Path(root) / "outputs/pipeline_delivery/real_evaluation/delivery_manifest.json"
    return _read_json(path)["cases"] if path.exists() else []


def load_latest_evaluation(case_id: str, root: Path) -> DemoRun:
    root = Path(root).resolve()
    base = root / "outputs/pipeline_delivery/real_evaluation"
    manifest = _read_json(base / "delivery_manifest.json")
    matches = [case for case in manifest["cases"] if case["id"] == case_id]
    if len(matches) != 1:
        raise ValueError("Résultat récent inconnu ou ambigu.")
    entry = matches[0]
    folder = _inside(base, entry["audit_dir"])
    for relative, expected in entry["sha256"].items():
        if hashlib.sha256(_inside(folder, relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Empreinte du résultat récent incorrecte : " + relative)
    report = BIAnalysisReport.model_validate(_read_json(folder / "report.json"))
    events = _events(folder / "audit.jsonl")
    request = [e for e in events if e["event"] == "request_received"]
    if report.execution_id != entry["execution_id"] or report.question != entry["question"] or len(request) != 1 or request[0]["question"] != report.question:
        raise ValueError("Identité du résultat récent incohérente.")
    return DemoRun(report, events, _read_json(folder / "analysis.json") if (folder / "analysis.json").exists() else None,
                   "replay", "Dernière évaluation réelle Granite sur les données préparées", folder,
                   dict(integrity_verified=True, **_contract(report, root)),
                   dict(source_mode="live", llm_called_now=False, injected=False,
                        dataset_version=manifest["dataset_version"], original_elapsed_ms=report.timing_ms.get("total")))


def load_recording(scenario_id: str, root: Path) -> DemoRun:
    """No model/client construction and no analytical computation during replay."""
    root = Path(root).resolve()
    started = time.perf_counter()
    base = recordings_root(root)
    manifest = _read_json(base / "manifest.json")
    matches = [item for item in manifest["recordings"] if item["scenario_id"] == scenario_id]
    if len(matches) != 1:
        raise ValueError("Scénario enregistré inconnu ou ambigu.")
    entry = matches[0]
    for relative, expected in entry["sha256"].items():
        path = _inside(base, relative)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Empreinte de restitution incorrecte : " + relative)
    folder = _inside(base, entry["directory"])
    report = BIAnalysisReport.model_validate(_read_json(folder / "report.json"))
    events = _events(folder / "audit.jsonl")
    metadata = _read_json(folder / "metadata.json")
    if report.execution_id != entry["execution_id"] or report.execution_id != metadata["execution_id"]:
        raise ValueError("Identifiant d'exécution de restitution incohérent.")
    injected = any(event["event"] == "controlled_fault_injection" for event in events)
    if injected != bool(metadata.get("injected")) or injected != (metadata.get("source_mode") == "controlled_test"):
        raise ValueError("Le type de restitution contredit le journal d'injection.")
    if injected and metadata.get("excluded_from_model_metrics") is not True:
        raise ValueError("Une injection doit rester exclue des métriques Granite.")
    verification = {"integrity_verified": True, **_contract(report, root),
                    "source_verification": _read_json(folder / "verification.json")}
    metadata.update(recorded_at=report.generated_at, original_elapsed_ms=report.timing_ms.get("total"),
                    replay_elapsed_ms=(time.perf_counter() - started) * 1000,
                    source_mode=metadata["source_mode"], llm_called_now=False)
    return DemoRun(report, events, _read_json(folder / "analysis.json") if (folder / "analysis.json").is_file() else None,
                   "replay", metadata["origin"], folder, verification, metadata)


def available_datasets(root: Path) -> list[str]:
    return sorted(path.parent.parent.name for path in (Path(root) / "data/versions").glob("*/processed/manifest.json")
                  if not (path.parent.parent / "invalidated.json").exists())


def active_dataset(root: Path) -> str | None:
    pointer = Path(root) / "data/active_dataset.json"
    if not pointer.exists():
        return None
    value = _read_json(pointer)
    from bi_pipeline.loader import version_path
    manifest = version_path(root, value["dataset_version"]) / "processed/manifest.json"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != value["processed_manifest_sha256"]:
        raise ValueError("La version active ne correspond plus au manifeste validé.")
    return value["dataset_version"]


def run_live(question: str, root: Path, *, audit_root: Path | None = None, client=None, dataset_version: str | None = None) -> DemoRun:
    root = Path(root).resolve()
    selected = active_dataset(root) if dataset_version is None else None if dataset_version == "legacy" else dataset_version
    destination = audit_root or root / ("outputs/pipeline_delivery/live_runs" if selected else "outputs/demo_live")
    report = analyze(question, AnalysisContext(project_root=root, dataset_version=selected), audit_root=destination, client=client)
    folder = destination / report.execution_id
    events = _events(folder / "audit.jsonl")
    requests = [event for event in events if event["event"] == "request_received"]
    if report.question != question or folder.name != report.execution_id or len(requests) != 1 or requests[0]["question"] != question or requests[0]["execution_id"] != report.execution_id:
        raise ValueError("La question, le rapport et l'identifiant d'exécution en direct ne correspondent pas.")
    verification = _contract(report, root)
    return DemoRun(report, events,
                   _read_json(folder / "analysis.json") if (folder / "analysis.json").is_file() else None,
                   "live", "Analyse locale demandée à Granite via Ollama", folder, verification,
                   dict(source_mode="live", dataset_version=selected or "legacy", submitted_question=question, execution_id=report.execution_id, recorded_at=report.generated_at,
                        original_elapsed_ms=report.timing_ms.get("total"),
                        llm_called_now=any(event["event"] == "llm_request" for event in _events(folder / "audit.jsonl"))))


def run_rejection_demo(root: Path) -> DemoRun:
    """Fault injection derived from a genuine saved response, NOT model inference."""
    root = Path(root).resolve()
    source = load_recording("compare_boroughs", root)
    responses = [event for event in source.events if event["event"] == "llm_response"]
    if not responses:
        raise ValueError("La réponse réelle source de l'injection est absente.")
    bad = json.loads(responses[0]["raw_response"])
    bad.update(evidence_ids=["delay.borough.*"],
               insight="Les délais augmentent de 999 jours à cause du manque de personnel.",
               recommendation="Augmenter les effectifs et imposer un SLA.")
    raw = json.dumps(bad, ensure_ascii=False)

    class InjectedResponse:
        def installed(self):
            return {"granite3.3:2b": {"digest": "CONTROLLED-INJECTION-NO-MODEL-CALL"}}

        def call(self, endpoint, payload=None, timeout=None):
            if endpoint == "version":
                return {"version": "controlled-test/no-ollama-call"}
            if endpoint != "chat":
                raise ValueError("Unexpected controlled-test endpoint.")
            return {"model": "granite3.3:2b", "done": True, "done_reason": "stop", "message": {"content": raw}}

    report = analyze(source.report.question, AnalysisContext(project_root=root), client=InjectedResponse(),
                     audit_root=root / "outputs/demo_controlled_tests")
    folder = root / "outputs/demo_controlled_tests" / report.execution_id
    marker = dict(event="controlled_fault_injection", timestamp=datetime.now(timezone.utc).isoformat(),
                  source_execution_id=source.report.execution_id, source_model="granite3.3:2b",
                  source_response_sha256=hashlib.sha256(responses[0]["raw_response"].encode()).hexdigest(),
                  injected_response_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                  actual_model_inference=False, excluded_from_model_metrics=True,
                  note="Réponse réelle de référence altérée volontairement; ce contenu incorrect n'est pas une sortie authentique de Granite.")
    with (folder / "audit.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(marker, ensure_ascii=False) + "\n")
    metadata = dict(source_mode="controlled_test", injected=True, llm_called_now=False,
                    execution_id=report.execution_id, origin=marker["note"], source_execution_id=source.report.execution_id,
                    excluded_from_model_metrics=True, recorded_at=report.generated_at,
                    original_elapsed_ms=report.timing_ms.get("total"))
    (folder / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if report.status != "fallback" or report.validation.status != "rejected":
        raise ValueError("Le test d'injection n'a pas été rejeté comme attendu.")
    return DemoRun(report, _events(folder / "audit.jsonl"), None, "controlled_test", marker["note"], folder,
                   {**_contract(report, root), "injected_assertions_blocked": True}, metadata)
