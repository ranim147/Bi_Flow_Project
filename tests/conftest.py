"""Fixtures partagées. Les tests marqués `db` utilisent une base dédiée (POSTGRES_TEST_DB,
défaut biflow_test) créée automatiquement ; ils sont ignorés si PostgreSQL est injoignable."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from biflow.agents.base import RunContext
from biflow.common.config import get_settings
from biflow.db.init_db import init_db
from biflow.orchestrator.tracing import MemoryTraceLogger


@pytest.fixture(scope="session")
def db_engine():
    settings = get_settings()
    admin = create_engine(settings.database_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": settings.postgres_test_db}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{settings.postgres_test_db}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL injoignable ({type(exc).__name__}) — lancer `docker compose up -d`")
    finally:
        admin.dispose()

    engine = create_engine(settings.test_database_url)
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE raw.nyc311, audit.trace_log"))
    return db_engine


@pytest.fixture
def ctx() -> RunContext:
    return RunContext(trace_logger=MemoryTraceLogger(), max_retries=2,
                      retry_delay_seconds=0, sleep=lambda _: None)
