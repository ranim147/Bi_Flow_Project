"""CLI d'ingestion : télécharge une tranche de NYC 311, la sauvegarde dans data/raw/ et
l'upsert dans raw.nyc311.

Exemples :
    python -m biflow.ingestion                         # 12 derniers mois, max 200 000 lignes
    python -m biflow.ingestion --max-rows 5000
    python -m biflow.ingestion --start 2025-01-01 --end 2025-03-31 \
        --complaint-types "Noise - Residential" "Street Condition"
    python -m biflow.ingestion --no-db --format csv    # fichier seulement
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta

import pandas as pd

from biflow.common.config import RAW_DIR, get_settings
from biflow.common.logging import setup_logging
from biflow.db.engine import get_engine
from biflow.db.init_db import init_db
from biflow.db.loader import upsert_nyc311
from biflow.ingestion.socrata import (
    DEFAULT_COMPLAINT_TYPES,
    fetch_records,
    records_to_frame,
)
from biflow.orchestrator.validation import check_raw_frame

log = logging.getLogger("biflow.ingestion")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    today = date.today()
    p = argparse.ArgumentParser(description="Ingestion NYC 311 (Socrata) -> data/raw + raw.nyc311")
    p.add_argument("--start", type=date.fromisoformat, default=today - timedelta(days=365),
                   help="Date de début incluse (AAAA-MM-JJ). Défaut : il y a 12 mois")
    p.add_argument("--end", type=date.fromisoformat, default=today,
                   help="Date de fin incluse (AAAA-MM-JJ). Défaut : aujourd'hui")
    p.add_argument("--complaint-types", nargs="+", default=DEFAULT_COMPLAINT_TYPES,
                   help="Types exacts ou préfixes terminés par * (défaut : 'Noise*' 'Street*')")
    p.add_argument("--max-rows", type=int, default=200_000, help="Plafond de lignes (défaut 200000)")
    p.add_argument("--page-size", type=int, default=50_000)
    p.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    p.add_argument("--no-db", action="store_true", help="Ne pas charger dans PostgreSQL")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)
    settings = get_settings()
    log.info("Extraction %s -> %s, types=%s, max=%d",
             args.start, args.end, args.complaint_types, args.max_rows)

    pages = fetch_records(args.start, args.end, args.complaint_types, args.max_rows,
                          page_size=args.page_size, app_token=settings.socrata_app_token)
    frames = [records_to_frame(page) for page in pages]
    df = pd.concat(frames, ignore_index=True) if frames else records_to_frame([])
    log.info("%d lignes extraites", len(df))

    for check in check_raw_frame(df):
        level = logging.INFO if check.passed else logging.WARNING
        log.log(level, "Contrôle %s : %s", check.name, check.message)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RAW_DIR / f"nyc311_{args.start}_{args.end}_{stamp}.{args.format}"
    if args.format == "parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)
    log.info("Extrait sauvegardé : %s", path)

    if not args.no_db:
        engine = get_engine()
        init_db(engine)
        result = upsert_nyc311(engine, df)
        log.info("raw.nyc311 : %d nouvelles lignes, %d mises à jour", result.inserted, result.updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
