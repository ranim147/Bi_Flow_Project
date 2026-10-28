"""Chunked profiling and deterministic ETL, preserving rejected records and reason codes."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import gc
import uuid

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .common import inside, log, now, read, sha, write

REQUIRED = {"unique_key", "created_date", "closed_date", "borough", "complaint_type", "agency", "status"}
POLICY = dict(version="etl-2.0", minimum_group_n=30, maximum_resolution_days=3650,
              review_resolution_days=90, max_missing_key_fraction=.01,
              max_invalid_creation_fraction=.01, max_temporal_conflict_fraction=.01,
              duplicate_rule="First occurrence in sorted immutable raw part order; all later occurrences retained in quarantine")


def discard_scratch(database, version):
    gc.collect()
    try:
        database.unlink(missing_ok=True)
    except PermissionError:
        # Windows can briefly retain a SQLite/antivirus handle. Cleanup is not
        # a reason to invalidate already verified analytics; retain and log it.
        log(Path(version) / "pipeline_audit.jsonl", "scratch_cleanup_deferred", file=str(database))


def names(frame):
    aliases = {"problem": "complaint_type", "problem_detail": "descriptor"}
    columns = [aliases.get(re.sub(r"[^a-z0-9]+", "_", str(c).strip().casefold()).strip("_"),
                           re.sub(r"[^a-z0-9]+", "_", str(c).strip().casefold()).strip("_")) for c in frame.columns]
    if len(set(columns)) != len(columns):
        raise ValueError("Column normalization creates a collision")
    result = frame.copy(); result.columns = columns
    return result


def parse_dates(values, timezone):
    values = values.astype("string").str.strip().replace("", pd.NA)
    aware = values.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True, na=False)
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    if aware.any():
        result.loc[aware] = pd.to_datetime(values.loc[aware], errors="coerce", format="mixed", utc=True)
    if (~aware).any():
        parsed = pd.to_datetime(values.loc[~aware], errors="coerce", format="mixed")
        result.loc[~aware] = parsed.dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    return result


def raw_parts(version):
    manifest = read(Path(version) / "raw/manifest.json")
    for row in manifest["parts"]:
        path = inside(version, row["path"])
        if sha(path) != row["sha256"]:
            raise ValueError("Raw input digest mismatch: " + row["path"])
        offset = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=5000):
            frame = names(batch.to_pandas())
            frame.attrs["source_row_offset"] = offset
            yield frame, row["path"]
            offset += len(frame)


def fingerprint(row):
    return hashlib.sha256(json.dumps([None if pd.isna(v) else str(v) for v in row], ensure_ascii=False).encode()).hexdigest()


def transform(frame, metadata):
    frame = names(frame)
    missing = REQUIRED - set(frame)
    if missing:
        raise ValueError("Required columns absent: " + str(sorted(missing)))
    for c in frame:
        frame[c] = frame[c].astype("string").str.strip().replace("", pd.NA)
    for c in ("borough", "agency", "complaint_type"):
        frame[c + "_raw"] = frame[c]
        frame[c] = frame[c].str.replace(r"\s+", " ", regex=True).str.upper()
    frame["borough"] = frame["borough"].replace({"STATEN IS": "STATEN ISLAND", "STATEN ISLAND ": "STATEN ISLAND", "UNSPECIFIED": "__MISSING__"}).fillna("__MISSING__")
    for c in ("agency", "complaint_type"):
        frame[c] = frame[c].fillna("__MISSING__")
    frame["status"] = frame.status.str.upper()
    timezone = metadata.get("source_timezone", "America/New_York")
    for c in ("created_date", "closed_date"):
        frame[c + "_raw"] = frame[c]
        frame[c] = parse_dates(frame[c], timezone)
        frame[c + "_parse_error"] = frame[c + "_raw"].notna() & frame[c].isna()
    cutoff = pd.to_datetime(metadata["extracted_at_utc"], utc=True)
    duration = (frame.closed_date - frame.created_date).dt.total_seconds() / 86400
    frame["temporal_inconsistency"] = duration.lt(0)
    frame["future_creation"] = frame.created_date.gt(cutoff)
    frame["future_closure"] = frame.closed_date.gt(cutoff)
    frame["duration_suspicious"] = duration.gt(POLICY["maximum_resolution_days"]) | duration.lt(0)
    frame["long_duration_review"] = duration.gt(POLICY["review_resolution_days"])
    closed = frame.created_date.notna() & frame.closed_date.notna() & duration.ge(0) & ~frame.future_creation & ~frame.future_closure
    inconsistent = (frame.created_date.isna() | frame.temporal_inconsistency | frame.future_creation | frame.future_closure
                    | frame.closed_date_parse_error | (frame.status.eq("CLOSED").fillna(False) & frame.closed_date.isna()))
    frame["state"] = np.select([inconsistent.to_numpy(dtype=bool, na_value=False), closed.to_numpy(dtype=bool, na_value=False)], ["inconsistent", "closed"], default="open")
    valid = closed & ~frame.duration_suspicious & ~inconsistent
    frame["resolution_days"] = duration.where(valid)
    frame["created_month"] = frame.created_date.dt.tz_convert(timezone).dt.strftime("%Y-%m")
    frame["closed_month"] = frame.closed_date.dt.tz_convert(timezone).dt.strftime("%Y-%m")
    issue_columns = ["created_date_parse_error", "closed_date_parse_error", "temporal_inconsistency", "future_creation", "future_closure", "duration_suspicious", "long_duration_review"]
    reasons = []
    for row in frame.to_dict("records"):
        codes = [c for c in issue_columns if row[c]]
        if pd.isna(row["unique_key"]): codes.append("missing_key")
        if pd.isna(row["created_date"]): codes.append("missing_or_invalid_creation")
        if row["state"] == "inconsistent": codes.append("inconsistent_state")
        if any(row[c] == "__MISSING__" for c in ("borough", "agency", "complaint_type")): codes.append("missing_dimension")
        reasons.append("|".join(codes))
    frame["quality_reasons"] = reasons
    return frame


def profile(version: Path):
    version = Path(version)
    metadata = read(version / "raw/manifest.json")
    folder = version / "profiling"; folder.mkdir(exist_ok=True)
    if (folder / "report.json").exists():
        previous = read(folder / "report.json")
        if previous["input_manifest_sha256"] != sha(version / "raw/manifest.json") or previous["policy"] != POLICY:
            raise ValueError("Profiling inputs/policy changed; create a new version")
        for part in metadata["parts"]:
            if sha(inside(version, part["path"])) != part["sha256"]:
                raise ValueError("Raw input digest mismatch")
        previous["counts"].setdefault("exact_duplicate_rows", 0)
        previous["counts"].setdefault("duplicate_key_rows", 0)
        return previous
    database = folder / ("profile_work_" + uuid.uuid4().hex + ".sqlite")
    connection = sqlite3.connect(database)
    # Disposable indexes are rebuilt after interruption; raw data and manifests
    # remain durable. A bounded cache avoids random disk I/O for every hash.
    connection.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-65536;")
    connection.executescript("CREATE TABLE fingerprints (digest TEXT PRIMARY KEY); CREATE TABLE keys (key TEXT PRIMARY KEY);")
    counts, missing, types, distributions = Counter(dict(rows=0, exact_duplicate_rows=0, duplicate_key_rows=0)), Counter(), {}, {c: Counter() for c in ("borough", "complaint_type", "agency", "status", "created_month", "created_day")}
    minimum, maximum = None, None
    try:
        for raw, path in raw_parts(version):
            counts["rows"] += len(raw)
            for c in raw:
                types.setdefault(c, set()).add(str(raw[c].dtype))
                missing[c] += int((raw[c].isna() | raw[c].astype("string").str.strip().eq("").fillna(False)).sum())
            for values in raw.itertuples(index=False, name=None):
                if not connection.execute("INSERT OR IGNORE INTO fingerprints VALUES (?)", (fingerprint(values),)).rowcount:
                    counts["exact_duplicate_rows"] += 1
            clean = transform(raw, metadata)
            clean["created_day"] = clean.created_date.dt.tz_convert(metadata.get("source_timezone", "UTC")).dt.strftime("%Y-%m-%d")
            for key in clean.unique_key.dropna():
                if not connection.execute("INSERT OR IGNORE INTO keys VALUES (?)", (str(key),)).rowcount:
                    counts["duplicate_key_rows"] += 1
            counts["missing_key_rows"] += int(clean.unique_key.isna().sum())
            for c in ("created_date_parse_error", "closed_date_parse_error", "temporal_inconsistency", "future_creation", "future_closure", "duration_suspicious", "long_duration_review"):
                counts[c] += int(clean[c].sum())
            counts["invalid_creation_rows"] += int(clean.created_date.isna().sum())
            for c in distributions:
                distributions[c].update(clean[c].fillna("__MISSING__").value_counts().to_dict())
            dates = clean.created_date.dropna()
            if len(dates):
                left, right = dates.min().isoformat(), dates.max().isoformat()
                minimum = min(minimum or left, left); maximum = max(maximum or right, right)
            connection.commit()
            if counts["rows"] % 50000 < len(raw):
                log(version / "pipeline_audit.jsonl", "profiling_progress", rows=counts["rows"])
    finally:
        connection.close()
    alerts = []
    if counts["rows"] != metadata["sample_rows"]:
        alerts.append(dict(code="raw_manifest_row_mismatch", expected=metadata["sample_rows"], observed=counts["rows"], severity="critical"))
    for key, threshold in (("missing_key_rows", POLICY["max_missing_key_fraction"]), ("invalid_creation_rows", POLICY["max_invalid_creation_fraction"]), ("temporal_inconsistency", POLICY["max_temporal_conflict_fraction"])):
        fraction = counts[key] / max(counts["rows"], 1)
        if counts[key]:
            alerts.append(dict(code=key, observed_fraction=fraction, critical_threshold=threshold, severity="critical" if fraction > threshold else "warning"))
    partial = set(metadata.get("partial_months", []))
    cutoff = pd.to_datetime(metadata["extracted_at_utc"], utc=True).tz_convert(metadata.get("source_timezone", "UTC"))
    for month in distributions["created_month"]:
        if month != "__MISSING__" and (pd.Period(month, "M") + 1).start_time.tz_localize(cutoff.tzinfo) > cutoff:
            partial.add(month)
    days = [day for day in distributions["created_day"] if day != "__MISSING__"]
    daily = {month: dict(observed_days=sum(day.startswith(month) for day in days), calendar_days=pd.Period(month, "M").days_in_month)
             for month in sorted(distributions["created_month"]) if month != "__MISSING__"}
    result = dict(version="profiler-2.0", generated_at=now(), input_manifest_sha256=sha(version / "raw/manifest.json"),
                  policy=POLICY, counts=dict(counts), columns=len(types), types={c: sorted(t) for c,t in types.items()},
                  missing={c: dict(count=missing[c], percent=100 * missing[c] / max(counts["rows"], 1)) for c in types},
                  distributions={c: dict(v) for c,v in distributions.items()}, temporal_coverage=dict(minimum=minimum, maximum=maximum, partial_months=sorted(partial), sampled_daily_coverage=daily),
                  alerts=alerts, quality_gate="fail" if any(a["severity"] == "critical" for a in alerts) else "pass")
    write(folder / "report.json", result)
    discard_scratch(database, version)
    return result


def etl(version: Path):
    version = Path(version)
    metadata = read(version / "raw/manifest.json")
    profiling = read(version / "profiling/report.json")
    if profiling["input_manifest_sha256"] != sha(version / "raw/manifest.json") or profiling["quality_gate"] != "pass":
        raise ValueError("Profiling failed or refers to different raw inputs")
    folder = version / "cleaned"; folder.mkdir(exist_ok=True)
    rejected = version / "quarantine"; rejected.mkdir(exist_ok=True)
    if (folder / "manifest.json").exists():
        saved = read(folder / "manifest.json")
        if saved["raw_manifest_sha256"] != sha(version / "raw/manifest.json") or saved["profile_sha256"] != sha(version / "profiling/report.json"):
            raise ValueError("ETL version input changed")
        for part in [*saved["parts"], *saved["quarantine_parts"]]:
            if sha(inside(version, part["path"])) != part["sha256"]:
                raise ValueError("ETL output integrity mismatch")
        return saved
    database = folder / ("etl_work_" + uuid.uuid4().hex + ".sqlite")
    connection = sqlite3.connect(database)
    connection.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-65536; CREATE TABLE keys (key TEXT PRIMARY KEY);")
    counts, parts, quarantine = Counter(), [], []
    try:
        for index, (raw, path) in enumerate(raw_parts(version)):
            clean = transform(raw, metadata)
            duplicate = []
            for key in clean.unique_key:
                duplicate.append(False if pd.isna(key) else not connection.execute("INSERT OR IGNORE INTO keys VALUES (?)", (str(key),)).rowcount)
            duplicate = pd.Series(duplicate, index=clean.index)
            clean.loc[duplicate, "quality_reasons"] += "|duplicate_key"
            clean["source_part"] = path
            clean["source_row"] = np.arange(len(clean)) + raw.attrs.get("source_row_offset", 0)
            drop = duplicate | clean.unique_key.isna()
            issue = clean.quality_reasons.ne("")
            counts.update(input_rows=len(clean), cleaned_rows=int((~drop).sum()), excluded_rows=int(drop.sum()), review_rows=int(issue.sum()))
            for subset, target, collection in ((clean.loc[~drop], folder, parts), (clean.loc[issue], rejected, quarantine)):
                if subset.empty: continue
                output = target / f"part-{index:05d}.parquet"
                pq.write_table(pa.Table.from_pandas(subset, preserve_index=False), output, compression="zstd")
                collection.append(dict(path=output.relative_to(version).as_posix(), rows=len(subset), sha256=sha(output)))
            connection.commit()
            if counts["input_rows"] % 50000 < len(raw):
                log(version / "pipeline_audit.jsonl", "etl_progress", rows=counts["input_rows"])
    finally:
        connection.close()
    if counts["input_rows"] != counts["cleaned_rows"] + counts["excluded_rows"]:
        raise ValueError("ETL row reconciliation failed")
    result = dict(version="etl-2.0", generated_at=now(), raw_manifest_sha256=sha(version / "raw/manifest.json"),
                  profile_sha256=sha(version / "profiling/report.json"), policy=POLICY, counts=dict(counts), parts=parts, quarantine_parts=quarantine)
    write(folder / "manifest.json", result)
    discard_scratch(database, version)
    return result
