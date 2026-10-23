from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import text

from biflow.db.loader import upsert_nyc311
from biflow.ingestion.socrata import records_to_frame
from biflow.orchestrator.tracing import DbTraceLogger, TraceRecord

pytestmark = pytest.mark.db


def _frame(rows):
    return records_to_frame(rows)


def _row(key, status="Open", closed=None):
    return {"unique_key": str(key), "created_date": "2025-03-01T08:30:00.000",
            "closed_date": closed, "agency": "NYPD", "complaint_type": "Noise - Residential",
            "borough": "BROOKLYN", "status": status, "latitude": "40.65", "longitude": "-73.95"}


def test_upsert_inserts_then_updates(clean_db):
    first = upsert_nyc311(clean_db, _frame([_row(1), _row(2)]))
    assert (first.inserted, first.updated) == (2, 0)

    second = upsert_nyc311(clean_db, _frame([_row(2, "Closed", "2025-03-01T12:00:00.000"), _row(3)]))
    assert (second.inserted, second.updated) == (1, 1)

    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM raw.nyc311")).scalar() == 3
        status, closed = conn.execute(
            text("SELECT status, closed_date FROM raw.nyc311 WHERE unique_key = 2")).one()
    assert status == "Closed"
    assert closed == datetime(2025, 3, 1, 12, 0)


def test_upsert_is_idempotent_and_handles_nulls(clean_db):
    df = _frame([_row(10), {"unique_key": "11"}])  # 2e ligne : presque tout NULL
    upsert_nyc311(clean_db, df)
    again = upsert_nyc311(clean_db, df)
    assert (again.inserted, again.updated) == (0, 2)
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM raw.nyc311")).scalar() == 2
        assert conn.execute(
            text("SELECT borough FROM raw.nyc311 WHERE unique_key = 11")).scalar() is None


def test_upsert_deduplicates_within_batch(clean_db):
    res = upsert_nyc311(clean_db, _frame([_row(5, "Open"), _row(5, "Closed")]))
    assert res.total == 1
    with clean_db.connect() as conn:
        assert conn.execute(
            text("SELECT status FROM raw.nyc311 WHERE unique_key = 5")).scalar() == "Closed"


def test_upsert_empty_frame(clean_db):
    assert upsert_nyc311(clean_db, pd.DataFrame()).total == 0


def test_reference_tables_seeded(db_engine):
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM ref.boroughs")).scalar() == 5
        placeholders = conn.execute(
            text("SELECT bool_and(is_placeholder) FROM ref.sla_targets")).scalar()
    assert placeholders is True


def test_db_trace_logger_roundtrip(clean_db):
    logger = DbTraceLogger(clean_db)
    now = datetime.now(UTC)
    logger.log(TraceRecord(run_id="r1", step="quality_etl", status="success", started_at=now,
                           ended_at=now, input_rows=10, output_rows=8, details={"k": [1, 2]}))
    rows = logger.fetch_run("r1")
    assert len(rows) == 1
    assert rows[0]["output_rows"] == 8 and rows[0]["details"] == {"k": [1, 2]}
