"""Exécution des scénarios de démonstration depuis l'API, avec suivi en direct.

Un scénario = le VRAI pipeline (`run_pipeline`, mêmes agents, même orchestrateur LangGraph)
avec, au besoin, les mêmes options de simulation que la CLI (`--simulate-failure`,
`--simulate-audit-flag`, `--persistent`). Rien n'est rejoué ni précalculé.

Suivi en direct : chaque agent est instrumenté (événement `started` avant chaque essai) et
la trace de l'orchestrateur (`TraceRecord`, une ligne par essai) est captée au passage.
Le front interroge `/api/runs/{run_id}/live` pour animer le pipeline.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from biflow.agents import default_agents
from biflow.agents.auditor_xai import AuditorXAIAgent
from biflow.agents.base import BaseAgent, RunContext
from biflow.agents.testing import FailingAgent
from biflow.common.config import PROCESSED_DIR, get_settings
from biflow.orchestrator.graph import new_run_id, run_pipeline
from biflow.orchestrator.state import STEP_ORDER, DatasetRef
from biflow.orchestrator.tracing import MemoryTraceLogger, TraceRecord

log = logging.getLogger(__name__)

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "nominal",
        "title": "Run nominal",
        "summary": "Les 6 agents s'enchaînent, l'Auditeur valide chaque chiffre, le run est publié.",
        "expected": "success",
        "step_choices": [],
    },
    {
        "id": "panne",
        "title": "Panne d'un agent",
        "summary": "L'agent choisi échoue à chaque essai : l'orchestrateur le relance, puis arrête le run. "
                   "Rien n'est publié.",
        "expected": "failed",
        "step_choices": STEP_ORDER,
        "default_step": "quality_etl",
    },
    {
        "id": "correction",
        "title": "Écart corrigé par l'Auditeur",
        "summary": "L'Auditeur signale un écart sur l'étape choisie : le run repart de cette étape, "
                   "le second audit passe, le run est publié.",
        "expected": "success",
        "step_choices": STEP_ORDER[:-1],
        "default_step": "kpi_semantic",
    },
    {
        "id": "non_fiable",
        "title": "Écart persistant",
        "summary": "L'écart est toujours là après la correction : le run est déclaré non fiable "
                   "et le tableau de bord garde le dernier run validé.",
        "expected": "unreliable",
        "step_choices": STEP_ORDER[:-1],
        "default_step": "kpi_semantic",
    },
]
SCENARIO_IDS = {s["id"] for s in SCENARIOS}

DEFAULT_QUESTIONS = [
    "Combien de demandes de bruit dans le Bronx ?",
    "Quel borough a le délai médian de résolution le plus long ?",
    "Pourquoi les délais sont-ils plus longs à Staten Island ?",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class LiveRun:
    run_id: str
    scenario: str
    step: str | None
    dataset: str
    questions: list[str]
    pace_s: float
    started_at: str = field(default_factory=_now)
    ended_at: str | None = None
    status: str = "running"
    published: bool = False
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _round: int = 0

    def add(self, **event: Any) -> None:
        with self._lock:
            self.events.append({"seq": len(self.events), "at": _now(), **event})

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id, "scenario": self.scenario, "step": self.step,
                "dataset": self.dataset, "questions": self.questions, "pace_s": self.pace_s,
                "started_at": self.started_at, "ended_at": self.ended_at,
                "status": self.status, "published": self.published, "error": self.error,
                "events": self.events[since:], "next_seq": len(self.events),
            }


class LiveTraceLogger:
    """Capte chaque `TraceRecord` de l'orchestrateur et le transforme en événement."""

    def __init__(self, live: LiveRun) -> None:
        self.live = live
        self.memory = MemoryTraceLogger()

    def log(self, record: TraceRecord) -> None:
        self.memory.log(record)
        details = record.details or {}
        if record.step == "__run__":
            self.live.add(kind="run", status=record.status, message=record.message,
                          feedback_rounds=details.get("feedback_rounds", 0),
                          findings=details.get("findings", []))
            return
        self.live.add(
            kind=record.status,  # success | retry | failed
            step=record.step, attempt=record.attempt,
            feedback_round=details.get("feedback_round", self.live._round),
            message=record.message, input_rows=record.input_rows, output_rows=record.output_rows,
            validation=details.get("validation", []), error_type=details.get("error_type"),
            correction=details.get("correction_applied"),
            duration_s=round((record.ended_at - record.started_at).total_seconds(), 3)
            if record.ended_at else None,
        )


def _instrument(agent: BaseAgent, live: LiveRun) -> BaseAgent:
    """Ajoute un événement `started` avant chaque essai de l'agent (et le ralenti de démo)."""
    original = agent.run
    attempts: dict[int, int] = {}

    def run(state):  # même signature que BaseAgent.run
        live._round = state.feedback_rounds
        attempts[state.feedback_rounds] = attempts.get(state.feedback_rounds, 0) + 1
        live.add(kind="started", step=agent.name, attempt=attempts[state.feedback_rounds],
                 feedback_round=state.feedback_rounds,
                 correction=state.corrections.get(agent.name))
        if live.pace_s:
            time.sleep(live.pace_s)
        output = original(state)
        if agent.name == "auditor_xai":  # verdict de chaque passage de l'Auditeur
            checks = getattr(output, "checks", [])
            live.add(kind="audit", step=agent.name, feedback_round=state.feedback_rounds,
                     approved=output.approved, passed=sum(c.passed for c in checks), total=len(checks),
                     findings=[f.model_dump() for f in output.findings])
        return output

    agent.run = run  # type: ignore[method-assign]
    return agent


def build_agents(scenario: str, step: str | None) -> dict[str, BaseAgent]:
    agents = default_agents()
    if scenario == "panne":
        agents[step] = FailingAgent(agents[step])
    elif scenario in ("correction", "non_fiable"):
        agents["auditor_xai"] = AuditorXAIAgent(step, persistent=scenario == "non_fiable")
    return agents


class RunManager:
    """Un seul run à la fois (démo) ; garde les runs lancés depuis le démarrage du serveur."""

    def __init__(self, output_dir: Path = PROCESSED_DIR) -> None:
        self.output_dir = output_dir
        self.runs: dict[str, LiveRun] = {}
        self._busy = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def start(self, scenario: str, step: str | None, dataset: str, questions: list[str],
              pace_s: float) -> LiveRun:
        if scenario not in SCENARIO_IDS:
            raise ValueError(f"scénario inconnu : {scenario}")
        spec = next(s for s in SCENARIOS if s["id"] == scenario)
        if spec["step_choices"]:
            step = step or spec["default_step"]
            if step not in spec["step_choices"]:
                raise ValueError(f"étape {step!r} impossible pour le scénario {scenario}")
        else:
            step = None
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("un run est déjà en cours")
        try:
            live = LiveRun(run_id=new_run_id(), scenario=scenario, step=step, dataset=dataset,
                           questions=[q for q in questions if q.strip()], pace_s=max(0.0, min(pace_s, 3.0)))
            threading.Thread(target=self._execute, args=(live,), daemon=True, name=live.run_id).start()
        except BaseException:
            self._busy.release()  # sinon le serveur resterait « occupé » pour toujours
            raise
        self.runs[live.run_id] = live
        return live

    def _execute(self, live: LiveRun) -> None:
        try:
            settings = get_settings()
            agents = {name: _instrument(a, live) for name, a in build_agents(live.scenario, live.step).items()}
            ctx = RunContext(trace_logger=LiveTraceLogger(live), max_retries=settings.max_retries,
                             retry_delay_seconds=settings.retry_delay_seconds,
                             max_feedback_rounds=settings.max_feedback_rounds)
            state = run_pipeline(DatasetRef(file_path=live.dataset), agents=agents, ctx=ctx,
                                 run_id=live.run_id, questions=live.questions, output_dir=self.output_dir)
            live.status, live.published = state.status.value, state.published
        except Exception as exc:  # erreur remontée au front
            log.exception("run %s interrompu", live.run_id)
            live.status, live.error = "failed", f"{type(exc).__name__} : {exc}"
        finally:
            live.ended_at = _now()
            self._busy.release()
