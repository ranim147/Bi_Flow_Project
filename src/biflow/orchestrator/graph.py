"""Graphe LangGraph du pipeline BIFlow.

    START -> data_profiler -> quality_etl -> kpi_semantic -> analyste_bi
          -> dashboard_generator -> auditor_xai -> finalize -> END

- Après chaque nœud, le routeur lit `state.next_step` :
  étape suivante, ou `finalize` si l'étape a échoué (run FAILED, rien n'est publié).
- Après l'Auditeur : si un finding bloquant existe et qu'il reste un tour de rétroaction,
  retour vers l'étape fautive (qui est réexécutée avec toutes les étapes en aval) ;
  sinon run UNRELIABLE.
- `finalize` fixe le statut final, décide de la publication et trace le run (`__run__`).
  Le state complet est écrit dans `<workspace>/run.json` (journal d'audit du run) ; si le
  run est publié, `<workspace>/../published.json` pointe vers lui (lu par le dashboard).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from biflow.agents import default_agents
from biflow.agents.base import BaseAgent, RunContext
from biflow.common.config import PROCESSED_DIR
from biflow.orchestrator.state import (
    STEP_ORDER,
    AuditFinding,
    DatasetRef,
    PipelineState,
    RunStatus,
)
from biflow.orchestrator.tracing import TraceRecord

log = logging.getLogger(__name__)

FINALIZE = "finalize"
AUDITOR = "auditor_xai"


def _audit_routing(state: PipelineState, audit_output: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """Décide de la suite après l'Auditeur (boucle de rétroaction)."""
    findings = [AuditFinding.model_validate(f) for f in audit_output.get("findings", [])]
    blocking = [f for f in findings if f.severity == "error"]
    updates: dict[str, Any] = {"findings": [*state.findings, *findings]}  # historique complet
    if not blocking:
        return {**updates, "next_step": FINALIZE}
    if state.feedback_rounds >= ctx.max_feedback_rounds:
        log.warning("L'Auditeur signale encore %d problème(s) après correction -> UNRELIABLE",
                    len(blocking))
        return {**updates, "status": RunStatus.UNRELIABLE, "next_step": FINALIZE}

    target = min((f.target_step for f in blocking), key=STEP_ORDER.index)
    corrections = {**state.corrections, **{f.target_step: f.message for f in blocking}}
    log.warning("Auditeur : renvoi vers '%s' (tour %d/%d)", target,
                state.feedback_rounds + 1, ctx.max_feedback_rounds)
    return {
        **updates,
        "corrections": corrections,
        "feedback_rounds": state.feedback_rounds + 1,
        "next_step": target,
    }


def _make_agent_node(agent: BaseAgent, ctx: RunContext) -> Callable[[PipelineState], dict]:
    idx = STEP_ORDER.index(agent.name)

    def node(state: PipelineState) -> dict[str, Any]:
        updates = agent.execute(state, ctx)
        if updates.get("status") == RunStatus.FAILED:
            updates["next_step"] = FINALIZE
        elif agent.name == AUDITOR:
            updates.update(_audit_routing(state, updates["outputs"][AUDITOR], ctx))
        else:
            updates["next_step"] = STEP_ORDER[idx + 1]
        return updates

    return node


def _make_finalize_node(ctx: RunContext, started_at: datetime) -> Callable[[PipelineState], dict]:
    def finalize(state: PipelineState) -> dict[str, Any]:
        status = RunStatus.SUCCESS if state.status == RunStatus.RUNNING else state.status
        published = status == RunStatus.SUCCESS
        ctx.trace_logger.log(TraceRecord(
            run_id=state.run_id, step="__run__", status=status.value,
            started_at=started_at, ended_at=datetime.now(UTC),
            message=f"run {status.value}, publié={published}",
            details={
                "dataset": state.dataset.model_dump(),
                "steps": [h.model_dump(mode="json") for h in state.history],
                "feedback_rounds": state.feedback_rounds,
                "findings": [f.model_dump() for f in state.findings],
                "error_count": len(state.errors),
                "stub_steps": [s for s, o in state.outputs.items() if o.get("is_stub")],
            },
        ))
        final = state.model_copy(update={"status": status, "published": published, "next_step": None})
        if state.workspace:
            write_run_files(final)
        return {"status": status, "published": published, "next_step": None}

    return finalize


PUBLISHED_POINTER = "published.json"


def write_run_files(state: PipelineState) -> None:
    workspace = Path(state.require_workspace())
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "run.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
    if state.published:
        dash = state.outputs.get("dashboard_generator", {})
        pointer = {"run_id": state.run_id, "workspace": str(workspace), "run_file": str(workspace / "run.json"),
                   "spec_path": dash.get("spec_path"), "spec_hash": dash.get("spec_hash"),
                   "published_at": datetime.now(UTC).isoformat()}
        (workspace.parent / PUBLISHED_POINTER).write_text(json.dumps(pointer, indent=2), encoding="utf-8")


def build_graph(agents: dict[str, BaseAgent], ctx: RunContext, started_at: datetime | None = None):
    missing = set(STEP_ORDER) - set(agents)
    if missing:
        raise ValueError(f"Agents manquants : {sorted(missing)}")

    graph = StateGraph(PipelineState)
    for name in STEP_ORDER:
        graph.add_node(name, _make_agent_node(agents[name], ctx))
    graph.add_node(FINALIZE, _make_finalize_node(ctx, started_at or datetime.now(UTC)))

    routes = {name: name for name in STEP_ORDER} | {FINALIZE: FINALIZE}
    graph.add_edge(START, STEP_ORDER[0])
    for name in STEP_ORDER:
        graph.add_conditional_edges(name, lambda s: s.next_step, routes)
    graph.add_edge(FINALIZE, END)
    return graph.compile()


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def run_pipeline(
    dataset: DatasetRef | None = None,
    agents: dict[str, BaseAgent] | None = None,
    ctx: RunContext | None = None,
    run_id: str | None = None,
    questions: list[str] | None = None,
    output_dir: str | Path | None = None,
) -> PipelineState:
    """Point d'entrée programmatique : exécute tout le pipeline et retourne le state final.

    Les artefacts du run vont dans `<output_dir>/<run_id>/` (défaut : data/processed).
    """
    ctx = ctx or RunContext()
    started_at = datetime.now(UTC)
    app = build_graph(agents or default_agents(), ctx, started_at)
    run_id = run_id or new_run_id()
    initial = PipelineState(run_id=run_id, dataset=dataset or DatasetRef(), questions=questions or [],
                            workspace=str(Path(output_dir or PROCESSED_DIR) / run_id))
    log.info("Démarrage du run %s", initial.run_id)
    result = app.invoke(initial, config={"recursion_limit": 100})
    return PipelineState.model_validate(result)
