"""Schéma de la base BIFlow (source de vérité, SQLAlchemy Core).

Schémas PostgreSQL :
- raw       : données brutes telles que reçues de l'API (aucune transformation métier)
- processed : sorties de l'agent Qualité/ETL (vide pour l'instant)
- ref       : tables de référence (boroughs, objectifs SLA)
- audit     : traçabilité du pipeline (utilisée par l'agent Auditeur/XAI)

Les dates NYC 311 sont des timestamps « flottants » (heure locale de New York, sans fuseau)
-> stockées en TIMESTAMP WITHOUT TIME ZONE.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

SCHEMAS = ["raw", "processed", "ref", "audit"]

metadata = MetaData()

nyc311 = Table(
    "nyc311",
    metadata,
    Column("unique_key", BigInteger, primary_key=True, autoincrement=False),
    Column("created_date", DateTime, index=True),
    Column("closed_date", DateTime),
    Column("agency", Text),
    Column("complaint_type", Text, index=True),
    Column("descriptor", Text),
    Column("incident_zip", Text),
    Column("borough", Text, index=True),
    Column("status", Text),
    Column("resolution_description", Text),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema="raw",
    comment="Demandes 311 brutes (API Socrata erm2-nwe9), upsert sur unique_key",
)

boroughs = Table(
    "boroughs",
    metadata,
    Column("borough", Text, primary_key=True, comment="Valeur telle que dans raw.nyc311.borough"),
    Column("borough_label", Text, nullable=False),
    Column("population", Integer, nullable=True, comment="A compléter (source + année)"),
    Column("population_year", Integer, nullable=True),
    Column("population_source", Text, nullable=True),
    schema="ref",
)

sla_targets = Table(
    "sla_targets",
    metadata,
    Column("complaint_type", Text, primary_key=True),
    Column("target_hours", Numeric, nullable=False),
    Column("is_placeholder", Boolean, nullable=False, server_default="true"),
    Column("note", Text),
    schema="ref",
    comment="Délai cible par type de plainte. Valeurs actuelles = PLACEHOLDERS (TO BE DEFINED)",
)

trace_log = Table(
    "trace_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", Text, nullable=False, index=True),
    Column("step", Text, nullable=False, comment="Nom de l'agent / étape (ou __run__)"),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("status", Text, nullable=False, comment="success | failed | skipped | ..."),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True)),
    Column("input_rows", BigInteger),
    Column("output_rows", BigInteger),
    Column("message", Text),
    Column("details", JSONB),
    schema="audit",
    comment="Une ligne par tentative d'étape du pipeline (traçabilité pour l'Auditeur/XAI)",
)
