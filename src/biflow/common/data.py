"""Accès aux données pour les agents : charger le jeu de données référencé par le state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from biflow.common.schema import OPTIONAL_COLUMNS, RAW_COLUMNS
from biflow.db.engine import get_engine

if TYPE_CHECKING:
    from biflow.orchestrator.state import DatasetRef


def load_raw_frame(
    dataset: DatasetRef, engine: Engine | None = None, limit: int | None = None
) -> pd.DataFrame:
    """Charge les lignes brutes correspondant au `DatasetRef` du run.

    - si `dataset.file_path` est renseigné, lit le fichier (parquet ou CSV) ;
    - sinon lit la table `dataset.table` filtrée sur la fenêtre de dates.

    Un CSV est lu en texte brut (seule la chaîne vide vaut NULL) : une valeur mal typée
    (« N/A » dans latitude...) reste visible pour le Data Profiler au lieu d'être
    silencieusement convertie en NULL. La conversion est faite par les agents.

    Une colonne facultative absente du fichier (`OPTIONAL_COLUMNS`) est ajoutée vide ; la
    liste est gardée dans `df.attrs["absent_columns"]` pour le Data Profiler.
    """
    if dataset.file_path:
        if dataset.file_path.endswith(".parquet"):
            df = pd.read_parquet(dataset.file_path)
        else:
            df = pd.read_csv(dataset.file_path, dtype=str, keep_default_na=False, na_values=[""])
        absent = [c for c in OPTIONAL_COLUMNS if c not in df.columns]
        for col in absent:
            df[col] = None
        df = df.head(limit) if limit else df
        df.attrs["absent_columns"] = absent
        return df

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


def metadata_path(file_path: str | Path) -> Path:
    """Fiche de provenance d'un extrait : `<fichier>.meta.json` à côté du fichier."""
    path = Path(file_path)
    return path.with_name(path.name + ".meta.json")


def load_metadata(dataset: DatasetRef) -> dict[str, Any]:
    """Provenance de l'extrait (source, méthode d'échantillonnage, réserves), si elle existe."""
    if not dataset.file_path:
        return {}
    path = metadata_path(dataset.file_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
