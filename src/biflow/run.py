"""Point d'entrée CLI : exécute tout le pipeline et affiche un résumé.

    python -m biflow.run
    python -m biflow.run --start 2026-01-01 --end 2026-03-31
    python -m biflow.run --no-db                               # trace en mémoire
    python -m biflow.run --simulate-failure quality_etl        # démo retry -> FAILED
    python -m biflow.run --simulate-audit-flag kpi_semantic    # démo boucle Auditeur
    python -m biflow.run --simulate-audit-flag kpi_semantic --persistent   # -> UNRELIABLE

Code de sortie : 0 = succès, 1 = échec, 2 = non fiable.
"""

from __future__ import annotations

import argparse
import logging

from biflow.agents import default_agents
from biflow.agents.auditor_xai import AuditorXAIAgent
from biflow.agents.base import RunContext
from biflow.agents.testing import FailingAgent
from biflow.common.config import get_settings
from biflow.common.logging import setup_logging
from biflow.db.engine import can_connect, get_engine
from biflow.db.init_db import init_db
from biflow.orchestrator.graph import run_pipeline
from biflow.orchestrator.report import format_summary
from biflow.orchestrator.state import STEP_ORDER, DatasetRef, RunStatus
from biflow.orchestrator.tracing import DbTraceLogger, MemoryTraceLogger, TraceLogger

log = logging.getLogger("biflow.run")
EXIT_CODES = {RunStatus.SUCCESS: 0, RunStatus.FAILED: 1, RunStatus.UNRELIABLE: 2}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    p = argparse.ArgumentParser(description="Exécute le pipeline BIFlow de bout en bout")
    p.add_argument("--start", help="Date de début incluse (AAAA-MM-JJ)")
    p.add_argument("--end", help="Date de fin incluse (AAAA-MM-JJ)")
    p.add_argument("--file", help="Extrait parquet/CSV à utiliser au lieu de raw.nyc311")
    p.add_argument("--no-db", action="store_true", help="Trace en mémoire (sans PostgreSQL)")
    p.add_argument("--max-retries", type=int, default=settings.max_retries)
    p.add_argument("--retry-delay", type=float, default=settings.retry_delay_seconds)
    p.add_argument("--max-feedback-rounds", type=int, default=settings.max_feedback_rounds)
    sim = p.add_argument_group("simulation (démo / tests)")
    sim.add_argument("--simulate-failure", choices=STEP_ORDER, help="Cette étape échoue toujours")
    sim.add_argument("--simulate-audit-flag", choices=STEP_ORDER[:-1],
                     help="L'Auditeur stub signale cette étape comme fautive")
    sim.add_argument("--persistent", action="store_true",
                     help="Avec --simulate-audit-flag : le problème persiste après correction")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)

    trace_logger: TraceLogger
    trace_target = "mémoire (--no-db)"
    if args.no_db:
        trace_logger = MemoryTraceLogger()
    else:
        engine = get_engine()
        if can_connect(engine):
            init_db(engine)
            trace_logger = DbTraceLogger(engine)
            trace_target = "audit.trace_log"
        else:
            log.warning("PostgreSQL injoignable (docker compose up -d ?) -> trace en mémoire")
            trace_logger = MemoryTraceLogger()

    agents = default_agents()
    if args.simulate_audit_flag:
        agents["auditor_xai"] = AuditorXAIAgent(args.simulate_audit_flag, args.persistent)
    if args.simulate_failure:
        agents[args.simulate_failure] = FailingAgent(agents[args.simulate_failure])

    ctx = RunContext(
        trace_logger=trace_logger,
        max_retries=args.max_retries,
        retry_delay_seconds=args.retry_delay,
        max_feedback_rounds=args.max_feedback_rounds,
    )
    dataset = DatasetRef(start_date=args.start, end_date=args.end, file_path=args.file)
    state = run_pipeline(dataset, agents=agents, ctx=ctx)
    if trace_target == "audit.trace_log":
        trace_target += f" (WHERE run_id = '{state.run_id}')"
    print(format_summary(state, trace_target))
    return EXIT_CODES.get(state.status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
