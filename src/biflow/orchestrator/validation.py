"""Contrôles de validation réutilisables (entre les étapes et sur les données brutes).

Chaque fonction retourne une liste de `Check`. Un `Check` de sévérité "error" qui échoue
fait échouer l'étape (=> nouvel essai, puis run en échec). "warning" est seulement tracé.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import pandas as pd

from biflow.common.schema import RAW_COLUMNS
from biflow.orchestrator.state import Check


def check_required_columns(
    columns: Iterable[str], required: Iterable[str], name: str = "schema"
) -> Check:
    missing = sorted(set(required) - set(columns))
    return Check(
        name=name,
        passed=not missing,
        message=f"colonnes manquantes : {missing}" if missing else "toutes les colonnes attendues sont présentes",
    )


def check_in_range(
    name: str, value: float | None, low: float | None = None, high: float | None = None,
    severity: str = "error",
) -> Check:
    ok = value is not None and not (isinstance(value, float) and math.isnan(value))
    if ok and low is not None:
        ok = value >= low
    if ok and high is not None:
        ok = value <= high
    bounds = f"[{'-inf' if low is None else low}, {'+inf' if high is None else high}]"
    return Check(
        name=name, passed=ok, severity=severity,
        message=f"valeur {value} {'dans' if ok else 'hors de'} l'intervalle {bounds}",
    )


def check_closed_after_created(df: pd.DataFrame, max_ratio: float = 0.05) -> Check:
    """Compte les demandes avec closed_date < created_date (anomalie connue de NYC 311).

    Avertissement seulement : c'est à l'agent Qualité/ETL de les traiter.
    """
    both = df.dropna(subset=["created_date", "closed_date"])
    bad = int((both["closed_date"] < both["created_date"]).sum())
    ratio = bad / len(df) if len(df) else 0.0
    return Check(
        name="closed_after_created",
        passed=ratio <= max_ratio,
        severity="warning",
        message=f"{bad} lignes avec closed_date < created_date ({ratio:.2%})",
    )


def check_raw_frame(df: pd.DataFrame) -> list[Check]:
    """Contrôles de base sur un extrait brut NYC 311."""
    checks = [check_required_columns(df.columns, RAW_COLUMNS)]
    if checks[0].passed:
        dup = int(df["unique_key"].duplicated().sum())
        checks.append(Check(name="unique_key_unique", passed=dup == 0,
                            message=f"{dup} clés dupliquées"))
        checks.append(check_closed_after_created(df))
        null_created = int(df["created_date"].isna().sum())
        checks.append(Check(name="created_date_not_null", passed=null_created == 0,
                            severity="warning", message=f"{null_created} created_date nulles"))
    return checks
