"""Fixtures partagées. Les tests marqués `db` utilisent une base dédiée (POSTGRES_TEST_DB,
défaut biflow_test) créée automatiquement ; ils sont ignorés si PostgreSQL est injoignable."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from biflow.agents.base import RunContext
from biflow.common.config import get_settings
from biflow.db.init_db import init_db
from biflow.evaluation.synthetic import SyntheticDataset, generate
from biflow.orchestrator.graph import run_pipeline
from biflow.orchestrator.state import DatasetRef
from biflow.orchestrator.tracing import MemoryTraceLogger


@pytest.fixture(scope="session")
def db_engine():
    settings = get_settings()
    admin = create_engine(settings.database_url("postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 3})
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


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Les tests n'appellent jamais l'API Mistral, même si MISTRAL_API_KEY est dans .env."""
    for module in ("biflow.agents.kpi_semantic", "biflow.agents.analyste_bi"):
        monkeypatch.setattr(f"{module}.get_llm", lambda: None)


@pytest.fixture(scope="session")
def synthetic() -> SyntheticDataset:
    """Petit jeu synthétique (4 semaines) avec défauts et anomalie connus."""
    return generate(days=28, daily=90, spike_day=20)


@pytest.fixture(scope="session")
def synthetic_dataset(synthetic, tmp_path_factory) -> DatasetRef:
    return DatasetRef(file_path=str(synthetic.write(tmp_path_factory.mktemp("data"))))


@pytest.fixture
def pipeline(ctx, synthetic_dataset, tmp_path):
    """`pipeline(agents=..., questions=...)` : run complet sur le jeu synthétique, sans base."""
    def _run(**kwargs):
        kwargs.setdefault("ctx", ctx)
        kwargs.setdefault("output_dir", tmp_path / "runs")
        return run_pipeline(kwargs.pop("dataset", synthetic_dataset), **kwargs)
    return _run
