"""Création des schémas/tables et données de référence (idempotent).

Usage : python -m biflow.db.init_db
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from biflow.db.engine import get_engine
from biflow.db.models import SCHEMAS, boroughs, metadata, sla_targets

log = logging.getLogger(__name__)

BOROUGH_SEED = [
    {"borough": "BRONX", "borough_label": "Bronx"},
    {"borough": "BROOKLYN", "borough_label": "Brooklyn"},
    {"borough": "MANHATTAN", "borough_label": "Manhattan"},
    {"borough": "QUEENS", "borough_label": "Queens"},
    {"borough": "STATEN ISLAND", "borough_label": "Staten Island"},
]

_PLACEHOLDER_NOTE = "TO BE DEFINED - valeur provisoire, à valider par l'équipe KPI"
SLA_SEED = [
    {"complaint_type": ct, "target_hours": h, "is_placeholder": True, "note": _PLACEHOLDER_NOTE}
    for ct, h in [
        ("Noise - Residential", 8),
        ("Noise - Street/Sidewalk", 8),
        ("Noise - Commercial", 8),
        ("Noise - Vehicle", 8),
        ("Street Condition", 240),
        ("Street Light Condition", 240),
    ]
]


def init_db(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        metadata.create_all(conn)
        # Seeds : n'écrase jamais une valeur déjà saisie par l'équipe
        conn.execute(insert(boroughs).values(BOROUGH_SEED).on_conflict_do_nothing())
        conn.execute(insert(sla_targets).values(SLA_SEED).on_conflict_do_nothing())
    log.info("Base initialisée : schémas %s", ", ".join(SCHEMAS))


if __name__ == "__main__":
    from biflow.common.logging import setup_logging

    setup_logging()
    init_db()
