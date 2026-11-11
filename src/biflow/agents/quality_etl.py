"""Agent 2 — Qualité / ETL (code classique, sans LLM dans le MVP).

À partir des données brutes et du profil (`state.output("data_profiler")`) :
- nettoyer : types, doublons, closed_date < created_date, boroughs hors liste,
  codes postaux invalides, coordonnées hors NYC, libellés de complaint_type ;
- minimiser (RGPD) : texte libre `resolution_description` retiré, coordonnées ramenées
  à une zone (2 décimales, environ 1 km) ;
- enrichir : resolution_hours, jour/heure, objectif SLA (ref.sla_targets), within_sla ;
- écrire un INSTANTANÉ parquet versionné par son empreinte (lu par les KPI et l'Auditeur),
  et, si le run lit PostgreSQL, la table processed.nyc311_clean ;
- garder en QUARANTAINE (`quarantine.parquet`, dossier du run) chaque ligne supprimée, avec
  la règle qui l'a écartée : aucune ligne ne disparaît sans trace (texte libre exclu).
Chaque règle appliquée est listée dans `rules_applied` avec son nombre de lignes.

Les règles sont du code relu dans Git : toute nouvelle règle est validée avant usage.

Entrée  : state.dataset, state.outputs["data_profiler"]
Sortie  : QualityOutput -> state.outputs["quality_etl"]
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from pydantic import Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.agents.data_profiler import NYC_LAT, NYC_LON, ZIP_PATTERN, coerce_types
from biflow.common.data import load_raw_frame
from biflow.common.reference import load_reference
from biflow.common.schema import BOROUGHS
from biflow.common.snapshot import write_snapshot
from biflow.orchestrator.state import Check, PipelineState

log = logging.getLogger(__name__)

CLEAN_TABLE = "processed.nyc311_clean"
UNSPECIFIED = "UNSPECIFIED"
DROPPED_COLUMNS = ["resolution_description"]  # texte libre : jamais dans la couche propre


class QualityOutput(AgentOutput):
    rows_in: int
    rows_out: int
    rows_dropped: int
    invalid_dates: int = Field(0, description="lignes avec closed_date < created_date")
    duplicates: int = 0
    output_table: str = CLEAN_TABLE
    snapshot_path: str = Field(description="instantané parquet des données nettoyées")
    snapshot_hash: str = Field(description="empreinte SHA-256 du contenu de l'instantané")
    quarantine_path: str | None = Field(None, description="lignes supprimées, avec leur motif")
    quarantine_rows: int = 0
    columns: list[str] = Field(default_factory=list)
    rules_applied: list[str] = Field(default_factory=list)
    rule_counts: dict[str, int] = Field(default_factory=dict)


def clean_frame(raw: pd.DataFrame, sla_targets: pd.DataFrame,
                quarantine: list[pd.DataFrame] | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """Applique les règles de nettoyage. Retourne (données propres, lignes touchées par règle).
    Les lignes supprimées sont ajoutées à `quarantine` (telles que reçues, avec leur motif)."""
    counts: dict[str, int] = {}
    df, bad_types = coerce_types(raw)
    counts["types convertis (valeurs invalides -> NULL)"] = int(sum(m.sum() for m in bad_types.values()))

    def drop(rule: str, mask: pd.Series) -> None:
        nonlocal df
        counts[rule] = int(mask.sum())
        if quarantine is not None and mask.any():
            kept = raw.loc[mask[mask].index].drop(columns=DROPPED_COLUMNS, errors="ignore")
            quarantine.append(kept.astype("string").assign(motif=rule))
        df = df[~mask]

    drop("suppression : unique_key absente", df["unique_key"].isna())
    drop("suppression : created_date absente ou invalide", df["created_date"].isna())
    drop("suppression : doublons de unique_key (dernière version gardée)",
         df["unique_key"].duplicated(keep="last"))
    drop("suppression : closed_date < created_date",
         df["closed_date"].notna() & (df["closed_date"] < df["created_date"]))
    drop("suppression : complaint_type absent", df["complaint_type"].isna())
    df = df.copy()

    def fix(rule: str, mask: pd.Series, column: str, value) -> None:
        counts[rule] = int(mask.sum())
        df.loc[mask, column] = value

    df["complaint_type"] = df["complaint_type"].astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    df["borough"] = df["borough"].astype("string").str.strip().str.upper()
    fix("borough hors des 5 boroughs -> UNSPECIFIED", ~df["borough"].isin(BOROUGHS), "borough", UNSPECIFIED)
    zips = df["incident_zip"].astype("string").str.strip()
    fix("code postal invalide -> NULL", zips.notna() & ~zips.str.match(ZIP_PATTERN, na=False),
        "incident_zip", pd.NA)
    outside = ~(df["latitude"].between(*NYC_LAT) & df["longitude"].between(*NYC_LON))
    outside &= df["latitude"].notna() | df["longitude"].notna()
    counts["coordonnées hors NYC -> NULL"] = int(outside.sum())
    df.loc[outside, ["latitude", "longitude"]] = float("nan")
    df["latitude"] = df["latitude"].round(2)
    df["longitude"] = df["longitude"].round(2)
    counts["minimisation : coordonnées arrondies à 2 décimales"] = int(df["latitude"].notna().sum())
    counts["minimisation : resolution_description retirée"] = len(df)
    df = df.drop(columns=[c for c in DROPPED_COLUMNS if c in df])

    # Enrichissement
    df["is_closed"] = df["closed_date"].notna()
    df["resolution_hours"] = (df["closed_date"] - df["created_date"]).dt.total_seconds() / 3600
    df["created_day"] = df["created_date"].dt.normalize().dt.date
    df["created_hour"] = df["created_date"].dt.hour.astype("int64")
    df["weekday"] = df["created_date"].dt.day_of_week.astype("int64")
    targets = sla_targets.set_index("complaint_type")["target_hours"].astype(float)
    df["target_hours"] = df["complaint_type"].map(targets).astype("float64")
    within = df["resolution_hours"] <= df["target_hours"]
    df["within_sla"] = within.where(df["is_closed"] & df["target_hours"].notna()).astype("boolean")
    counts["enrichissement : objectif SLA trouvé"] = int(df["target_hours"].notna().sum())

    for col in ("agency", "descriptor", "incident_zip", "status"):
        df[col] = df[col].astype("string")
    df = df.sort_values(["created_date", "unique_key"]).reset_index(drop=True)
    df["unique_key"] = df["unique_key"].astype("int64")
    return df, counts


class QualityETLAgent(BaseAgent):
    name = "quality_etl"
    output_model = QualityOutput

    def run(self, state: PipelineState) -> QualityOutput:
        profile = state.output("data_profiler")
        raw = load_raw_frame(state.dataset)
        reference = load_reference(state.dataset)
        quarantined: list[pd.DataFrame] = []
        clean, counts = clean_frame(raw, reference["sla_targets"], quarantined)
        workspace = state.require_workspace()
        snapshot_path, snapshot_hash = write_snapshot(clean, workspace, "nyc311_clean")
        quarantine = pd.concat(quarantined, ignore_index=True) if quarantined else pd.DataFrame()
        quarantine_path = None
        if len(quarantine):
            quarantine_path = str(Path(workspace) / "quarantine.parquet")
            quarantine.to_parquet(quarantine_path, index=False)

        notes = []
        if state.dataset.file_path is None:  # le run lit PostgreSQL : on y publie aussi la couche propre
            from biflow.db.engine import get_engine

            schema, table = CLEAN_TABLE.split(".")
            clean.to_sql(table, get_engine(), schema=schema, if_exists="replace", index=False,
                         chunksize=10_000)
            notes.append(f"écrit dans {CLEAN_TABLE}")
        correction = state.corrections.get(self.name)
        if correction:
            notes.append(f"relancé après signalement de l'Auditeur : {correction}")

        rows_in, rows_out = len(raw), len(clean)
        if rows_in != profile["row_count"]:
            notes.append(f"attention : {rows_in} lignes lues, {profile['row_count']} profilées")
        dup_rule = next(k for k in counts if k.startswith("suppression : doublons"))
        return QualityOutput(
            rows_in=rows_in,
            rows_out=rows_out,
            rows_dropped=rows_in - rows_out,
            invalid_dates=counts["suppression : closed_date < created_date"],
            duplicates=counts[dup_rule],
            snapshot_path=snapshot_path,
            snapshot_hash=snapshot_hash,
            quarantine_path=quarantine_path,
            quarantine_rows=len(quarantine),
            columns=list(clean.columns),
            rules_applied=[f"{rule} ({n} lignes)" for rule, n in counts.items()],
            rule_counts=counts,
            input_rows=rows_in,
            output_rows=rows_out,
            notes=notes + [f"{rows_in - rows_out} lignes supprimées, instantané {snapshot_hash[:12]}"],
        )

    def validate(self, output: QualityOutput, state: PipelineState) -> list[Check]:
        return [
            Check(name="rows_out_le_rows_in", passed=output.rows_out <= output.rows_in,
                  message=f"rows_out={output.rows_out}, rows_in={output.rows_in}"),
            Check(name="rows_balance",
                  passed=output.rows_out + output.rows_dropped == output.rows_in,
                  message="rows_out + rows_dropped doit égaler rows_in"),
            Check(name="counts_non_negative",
                  passed=min(output.rows_out, output.rows_dropped,
                             output.invalid_dates, output.duplicates) >= 0,
                  message="compteurs >= 0"),
            Check(name="rows_out_not_empty", passed=output.rows_out > 0,
                  message=f"{output.rows_out} lignes après nettoyage"),
            Check(name="no_free_text", passed=not set(DROPPED_COLUMNS) & set(output.columns),
                  message="aucun champ de texte libre dans la couche propre"),
            Check(name="dropped_rows_quarantined", passed=output.quarantine_rows == output.rows_dropped,
                  message=f"{output.quarantine_rows} lignes en quarantaine pour {output.rows_dropped} "
                          "supprimées"),
            Check(name="rows_match_profile",
                  passed=output.rows_in == state.output("data_profiler")["row_count"],
                  severity="warning", message="lignes lues = lignes profilées"),
        ]
