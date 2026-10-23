"""Configuration centralisée, lue depuis les variables d'environnement / le fichier .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

load_dotenv(PROJECT_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_test_db: str
    socrata_app_token: str | None
    max_retries: int
    retry_delay_seconds: float
    max_feedback_rounds: int
    log_level: str

    def database_url(self, db_name: str | None = None) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{db_name or self.postgres_db}"
        )

    @property
    def test_database_url(self) -> str:
        return self.database_url(self.postgres_test_db)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        postgres_user=os.getenv("POSTGRES_USER", "biflow"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", ""),
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=_env_int("POSTGRES_PORT", 5432),
        postgres_db=os.getenv("POSTGRES_DB", "biflow"),
        postgres_test_db=os.getenv("POSTGRES_TEST_DB", "biflow_test"),
        socrata_app_token=os.getenv("SOCRATA_APP_TOKEN") or None,
        max_retries=_env_int("BIFLOW_MAX_RETRIES", 2),
        retry_delay_seconds=_env_float("BIFLOW_RETRY_DELAY_SECONDS", 1.0),
        max_feedback_rounds=_env_int("BIFLOW_MAX_FEEDBACK_ROUNDS", 1),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
