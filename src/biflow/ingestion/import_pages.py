"""Import d'un extrait NYC 311 déjà téléchargé en pages parquet/CSV (ex. dossier `raw/` d'une
autre acquisition) : un seul fichier brut dans data/raw + sa fiche de provenance.

    python -m biflow.ingestion.import_pages <dossier ou fichiers> --name nyc311_2023-01_2026-09 \
        --source "..." --note "échantillon systématique d'environ 2 % ..."

Les valeurs ne sont pas modifiées (texte brut, tel que reçu) : le nettoyage reste le travail
de l'agent Qualité / ETL. Seules les colonnes de `RAW_COLUMNS` présentes sont gardées.
La fiche `<fichier>.meta.json` (source, empreinte, réserves) est affichée par le dashboard.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from biflow.common.config import RAW_DIR
from biflow.common.data import metadata_path
from biflow.common.logging import setup_logging
from biflow.common.schema import RAW_COLUMNS, REQUIRED_COLUMNS
from biflow.common.snapshot import file_hash

log = logging.getLogger("biflow.ingestion.import_pages")


def find_pages(inputs: list[str]) -> list[Path]:
    pages: list[Path] = []
    for item in map(Path, inputs):
        if item.is_dir():
            pages += sorted(p for p in item.rglob("*") if p.suffix in (".parquet", ".csv"))
        else:
            pages.append(item)
    return pages


def read_page(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    return df[[c for c in RAW_COLUMNS if c in df.columns]]


def import_pages(inputs: list[str], name: str, out_dir: Path = RAW_DIR, source: str = "",
                 notes: list[str] | None = None) -> Path:
    pages = find_pages(inputs)
    if not pages:
        raise FileNotFoundError(f"aucune page parquet/CSV dans {inputs}")
    frames = [read_page(p) for p in pages]
    df = pd.concat(frames, ignore_index=True).astype("string")
    missing = sorted(set(REQUIRED_COLUMNS) - set(df.columns))
    if missing:
        raise ValueError(f"colonnes obligatoires absentes : {missing}")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.parquet"
    df.to_parquet(path, index=False)
    created = pd.to_datetime(df["created_date"], errors="coerce", format="mixed")
    meta = {
        "name": name,
        "source": source,
        "imported_at": datetime.now(UTC).isoformat(),
        "pages": len(pages),
        "rows": len(df),
        "columns": list(df.columns),
        "absent_columns": [c for c in RAW_COLUMNS if c not in df.columns],
        "created_min": str(created.min()),
        "created_max": str(created.max()),
        "file_sha256": file_hash(path),
        "notes": notes or [],
    }
    metadata_path(path).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("%d lignes (%d pages) -> %s", len(df), len(pages), path)
    return path


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(description="Importe des pages NYC 311 brutes en un extrait data/raw")
    p.add_argument("inputs", nargs="+", help="Dossier(s) ou fichier(s) parquet/CSV")
    p.add_argument("--name", required=True, help="Nom du fichier produit (sans extension)")
    p.add_argument("--out-dir", default=str(RAW_DIR))
    p.add_argument("--source", default="", help="Origine des données (texte libre)")
    p.add_argument("--note", action="append", default=[], help="Réserve à afficher (répétable)")
    args = p.parse_args(argv)
    path = import_pages(args.inputs, args.name, Path(args.out_dir), args.source, args.note)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
