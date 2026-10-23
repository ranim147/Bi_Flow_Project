"""Schéma des données brutes NYC 311 — contrat partagé par l'ingestion et les agents."""

from __future__ import annotations

# Colonnes demandées à l'API Socrata ($select) et stockées dans raw.nyc311
# (hors `ingested_at`, ajoutée par la base).
RAW_COLUMNS: list[str] = [
    "unique_key",
    "created_date",
    "closed_date",
    "agency",
    "complaint_type",
    "descriptor",
    "incident_zip",
    "borough",
    "status",
    "resolution_description",
    "latitude",
    "longitude",
]

DATETIME_COLUMNS = ["created_date", "closed_date"]
FLOAT_COLUMNS = ["latitude", "longitude"]
TEXT_COLUMNS = [
    c for c in RAW_COLUMNS if c not in {"unique_key", *DATETIME_COLUMNS, *FLOAT_COLUMNS}
]

RAW_TABLE = "raw.nyc311"

# Boroughs officiels (valeurs telles qu'elles apparaissent dans la colonne `borough`)
BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]
