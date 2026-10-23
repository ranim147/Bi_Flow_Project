"""Création de l'engine SQLAlchemy et test de connexion."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from biflow.common.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url(), pool_pre_ping=True)


def can_connect(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
