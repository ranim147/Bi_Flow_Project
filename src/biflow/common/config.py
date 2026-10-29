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
    # LLM : auto (Mistral si clé, sinon aucun) | mistral | ollama | none ; sans LLM -> baseline
    llm_provider: str = "auto"
    mistral_api_key: str | None = None
    mistral_model: str = "mistral-small-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"
    ollama_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434/v1"
    # Sécurité / RGPD
    min_group_size: int = 10          # seuil anti-identification : agrégat < N demandes masqué
    sql_max_rows: int = 1000          # SQL généré : lignes max retournées
    sql_timeout_seconds: float = 10.0  # SQL généré : durée max

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
        llm_provider=(os.getenv("BIFLOW_LLM_PROVIDER") or "auto").lower(),
        mistral_api_key=os.getenv("MISTRAL_API_KEY") or None,
        mistral_model=os.getenv("MISTRAL_MODEL") or "mistral-small-latest",
        mistral_base_url=os.getenv("MISTRAL_BASE_URL") or "https://api.mistral.ai/v1",
        ollama_model=os.getenv("OLLAMA_MODEL") or "qwen2.5:7b",
        ollama_base_url=os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1",
        min_group_size=_env_int("BIFLOW_MIN_GROUP_SIZE", 10),
        sql_max_rows=_env_int("BIFLOW_SQL_MAX_ROWS", 1000),
        sql_timeout_seconds=_env_float("BIFLOW_SQL_TIMEOUT_SECONDS", 10.0),
    )
