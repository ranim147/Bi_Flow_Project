"""Accès aux données pour les agents : charger le jeu de données référencé par le state."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from biflow.common.schema import RAW_COLUMNS
from biflow.db.engine import get_engine

if TYPE_CHECKING:
    from biflow.orchestrator.state import DatasetRef


def load_raw_frame(
    dataset: DatasetRef, engine: Engine | None = None, limit: int | None = None
) -> pd.DataFrame:
    """Charge les lignes brutes correspondant au `DatasetRef` du run.

    - si `dataset.file_path` est renseigné, lit le fichier (parquet ou CSV) ;
    - sinon lit la table `dataset.table` filtrée sur la fenêtre de dates.
    """
    if dataset.file_path:
        if dataset.file_path.endswith(".parquet"):
            df = pd.read_parquet(dataset.file_path)
        else:
            df = pd.read_csv(dataset.file_path, parse_dates=["created_date", "closed_date"])
        return df.head(limit) if limit else df

    clauses, params = [], {}
    if dataset.start_date:
        clauses.append("created_date >= :start")
        params["start"] = dataset.start_date
    if dataset.end_date:
        clauses.append("created_date < CAST(:end AS date) + 1")
        params["end"] = dataset.end_date
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {', '.join(RAW_COLUMNS)} FROM {dataset.table} {where} ORDER BY created_date"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with (engine or get_engine()).connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)
