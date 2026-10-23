"""Chargement idempotent (upsert sur unique_key) d'un DataFrame dans raw.nyc311."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy.engine import Engine

from biflow.common.schema import RAW_COLUMNS, RAW_TABLE

log = logging.getLogger(__name__)


@dataclass
class UpsertResult:
    inserted: int
    updated: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated


def upsert_nyc311(engine: Engine, df: pd.DataFrame, table: str = RAW_TABLE) -> UpsertResult:
    """Upsert des lignes dans `table` : COPY vers une table temporaire puis
    INSERT ... ON CONFLICT (unique_key) DO UPDATE. Rejouer le même extrait est sans effet de bord.
    """
    if df.empty:
        return UpsertResult(0, 0)
    df = df[RAW_COLUMNS].drop_duplicates(subset="unique_key", keep="last")
    # Types Python natifs (int, float, Timestamp, str) et None pour psycopg
    df = df.astype({"unique_key": "int64"}).astype(object)
    df = df.where(df.notna(), None)
    cols = ", ".join(RAW_COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in RAW_COLUMNS if c != "unique_key")

    with engine.begin() as conn:
        pg = conn.connection.driver_connection  # connexion psycopg 3 native
        with pg.cursor() as cur:
            cur.execute(
                f"CREATE TEMP TABLE _stage_nyc311 (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            with cur.copy(f"COPY _stage_nyc311 ({cols}) FROM STDIN") as copy:
                for row in df.itertuples(index=False, name=None):
                    copy.write_row(row)
            cur.execute(
                f"""
                INSERT INTO {table} ({cols}, ingested_at)
                SELECT {cols}, now() FROM _stage_nyc311
                ON CONFLICT (unique_key) DO UPDATE SET {updates}, ingested_at = now()
                RETURNING (xmax = 0) AS inserted
                """
            )
            flags = [r[0] for r in cur.fetchall()]
    result = UpsertResult(inserted=sum(flags), updated=len(flags) - sum(flags))
    log.info("Upsert %s : %d insérées, %d mises à jour", table, result.inserted, result.updated)
    return result
