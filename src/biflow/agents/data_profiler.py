"""Agent 1 — Data Profiler (code classique, sans LLM dans le MVP).

Charge les données brutes du run (`load_raw_frame(state.dataset)`) et produit un profil :
- nombre de lignes, colonnes, taux de valeurs nulles par colonne ;
- plage de dates (created_date min/max) ;
- distributions des complaint_type, boroughs, statuts ;
- défauts repérés (`issues`), chacun sur une colonne et avec un type précis
  (doublon, manquant, mauvais type, format invalide, hors plage, incohérence) :
  ils sont traités par l'agent Qualité/ETL et servent à l'évaluation (preuve 1 du MVP).

Entrée  : state.dataset (DatasetRef)
Sortie  : ProfilerOutput -> state.outputs["data_profiler"]
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.common.data import load_metadata, load_raw_frame
from biflow.common.schema import (
    BOROUGHS,
    DATETIME_COLUMNS,
    FLOAT_COLUMNS,
    RAW_COLUMNS,
    REQUIRED_COLUMNS,
)
from biflow.orchestrator.state import Check, PipelineState
from biflow.orchestrator.validation import check_in_range, check_required_columns

# Emprise approximative de New York City
NYC_LAT = (40.47, 40.93)
NYC_LON = (-74.27, -73.68)
REQUIRED_VALUES = ["created_date", "complaint_type", "borough"]
ZIP_PATTERN = r"^\d{5}$"

IssueType = Literal["duplicate", "missing", "invalid_type", "invalid_format", "invalid_value",
                    "out_of_range", "inconsistent"]


class DataIssue(BaseModel):
    column: str
    issue_type: IssueType
    count: int
    description: str
    sample_keys: list[int] = Field(default_factory=list, description="quelques unique_key concernées")


class ProfilerOutput(AgentOutput):
    row_count: int
    columns: list[str]
    null_rates: dict[str, float] = Field(default_factory=dict, description="colonne -> [0, 1]")
    date_min: str | None = None
    date_max: str | None = None
    complaint_type_counts: dict[str, int] = Field(default_factory=dict)
    borough_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    issues: list[DataIssue] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list, description="issues en texte lisible")
    absent_columns: list[str] = Field(default_factory=list,
                                      description="colonnes facultatives absentes de l'extrait (vides)")
    source: dict = Field(default_factory=dict, description="provenance de l'extrait (fiche .meta.json)")


def coerce_types(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Convertit les colonnes date/nombre ; retourne aussi, par colonne, le masque des valeurs
    présentes mais non convertibles (« mauvais type »)."""
    df = df.copy()
    bad: dict[str, pd.Series] = {}
    for col in DATETIME_COLUMNS + FLOAT_COLUMNS:
        if col not in df:
            continue
        raw = df[col]
        if col in DATETIME_COLUMNS:
            if pd.api.types.is_datetime64_any_dtype(raw):
                continue
            conv = pd.to_datetime(raw, errors="coerce", format="mixed")
        else:
            if pd.api.types.is_numeric_dtype(raw):
                continue
            conv = pd.to_numeric(raw, errors="coerce")
        bad[col] = raw.notna() & conv.isna()
        df[col] = conv
    df["unique_key"] = pd.to_numeric(df["unique_key"], errors="coerce").astype("Int64")
    return df, bad


def _keys(df: pd.DataFrame, mask: pd.Series, n: int = 5) -> list[int]:
    return [int(k) for k in df.loc[mask, "unique_key"].dropna().head(n)]


def find_issues(raw: pd.DataFrame) -> list[DataIssue]:
    df, bad_types = coerce_types(raw)
    issues: list[DataIssue] = []

    def add(column: str, issue_type: IssueType, mask: pd.Series, description: str) -> None:
        count = int(mask.sum())
        if count:
            issues.append(DataIssue(column=column, issue_type=issue_type, count=count,
                                    description=f"{count} {description}", sample_keys=_keys(df, mask)))

    add("unique_key", "duplicate", df["unique_key"].duplicated(keep="first") & df["unique_key"].notna(),
        "lignes avec une unique_key déjà vue (doublons)")
    for col, mask in bad_types.items():
        add(col, "invalid_type", mask, f"valeurs de {col} non convertibles")
    no_bad = pd.Series(False, index=df.index)
    for col in REQUIRED_VALUES:
        add(col, "missing", df[col].isna() & ~bad_types.get(col, no_bad), f"valeurs de {col} manquantes")

    both = df["created_date"].notna() & df["closed_date"].notna()
    add("closed_date", "inconsistent", both & (df["closed_date"] < df["created_date"]),
        "lignes avec closed_date < created_date")
    add("closed_date", "missing",
        (df["status"] == "Closed") & df["closed_date"].isna() & ~bad_types.get("closed_date", no_bad),
        "demandes « Closed » sans closed_date")

    zips = df["incident_zip"].astype("string").str.strip()
    add("incident_zip", "invalid_format", zips.notna() & ~zips.str.match(ZIP_PATTERN, na=False),
        "codes postaux qui ne sont pas à 5 chiffres")
    add("borough", "invalid_value", df["borough"].notna() & ~df["borough"].isin(BOROUGHS),
        "boroughs hors des 5 boroughs officiels (ex. « Unspecified »)")
    for col, (low, high) in (("latitude", NYC_LAT), ("longitude", NYC_LON)):
        values = df[col]
        add(col, "out_of_range", values.notna() & ~values.between(low, high),
            f"{col} hors de l'emprise de New York")
    return issues


class DataProfilerAgent(BaseAgent):
    name = "data_profiler"
    output_model = ProfilerOutput

    def run(self, state: PipelineState) -> ProfilerOutput:
        raw = load_raw_frame(state.dataset)
        missing_cols = sorted(set(REQUIRED_COLUMNS) - set(raw.columns))
        if missing_cols:
            raise ValueError(f"colonnes manquantes dans les données brutes : {missing_cols}")
        df, _ = coerce_types(raw)
        issues = find_issues(raw)
        n = len(df)

        def counts(col: str) -> dict[str, int]:
            return {str(k): int(v) for k, v in df[col].value_counts(dropna=False).items()}

        dates = df["created_date"].dropna()
        absent = raw.attrs.get("absent_columns", [])
        source = load_metadata(state.dataset)
        notes = [f"{len(issues)} type(s) de défaut détecté(s)"]
        if absent:
            notes.append(f"colonnes absentes de l'extrait (laissées vides) : {absent}")
        return ProfilerOutput(
            row_count=n,
            columns=list(raw.columns),
            null_rates={c: round(float(df[c].isna().mean()), 4) if n else 0.0 for c in RAW_COLUMNS},
            date_min=dates.min().isoformat() if len(dates) else None,
            date_max=dates.max().isoformat() if len(dates) else None,
            complaint_type_counts=counts("complaint_type"),
            borough_counts=counts("borough"),
            status_counts=counts("status"),
            issues=issues,
            anomalies=[f"{i.column} [{i.issue_type}] : {i.description}" for i in issues],
            absent_columns=absent,
            source=source,
            input_rows=n,
            output_rows=n,
            notes=notes,
        )

    def validate(self, output: ProfilerOutput, state: PipelineState) -> list[Check]:
        checks = [
            check_required_columns(output.columns, REQUIRED_COLUMNS),
            check_in_range("row_count", output.row_count, low=1),
        ]
        bad = {c: r for c, r in output.null_rates.items() if not 0 <= r <= 1}
        checks.append(Check(name="null_rates_range", passed=not bad,
                            message=f"taux hors [0,1] : {bad}" if bad else "ok"))
        return checks
