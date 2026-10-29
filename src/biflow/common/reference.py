"""Données de référence (boroughs, objectifs SLA) : valeurs d'amorçage et chargement.

Les mêmes valeurs servent à amorcer `ref.*` (init_db) et de repli quand le run lit un
extrait parquet/CSV sans PostgreSQL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from biflow.orchestrator.state import DatasetRef

_CENSUS = "US Census Bureau, recensement décennal 2020"
BOROUGH_SEED = [
    {"borough": "BRONX", "borough_label": "Bronx", "population": 1_472_654},
    {"borough": "BROOKLYN", "borough_label": "Brooklyn", "population": 2_736_074},
    {"borough": "MANHATTAN", "borough_label": "Manhattan", "population": 1_694_251},
    {"borough": "QUEENS", "borough_label": "Queens", "population": 2_405_464},
    {"borough": "STATEN ISLAND", "borough_label": "Staten Island", "population": 495_747},
]
for _b in BOROUGH_SEED:
    _b.update(population_year=2020, population_source=_CENSUS)

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


def load_reference(dataset: DatasetRef) -> dict[str, pd.DataFrame]:
    """`{"boroughs": ..., "sla_targets": ...}` : depuis `ref.*` si le run lit la base,
    sinon (extrait fichier) depuis les valeurs d'amorçage."""
    if dataset.file_path is None:
        from biflow.db.engine import get_engine

        with get_engine().connect() as conn:
            boroughs = pd.read_sql("SELECT * FROM ref.boroughs", conn)
            sla = pd.read_sql("SELECT * FROM ref.sla_targets", conn)
        # NUMERIC arrive en Decimal : types numériques explicites pour pandas et DuckDB
        boroughs["population"] = boroughs["population"].astype("float64")
        sla["target_hours"] = sla["target_hours"].astype("float64")
        return {"boroughs": boroughs, "sla_targets": sla}
    return {"boroughs": pd.DataFrame(BOROUGH_SEED), "sla_targets": pd.DataFrame(SLA_SEED)}
