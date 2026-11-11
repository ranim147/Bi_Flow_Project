"""Création des schémas/tables et données de référence (idempotent).

Usage : python -m biflow.db.init_db
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from biflow.common.reference import BOROUGH_SEED, SLA_SEED
from biflow.db.engine import get_engine
from biflow.db.models import SCHEMAS, boroughs, metadata, sla_targets

log = logging.getLogger(__name__)


def init_db(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        metadata.create_all(conn)
        # Seeds : n'écrase jamais une valeur déjà saisie par l'équipe
        stmt = insert(boroughs).values(BOROUGH_SEED)
        # population : complétée seulement si elle n'a pas encore été saisie
        conn.execute(stmt.on_conflict_do_update(
            index_elements=[boroughs.c.borough],
            set_={c: stmt.excluded[c] for c in ("population", "population_year", "population_source")},
            where=boroughs.c.population.is_(None),
        ))
        conn.execute(insert(sla_targets).values(SLA_SEED).on_conflict_do_nothing())
    log.info("Base initialisée : schémas %s", ", ".join(SCHEMAS))


if __name__ == "__main__":
    from biflow.common.logging import setup_logging

    setup_logging()
    init_db()
