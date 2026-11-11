"""API HTTP de BIFlow, utilisée par le front React (`frontend/`).

    python -m biflow.api            # http://localhost:8000 (API + front compilé)

Routes :
    GET  /api/meta                       scénarios, extraits disponibles, LLM, questions d'exemple
    POST /api/runs                       lance un scénario (le vrai pipeline)
    GET  /api/runs                       runs présents dans data/processed (statut, publication)
    GET  /api/runs/{run_id}/live         événements en direct d'un run lancé depuis l'API
    GET  /api/runs/{run_id}              run.json complet (sorties des 6 agents)
    GET  /api/runs/{run_id}/quarantine   lignes écartées par Qualité/ETL, avec leur motif
    GET  /api/published                  dernier run PUBLIÉ + spec du dashboard (empreinte vérifiée)
    POST /api/ask                        question sur l'instantané audité du run publié

Mêmes garde-fous que le dashboard Streamlit (premier prototype) : seul un run publié est affiché, l'empreinte de
la spec est vérifiée, les questions passent par le périmètre, le contrôle des données
personnelles, le bac à sable SQL et le seuil anti-identification.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from biflow.agents.kpi_semantic import ask
from biflow.api.live import DEFAULT_QUESTIONS, SCENARIOS, RunManager
from biflow.common.config import PROCESSED_DIR, PROJECT_ROOT, RAW_DIR, get_settings
from biflow.common.reference import load_reference
from biflow.common.snapshot import file_hash
from biflow.llm.client import get_llm, llm_label
from biflow.orchestrator.state import STEP_ORDER, DatasetRef

FRONT_DIST = PROJECT_ROOT / "frontend" / "dist"
PREFERRED_DATASET = "nyc311_2023-01_2026-09_echantillon.parquet"
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
EXAMPLE_QUESTIONS = [
    "Combien de demandes de bruit dans le Bronx ?",
    "Quel borough a le délai médian de résolution le plus long ?",
    "Quel est le taux de respect du SLA à Brooklyn ?",
    "Quelle agence traite le plus de demandes ?",
    "Combien d'agents faut-il recruter ?",
    "Pourquoi les délais sont-ils plus longs à Staten Island ?",
]

log = logging.getLogger(__name__)
app = FastAPI(title="BIFlow API", version="0.1.0")
manager = RunManager(PROCESSED_DIR)


# ---------------------------------------------------------------------------- helpers
def _datasets() -> list[dict[str, Any]]:
    out = []
    for f in sorted(list(RAW_DIR.glob("*.parquet")) + list(RAW_DIR.glob("*.csv"))):
        rows = pq.ParquetFile(f).metadata.num_rows if f.suffix == ".parquet" else None
        meta = f.with_name(f.name + ".meta.json")
        notes = json.loads(meta.read_text(encoding="utf-8")).get("notes", []) if meta.exists() else []
        out.append({"path": str(f.relative_to(PROJECT_ROOT)).replace("\\", "/"), "name": f.name,
                    "rows": rows, "notes": notes, "preferred": f.name == PREFERRED_DATASET})
    return out


def _run_dir(run_id: str) -> Path:
    if not RUN_ID.match(run_id):
        raise HTTPException(400, "identifiant de run invalide")
    return PROCESSED_DIR / run_id


@lru_cache(maxsize=32)
def _load_run(path: str, mtime: float) -> dict[str, Any]:  # mtime : invalide le cache si le fichier change
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_run(run_id: str) -> dict[str, Any]:
    f = _run_dir(run_id) / "run.json"
    if not f.exists():
        raise HTTPException(404, f"run {run_id} introuvable (pas encore terminé ?)")
    return _load_run(str(f), f.stat().st_mtime)


def _published_pointer() -> dict[str, Any] | None:
    f = PROCESSED_DIR / "published.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _run_summary(run: dict[str, Any], published_id: str | None) -> dict[str, Any]:
    history = run.get("history", [])
    audit = run.get("outputs", {}).get("auditor_xai") or {}
    checks = audit.get("checks", [])
    return {
        "run_id": run["run_id"], "status": run["status"], "published": run.get("published", False),
        "is_current": run["run_id"] == published_id,
        "dataset": run.get("dataset", {}).get("file_path") or run.get("dataset", {}).get("table"),
        "questions": len(run.get("questions", [])),
        "started_at": history[0]["started_at"] if history else None,
        "ended_at": history[-1]["ended_at"] if history else None,
        "feedback_rounds": run.get("feedback_rounds", 0),
        "retries": sum(h["attempts"] - 1 for h in history),
        "steps_done": len({h["step"] for h in history if h["status"] == "success"}),
        "audit": f"{sum(c['passed'] for c in checks)}/{len(checks)}" if checks else None,
    }


# ---------------------------------------------------------------------------- routes
@app.get("/api/meta")
def meta() -> dict[str, Any]:
    settings = get_settings()
    return {
        "steps": STEP_ORDER,
        "scenarios": SCENARIOS,
        "datasets": _datasets(),
        "default_questions": DEFAULT_QUESTIONS,
        "example_questions": EXAMPLE_QUESTIONS,
        "llm": llm_label(get_llm()),
        "min_group_size": settings.min_group_size,
        "max_retries": settings.max_retries,
        "max_feedback_rounds": settings.max_feedback_rounds,
        "busy": manager.busy,
    }


class RunRequest(BaseModel):
    scenario: str = "nominal"
    step: str | None = None
    dataset: str | None = None
    questions: list[str] = Field(default_factory=list)
    pace_s: float = 0.6


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict[str, Any]:
    datasets = _datasets()
    if not datasets:
        raise HTTPException(409, f"aucun extrait dans {RAW_DIR} : lancer l'ingestion d'abord")
    known = {d["path"] for d in datasets}
    dataset = req.dataset or next((d["path"] for d in datasets if d["preferred"]), datasets[-1]["path"])
    if dataset not in known:
        raise HTTPException(400, f"extrait inconnu : {dataset}")
    try:
        # chemin absolu : le run ne dépend pas du dossier depuis lequel l'API a été lancée
        live = manager.start(req.scenario, req.step, (PROJECT_ROOT / dataset).as_posix(), req.questions,
                             req.pace_s)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return live.snapshot()


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    pointer = _published_pointer()
    published_id = pointer["run_id"] if pointer else None
    runs = []
    for d in sorted(PROCESSED_DIR.iterdir(), reverse=True) if PROCESSED_DIR.exists() else []:
        if RUN_ID.match(d.name) and (d / "run.json").exists():
            try:
                runs.append(_run_summary(load_run(d.name), published_id))
            except (ValueError, KeyError, IndexError):  # run.json tronqué ou d'un ancien format : ignoré
                log.warning("run %s illisible, ignoré dans l'historique", d.name)
    return runs


@app.get("/api/runs/{run_id}/live")
def live_run(run_id: str, since: int = 0) -> dict[str, Any]:
    live = manager.runs.get(run_id)
    if live is None:
        raise HTTPException(404, "run non suivi en direct (lancé hors de l'API ou avant le redémarrage)")
    return live.snapshot(since)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return load_run(run_id)


@app.get("/api/runs/{run_id}/quarantine")
def quarantine(run_id: str, limit: int = 50) -> dict[str, Any]:
    f = _run_dir(run_id) / "quarantine.parquet"
    if not f.exists():
        return {"total": 0, "by_rule": {}, "rows": []}
    df = pd.read_parquet(f)
    return {
        "total": len(df),
        "by_rule": {str(k): int(v) for k, v in df["motif"].value_counts().items()},
        "rows": json.loads(df.head(max(1, min(limit, 500))).to_json(orient="records", force_ascii=False)),
    }


@app.get("/api/published")
def published() -> dict[str, Any]:
    pointer = _published_pointer()
    if pointer is None:
        raise HTTPException(404, "aucun run publié : lancer le scénario nominal")
    if not Path(pointer["spec_path"]).exists():
        raise HTTPException(409, "la spec du run publié est introuvable : relancer le scénario nominal")
    if file_hash(pointer["spec_path"]) != pointer["spec_hash"]:
        raise HTTPException(409, "la spec du dashboard a été modifiée après l'audit : affichage refusé")
    spec = json.loads(Path(pointer["spec_path"]).read_text(encoding="utf-8"))
    return {"pointer": pointer, "spec": spec, "run": load_run(pointer["run_id"])}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.post("/api/ask")
def ask_question(req: AskRequest) -> dict[str, Any]:
    pointer = _published_pointer()
    if pointer is None:
        raise HTTPException(404, "aucun run publié : lancer le scénario nominal")
    run = load_run(pointer["run_id"])
    etl = run["outputs"]["quality_etl"]
    llm = get_llm()
    try:
        answer, queries = ask(req.question, etl["snapshot_path"], etl["snapshot_hash"],
                              load_reference(DatasetRef(**run["dataset"])), llm=llm)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"answer": answer.model_dump(mode="json"), "queries": [q.model_dump(mode="json") for q in queries],
            "llm": llm_label(llm), "run_id": run["run_id"], "snapshot_hash": etl["snapshot_hash"]}


# ---------------------------------------------------------------------------- front compilé
if FRONT_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONT_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def front(path: str) -> FileResponse:
        if path.startswith("api/"):  # une route d'API inconnue n'est pas une page du front
            raise HTTPException(404, "route d'API inconnue")
        target = FRONT_DIST / path
        if path and target.is_file() and FRONT_DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(FRONT_DIST / "index.html")
