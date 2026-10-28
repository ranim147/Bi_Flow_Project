"""Strict bridge from the versioned Parquet contract to existing BI Analyst tools."""
from pathlib import Path
import re

import pandas as pd

from .common import read, sha
from .kpi import load_clean, validate_processed


def version_path(root, name):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Invalid dataset version identifier")
    return Path(root).resolve() / "data/versions" / name


def load_prepared_bundle(root, name):
    root = Path(root).resolve()
    version = version_path(root, name)
    manifest = validate_processed(version)
    if manifest["data_kind"] != "real":
        raise ValueError("Synthetic prepared inputs cannot enter the real BI Analyst")
    clean = load_clean(version)
    clean.attrs["reporting_timezone"] = manifest["metadata"].get("source_timezone", "UTC")
    clean["month"] = clean.created_month
    tables = {key: pd.read_parquet(version / manifest["files"][key]["path"]) for key in ("summary", "borough", "monthly", "groups", "trend")}
    # Existing delay tools do not accept empty/NaN group summaries as evidence.
    tables["groups"] = tables["groups"].loc[tables["groups"].sample_size.gt(0)].copy()
    tables["groups"]["sample_status"] = tables["groups"].sample_size.map(lambda n: "meets_minimum" if n >= 30 else "small_sample")
    paths = {key: (version / manifest["files"][key]["path"]).relative_to(root).as_posix() for key in tables}
    paths["clean"] = (version / "cleaned/manifest.json").relative_to(root).as_posix()
    paths["metadata"] = (version / "processed/manifest.json").relative_to(root).as_posix()
    return dict(kind="real", clean=clean, **tables, metadata=manifest["metadata"], quality=manifest["quality"],
                source_paths=paths, hashes={path: sha(root / path) for path in paths.values()},
                warnings=manifest["metadata"].get("limitations", []), prepared_manifest=manifest)
