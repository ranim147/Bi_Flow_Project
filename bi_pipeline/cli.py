"""Reproducible stage controller. No LLM is used for acquisition, profiling, ETL or KPIs."""
from __future__ import annotations

import argparse
from pathlib import Path
import time
import psutil

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .acquire import AcquisitionConfig, acquire
from .common import log, now, read, sha, write
from .kpi import build_kpis
from .loader import version_path
from .quality import etl, names, profile, parse_dates


def import_local(root, name, csv, metadata_path, *, timezone="UTC"):
    version = version_path(root, name)
    if (version / "raw/manifest.json").exists():
        raise FileExistsError("Local import must create a new data version")
    metadata = read(metadata_path)
    parts, monthly = [], {}
    for index, frame in enumerate(pd.read_csv(csv, dtype="string", chunksize=5000)):
        frame = names(frame)
        output = version / "raw/local" / f"part-{index:05d}.parquet"
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), output, compression="zstd")
        parts.append(dict(path=output.relative_to(version).as_posix(), rows=len(frame), sha256=sha(output)))
        for month, count in parse_dates(frame.created_date, timezone).dt.tz_convert(timezone).dt.strftime("%Y-%m").value_counts().items():
            monthly[str(month)] = monthly.get(str(month), 0) + int(count)
    if sum(p["rows"] for p in parts) != metadata["sample_rows"] or monthly != metadata["sample_monthly_counts"]:
        raise ValueError("Local raw file does not match the source metadata's row/month counts; no version is published")
    write(version / "raw/manifest.json", {**metadata, "data_kind": metadata.get("data_kind", "real"), "data_version": name, "source_timezone": timezone,
          "source_csv": str(Path(csv).resolve()), "source_csv_sha256":sha(csv), "source_metadata_sha256":sha(metadata_path),
          "imported_at_utc":now(), "parts":parts, "sample_rows":sum(p["rows"] for p in parts),
          "limitations":["Local immutable import of the earlier window-sampled extract; not a new remote extraction."]})
    return version


def prepare(root, name):
    version = version_path(root, name)
    journal = version / "pipeline_audit.jsonl"
    for stage, function in (("profiling", profile), ("etl", etl), ("kpi", build_kpis)):
        started = time.perf_counter()
        log(journal, "stage_started", stage=stage, data_version=name)
        try:
            value = function(version)
            memory = psutil.Process().memory_info()
            log(journal, "stage_completed", stage=stage, elapsed_seconds=time.perf_counter()-started,
                resident_bytes=memory.rss, process_peak_working_set_bytes=getattr(memory, "peak_wset", None))
        except KeyboardInterrupt:
            log(journal, "stage_interrupted", stage=stage, elapsed_seconds=time.perf_counter()-started)
            raise
        except Exception as exc:
            log(journal, "stage_failed", stage=stage, error_type=type(exc).__name__, error=str(exc))
            raise
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["acquire", "import-local", "prepare", "run", "activate"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True)
    parser.add_argument("--start", default="2023-01")
    parser.add_argument("--end", default="2026-10", help="Exclusive creation month")
    parser.add_argument("--suffix-count", type=int, default=2)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args()
    root = args.root.resolve(); version = version_path(root, args.version)
    if args.command in {"acquire", "run"}:
        acquire(version, AcquisitionConfig(start=args.start, end=args.end, suffix_count=args.suffix_count))
    if args.command == "import-local":
        if not args.csv or not args.metadata:
            parser.error("import-local requires --csv and --metadata")
        import_local(root, args.version, args.csv, args.metadata, timezone=args.timezone)
    if args.command in {"prepare", "run", "import-local"}:
        result = prepare(root, args.version)
        print(f"Prepared {args.version}: {result['quality']['rows']} requests", flush=True)
    if args.command == "activate":
        from .loader import load_prepared_bundle
        bundle = load_prepared_bundle(root, args.version)
        write(root / "data/active_dataset.json", dict(dataset_version=args.version, activated_at=now(),
              processed_manifest_sha256=sha(version / "processed/manifest.json"), rows=len(bundle["clean"])))
        print(f"Streamlit live dataset activated: {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
