"""Lecture du catalogue des KPI (`kpi_catalog.yaml`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

CATALOG_PATH = Path(__file__).with_name("kpi_catalog.yaml")

Unit = Literal["count", "hours", "percent", "ratio", "per_10k"]


class KPIDefinition(BaseModel):
    name: str
    unit: Unit
    definition: str
    sql: str


class BreakdownDefinition(KPIDefinition):
    dimension: str


class Catalog(BaseModel):
    table_schema: dict[str, dict[str, str]]
    kpis: list[KPIDefinition]
    breakdowns: list[BreakdownDefinition]

    def kpi(self, name: str) -> KPIDefinition:
        return next(k for k in self.kpis if k.name == name)

    def schema_text(self) -> str:
        """Schéma lisible (envoyé au LLM : aucune donnée, seulement les colonnes)."""
        return "\n".join(
            f"TABLE {table}(\n" + ",\n".join(f"  {col} -- {desc}" for col, desc in cols.items()) + "\n)"
            for table, cols in self.table_schema.items()
        )


@lru_cache
def load_catalog(path: str | Path = CATALOG_PATH) -> Catalog:
    with open(path, encoding="utf-8") as f:
        return Catalog.model_validate(yaml.safe_load(f))
