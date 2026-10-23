from datetime import date

import httpx
import pandas as pd
import pytest

from biflow.common.schema import RAW_COLUMNS
from biflow.ingestion.socrata import (
    build_complaint_filter,
    build_query_params,
    build_where,
    fetch_records,
    records_to_frame,
)


def test_complaint_filter_prefix_and_exact():
    f = build_complaint_filter(["Noise*", "Street Condition", "Blocked Driveway"])
    assert f == (
        "(complaint_type like 'Noise%' OR "
        "complaint_type in('Street Condition', 'Blocked Driveway'))"
    )


def test_complaint_filter_escapes_quotes_and_empty():
    assert build_complaint_filter(["O'Brien"]) == "(complaint_type in('O''Brien'))"
    assert build_complaint_filter([]) is None


def test_where_end_date_is_inclusive():
    where = build_where(date(2025, 1, 1), date(2025, 1, 31), [])
    assert where == (
        "created_date >= '2025-01-01T00:00:00' AND created_date < '2025-02-01T00:00:00'"
    )


def test_query_params():
    p = build_query_params(date(2025, 1, 1), date(2025, 1, 2), ["Noise*"], limit=100, offset=200)
    assert p["$select"] == ", ".join(RAW_COLUMNS)
    assert p["$limit"] == "100" and p["$offset"] == "200"
    assert "complaint_type like 'Noise%'" in p["$where"]
    assert p["$order"] == "created_date DESC, unique_key ASC"


@pytest.mark.parametrize("kwargs", [
    {"start": date(2025, 2, 1), "end": date(2025, 1, 1), "limit": 10},
    {"start": date(2025, 1, 1), "end": date(2025, 1, 2), "limit": 0},
    {"start": date(2025, 1, 1), "end": date(2025, 1, 2), "limit": 60_000},
])
def test_query_params_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        build_query_params(complaint_types=[], **kwargs)


def _fake_rows(n, start=0):
    return [{"unique_key": str(start + i), "created_date": "2025-01-01T10:00:00.000",
             "complaint_type": "Noise - Residential"} for i in range(n)]


def test_fetch_records_paginates_and_respects_cap():
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        limit = int(request.url.params["$limit"])
        offset = int(request.url.params["$offset"])
        offsets.append((offset, limit))
        return httpx.Response(200, json=_fake_rows(limit, offset))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pages = list(fetch_records(date(2025, 1, 1), date(2025, 1, 2), ["Noise*"],
                               max_rows=25, page_size=10, client=client))
    assert [len(p) for p in pages] == [10, 10, 5]
    assert offsets == [(0, 10), (10, 10), (20, 5)]


def test_fetch_records_stops_on_short_page():
    def handler(request):
        return httpx.Response(200, json=_fake_rows(3))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pages = list(fetch_records(date(2025, 1, 1), date(2025, 1, 2), [], max_rows=100,
                               page_size=10, client=client))
    assert len(pages) == 1


def test_records_to_frame_types_and_missing_columns():
    df = records_to_frame([
        {"unique_key": "123", "created_date": "2025-01-01T10:00:00.000", "latitude": "40.7"},
        {"unique_key": None, "created_date": "2025-01-01T10:00:00.000"},
    ])
    assert list(df.columns) == RAW_COLUMNS
    assert len(df) == 1  # ligne sans clé écartée
    assert df["unique_key"].iloc[0] == 123
    assert pd.api.types.is_datetime64_any_dtype(df["created_date"])
    assert df["latitude"].iloc[0] == pytest.approx(40.7)
    assert pd.isna(df["closed_date"].iloc[0])
