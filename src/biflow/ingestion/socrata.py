"""Client Socrata (SODA 2.x) pour le jeu NYC 311 `erm2-nwe9`.

Construit les paramètres SoQL ($select, $where, $order, $limit, $offset), pagine,
et normalise les enregistrements JSON en DataFrame typé selon `RAW_COLUMNS`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import date, timedelta

import httpx
import pandas as pd

from biflow.common.schema import DATETIME_COLUMNS, FLOAT_COLUMNS, RAW_COLUMNS, TEXT_COLUMNS

log = logging.getLogger(__name__)

SOCRATA_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
MAX_PAGE_SIZE = 50_000  # limite d'une requête SODA

# Un motif se terminant par `*` est un préfixe (LIKE 'Noise%'), sinon égalité exacte.
DEFAULT_COMPLAINT_TYPES = ["Noise*", "Street*"]


def _quote(value: str) -> str:
    """Littéral SoQL : apostrophes doublées."""
    return "'" + value.replace("'", "''") + "'"


def build_complaint_filter(complaint_types: list[str]) -> str | None:
    """`["Noise*", "Blocked Driveway"]` -> `(complaint_type like 'Noise%' OR complaint_type in('Blocked Driveway'))`."""
    if not complaint_types:
        return None
    prefixes = [ct[:-1] for ct in complaint_types if ct.endswith("*")]
    exact = [ct for ct in complaint_types if not ct.endswith("*")]
    parts = [f"complaint_type like {_quote(p + '%')}" for p in prefixes]
    if exact:
        parts.append(f"complaint_type in({', '.join(_quote(e) for e in exact)})")
    return "(" + " OR ".join(parts) + ")"


def build_where(start: date, end: date, complaint_types: list[str]) -> str:
    """Fenêtre [start, end] inclusive sur created_date + filtre de types."""
    end_exclusive = end + timedelta(days=1)
    clauses = [
        f"created_date >= '{start.isoformat()}T00:00:00'",
        f"created_date < '{end_exclusive.isoformat()}T00:00:00'",
    ]
    ct_filter = build_complaint_filter(complaint_types)
    if ct_filter:
        clauses.append(ct_filter)
    return " AND ".join(clauses)


def build_query_params(
    start: date,
    end: date,
    complaint_types: list[str],
    limit: int,
    offset: int = 0,
) -> dict[str, str]:
    """Paramètres SoQL d'une page. Tri stable (date desc puis clé) pour une pagination fiable."""
    if start > end:
        raise ValueError(f"start ({start}) doit être <= end ({end})")
    if not 0 < limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit doit être dans ]0, {MAX_PAGE_SIZE}]")
    return {
        "$select": ", ".join(RAW_COLUMNS),
        "$where": build_where(start, end, complaint_types),
        "$order": "created_date DESC, unique_key ASC",
        "$limit": str(limit),
        "$offset": str(offset),
    }


def _get_with_retry(
    client: httpx.Client, params: dict[str, str], attempts: int = 4
) -> list[dict]:
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(SOCRATA_URL, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            retriable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code == 429 or exc.response.status_code >= 500
            )
            if not retriable or attempt == attempts:
                raise
            wait = 2**attempt
            log.warning("Requête Socrata échouée (%s), nouvel essai dans %ss", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_records(
    start: date,
    end: date,
    complaint_types: list[str],
    max_rows: int,
    page_size: int = MAX_PAGE_SIZE,
    app_token: str | None = None,
    client: httpx.Client | None = None,
) -> Iterator[list[dict]]:
    """Itère sur les pages de résultats jusqu'à `max_rows` lignes ou épuisement."""
    headers = {"X-App-Token": app_token} if app_token else {}
    own_client = client is None
    client = client or httpx.Client(timeout=120, headers=headers)
    try:
        fetched = 0
        while fetched < max_rows:
            limit = min(page_size, max_rows - fetched)
            params = build_query_params(start, end, complaint_types, limit, offset=fetched)
            page = _get_with_retry(client, params)
            if not page:
                break
            fetched += len(page)
            log.info("Page reçue : %d lignes (total %d)", len(page), fetched)
            yield page
            if len(page) < limit:
                break
    finally:
        if own_client:
            client.close()


def records_to_frame(records: list[dict]) -> pd.DataFrame:
    """JSON Socrata -> DataFrame typé, colonnes manquantes ajoutées à NULL."""
    df = pd.DataFrame.from_records(records)
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[RAW_COLUMNS].copy()
    df["unique_key"] = pd.to_numeric(df["unique_key"], errors="coerce").astype("Int64")
    for col in DATETIME_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in TEXT_COLUMNS:
        df[col] = df[col].astype("string")
    return df.dropna(subset=["unique_key"])
