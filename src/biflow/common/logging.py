"""Configuration du logging console."""

from __future__ import annotations

import logging
import sys

from biflow.common.config import get_settings


def setup_logging(level: str | None = None) -> None:
    # Console Windows (cp1252) : forcer l'UTF-8 pour les accents
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=(level or get_settings().log_level).upper(),
        format="%(asctime)s %(levelname)-7s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logue chaque requête en INFO : trop verbeux pour la pagination
    logging.getLogger("httpx").setLevel(logging.WARNING)
