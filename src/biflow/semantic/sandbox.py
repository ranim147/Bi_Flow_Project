"""Exécution SQL sûre (DuckDB) sur l'instantané d'un run.

Garanties, pour le SQL du catalogue comme pour le SQL généré par le LLM :
- base DuckDB en mémoire contenant une COPIE des tables autorisées : rien à abîmer ;
- accès aux fichiers désactivé et configuration verrouillée après le chargement ;
- une seule instruction, de type SELECT, sur les tables autorisées uniquement ;
- durée et nombre de lignes limités (le SQL du catalogue, relu dans Git, a une limite de
  lignes plus haute : une série journalière sur plusieurs années dépasse 1 000 lignes).
"""

from __future__ import annotations

import datetime as dt
import math
import threading
from decimal import Decimal
from typing import Any

import duckdb
import pandas as pd

from biflow.common.config import get_settings

ALLOWED_TABLES = ("requests", "boroughs", "sla_targets")
CATALOG_MAX_ROWS = 100_000


class UnsafeQueryError(ValueError):
    """Requête refusée avant exécution (pas un SELECT, table non autorisée...)."""


def _jsonable(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):  # scalaires numpy
        return value.item()
    return value


class SQLSandbox:
    def __init__(self, tables: dict[str, pd.DataFrame], max_rows: int | None = None,
                 timeout_s: float | None = None) -> None:
        unknown = set(tables) - set(ALLOWED_TABLES)
        if unknown:
            raise ValueError(f"tables non autorisées : {sorted(unknown)}")
        settings = get_settings()
        self.max_rows = max_rows or settings.sql_max_rows
        self.timeout_s = timeout_s or settings.sql_timeout_seconds
        self.tables = set(tables)
        self.conn = duckdb.connect(":memory:")
        for name, df in tables.items():
            self.conn.register("_src", df)
            self.conn.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
            self.conn.unregister("_src")
        self.conn.execute("SET enable_external_access = false")
        self.conn.execute("SET python_enable_replacements = false")
        self.conn.execute("SET lock_configuration = true")

    @classmethod
    def from_snapshot(cls, snapshot_path: str, reference: dict[str, pd.DataFrame],
                      **kwargs: Any) -> SQLSandbox:
        return cls({"requests": pd.read_parquet(snapshot_path), **reference}, **kwargs)

    def check(self, sql: str) -> None:
        try:
            statements = duckdb.extract_statements(sql)
        except duckdb.Error as exc:
            raise UnsafeQueryError(f"SQL invalide : {exc}") from exc
        if len(statements) != 1:
            raise UnsafeQueryError(f"une seule instruction attendue ({len(statements)} reçues)")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise UnsafeQueryError(f"seul SELECT est autorisé (reçu {statements[0].type.name})")
        try:
            used = {t.split(".")[-1].lower() for t in self.conn.get_table_names(sql)}
        except duckdb.PermissionException as exc:  # lecture de fichier (read_csv...) : refusée
            raise UnsafeQueryError(f"accès aux fichiers interdit : {exc}") from exc
        forbidden = used - self.tables
        if forbidden:
            raise UnsafeQueryError(f"tables non autorisées : {sorted(forbidden)}")

    def query(self, sql: str, max_rows: int | None = None) -> list[dict[str, Any]]:
        """Exécute un SELECT contrôlé et retourne les lignes (valeurs JSON-sérialisables)."""
        max_rows = max_rows or self.max_rows
        sql = sql.strip().rstrip(";")
        self.check(sql)
        timer = threading.Timer(self.timeout_s, self.conn.interrupt)
        timer.start()
        try:
            rel = self.conn.execute(f"SELECT * FROM ({sql}) AS q LIMIT {max_rows + 1}")
            columns = [d[0] for d in rel.description]
            rows = rel.fetchall()
        except duckdb.InterruptException as exc:
            raise TimeoutError(f"requête interrompue après {self.timeout_s} s") from exc
        finally:
            timer.cancel()
        if len(rows) > max_rows:
            raise UnsafeQueryError(f"plus de {max_rows} lignes retournées")
        return [{c: _jsonable(v) for c, v in zip(columns, row, strict=True)} for row in rows]

    def scalar(self, sql: str) -> Any:
        rows = self.query(sql)
        if len(rows) != 1 or len(rows[0]) != 1:
            raise ValueError("la requête doit retourner une seule valeur")
        return next(iter(rows[0].values()))

    def close(self) -> None:
        self.conn.close()
