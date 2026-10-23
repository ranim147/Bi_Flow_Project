"""Traçabilité : écriture d'une ligne par tentative d'étape dans audit.trace_log.

Deux implémentations du même protocole :
- `DbTraceLogger`     : PostgreSQL (utilisé par défaut)
- `MemoryTraceLogger` : en mémoire (tests, ou exécution sans base avec --no-db)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from biflow.db.models import trace_log

log = logging.getLogger(__name__)


class TraceRecord(BaseModel):
    run_id: str
    step: str
    attempt: int = 1
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    input_rows: int | None = None
    output_rows: int | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class TraceLogger(Protocol):
    def log(self, record: TraceRecord) -> None: ...


class MemoryTraceLogger:
    def __init__(self) -> None:
        self.records: list[TraceRecord] = []

    def log(self, record: TraceRecord) -> None:
        self.records.append(record)


class DbTraceLogger:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def log(self, record: TraceRecord) -> None:
        # Une erreur d'écriture de trace ne doit pas faire tomber le pipeline
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(trace_log).values(**record.model_dump(mode="json")))
        except Exception:
            log.exception("Impossible d'écrire dans audit.trace_log (step=%s)", record.step)

    def fetch_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(trace_log).where(trace_log.c.run_id == run_id).order_by(trace_log.c.id)
            )
            return [dict(r._mapping) for r in rows]
