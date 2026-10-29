"""Instantanés de données : une version figée et identifiée (empreinte) des données d'un run.

Les KPI sont calculés sur un instantané parquet ; l'Auditeur recalcule l'empreinte et
rejoue les requêtes sur ce même instantané (reproductibilité).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


def frame_hash(df: pd.DataFrame) -> str:
    """Empreinte SHA-256 du contenu (colonnes + valeurs), indépendante du format de fichier."""
    h = hashlib.sha256(",".join(map(str, df.columns)).encode())
    h.update(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes())
    return h.hexdigest()


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_snapshot(df: pd.DataFrame, workspace: str | Path, name: str) -> tuple[str, str]:
    """Écrit `df` dans `<workspace>/<name>.parquet`. Retourne (chemin, empreinte du contenu)."""
    path = Path(workspace) / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    # empreinte calculée sur la relecture : c'est ce que l'Auditeur relira
    return str(path), frame_hash(read_snapshot(path))


def read_snapshot(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)
