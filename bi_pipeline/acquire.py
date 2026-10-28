"""Bounded anonymous SODA2 acquisition, monthly checkpoints and keyset pagination."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
import shutil
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from .common import inside, log, now, read, sha, write

HOST = "https://data.cityofnewyork.us"
DATASET = "erm2-nwe9"
COLUMNS = ["unique_key", "created_date", "closed_date", "borough", "complaint_type", "agency",
           "agency_name", "status", "descriptor", "resolution_action_updated_date", "open_data_channel_type"]


@dataclass(frozen=True)
class AcquisitionConfig:
    start: str = "2023-01"
    end: str = "2026-10"  # exclusive; client calendar, not the host's local clock
    suffix_count: int = 2  # two systematic positions per 100 request identifiers
    seed: int = 42
    page_size: int = 5000
    max_rows: int = 500_000
    max_bytes: int = 512 * 1024 * 1024

    def validate(self):
        if not (0 < self.suffix_count <= 100 and 1 <= self.page_size <= 10000):
            raise ValueError("Invalid sampling or batch limit")
        periods = pd.period_range(self.start, pd.Period(self.end, "M") - 1, freq="M")
        if not len(periods) or len(periods) > 120:
            raise ValueError("Acquisition must cover 1–120 explicitly bounded months")
        return [str(p) for p in periods]


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def month_where(month):
    right = str(pd.Period(month, "M") + 1)
    return f"created_date >= '{month}-01T00:00:00' AND created_date < '{right}-01T00:00:00'"


def sample_predicate(month, boroughs, config):
    clauses = []
    for borough in boroughs:
        offset = int(hashlib.sha256(f"{config.seed}|{month}|{borough}".encode()).hexdigest()[:8], 16) % 100
        positions = sorted({(offset + index * (100 // config.suffix_count)) % 100 for index in range(config.suffix_count)})
        suffixes = " OR ".join(f"unique_key like '%{position:02d}'" for position in positions)
        group = "borough IS NULL" if borough is None else "borough=" + literal(borough)
        clauses.append(f"({group} AND ({suffixes}))")
    return "(" + " OR ".join(clauses) + ")"


class Socrata:
    def __init__(self, journal: Path, *, session=None):
        self.journal = journal
        self.session = session or requests.Session()

    def get(self, path, params=None):
        for attempt in range(4):
            started = time.perf_counter()
            try:
                response = self.session.get(HOST + path, params=params, timeout=(10, 60), headers={"User-Agent": "BIFlow-academic-pipeline/2.0"})
                if response.status_code in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                if len(response.content) > 12 * 1024 * 1024:
                    raise ValueError("Response exceeds bounded page budget")
                value = response.json()
                log(self.journal, "http_success", url=response.url, response_sha256=hashlib.sha256(response.content).hexdigest(),
                    bytes=len(response.content), elapsed_seconds=time.perf_counter() - started)
                return value
            except requests.RequestException as exc:
                log(self.journal, "http_failure", request_path=path, parameters=params, attempt=attempt + 1, error=str(exc)[:500])
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)

    def query(self, **params):
        result = self.get(f"/resource/{DATASET}.json", {"$" + key: value for key, value in params.items()})
        if not isinstance(result, list):
            raise ValueError("SODA query did not return records")
        return result


def plan_acquisition(destination: Path, config: AcquisitionConfig, client: Socrata):
    months = config.validate()
    destination.mkdir(parents=True, exist_ok=True)
    old = destination / "acquisition_plan.json"
    if old.exists():
        saved = read(old)
        if saved["config"] != asdict(config):
            raise ValueError("A dataset version cannot resume with a different acquisition configuration")
        return saved
    schema = client.get(f"/api/views/{DATASET}.json")
    fields = {c["fieldName"]: c["dataTypeName"] for c in schema["columns"]}
    missing = set(COLUMNS) - fields.keys()
    if missing:
        raise ValueError("Upstream schema changed; required fields missing: " + str(sorted(missing)))
    if fields["unique_key"] != "text" or fields["created_date"] != "calendar_date":
        raise ValueError("Unexpected key/date source types")
    write(destination / "raw/source_schema.json", schema)
    source_summary = client.query(select="count(*) as rows")
    period = f"created_date >= '{config.start}-01T00:00:00' AND created_date < '{config.end}-01T00:00:00'"
    count_path = destination / "raw/planning_counts.json"
    cached = read(count_path) if count_path.exists() else {}
    if cached.get("source_revision") != schema.get("rowsUpdatedAt") or cached.get("months") != months:
        cached = dict(source_revision=schema.get("rowsUpdatedAt"), months=months, counts={})
    counts = cached["counts"]
    def count_month(month):
        return month, int(client.query(select="count(*) as request_count", where=month_where(month))[0]["request_count"])
    # Smaller indexed date-range queries avoid a costly unbounded source scan.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(count_month, month) for month in months if month not in counts]):
            month, count = future.result()
            counts[month] = count
            write(count_path, cached)
    counts = dict(sorted(counts.items()))
    if set(counts) != set(months):
        raise ValueError("Missing real source months; refusing to fabricate or fill them")
    expected = math.ceil(sum(counts.values()) * config.suffix_count / 100 * 1.3)
    disk = shutil.disk_usage(destination)
    if expected > config.max_rows or expected * 1000 > config.max_bytes or disk.free < max(2 * 1024**3, expected * 4000):
        raise ValueError("Acquisition exceeds row/transfer/free-disk budget; narrow the scope or sampling fraction")
    plan = dict(config=asdict(config), planned_at_utc=now(), source_dataset=DATASET, source_summary=source_summary,
                source_revision=schema.get("rowsUpdatedAt"), source_schema_sha256=sha(destination / "raw/source_schema.json"),
                source_timezone="America/New_York", months=months, population_monthly_counts=counts,
                expected_rows_upper_planning_estimate=expected, free_disk_bytes=disk.free,
                full_source_estimate_bytes_projected_columns=int(source_summary[0]["rows"]) * 500,
                strategy="Month/borough stratified systematic selection by seeded identifier suffixes; not a probability sample or a population estimate",
                limits=["Identifier periodicity and assignment processes may bias systematic selection.",
                        "Rare category/agency strata may be absent; coverage is measured against official joint aggregates.",
                        "API requests are not a transactionally frozen snapshot; changes are recorded.",
                        "No historical status or staffing/capacity data is present."])
    write(old, plan)
    return plan


def acquire_month(destination, month, config, client):
    folder = destination / "raw" / month
    folder.mkdir(parents=True, exist_ok=True)
    checkpoint = folder / "checkpoint.json"
    if checkpoint.exists():
        saved = read(checkpoint)
        for page in saved["pages"]:
            if sha(inside(destination, page["path"])) != page["sha256"]:
                raise ValueError("Raw checkpoint integrity failed")
        if saved.get("complete"):
            return saved
    else:
        saved = dict(month=month, pages=[], complete=False)
    strata_path = folder / "population_strata.json"
    if not strata_path.exists():
        strata = client.query(select="borough,complaint_type,agency,count(*) as request_count", where=month_where(month),
                              group="borough,complaint_type,agency", order="borough,complaint_type,agency", limit=50000)
        if len(strata) >= 50000:
            raise ValueError("Population stratum query reached its limit; cannot assume completeness")
        write(strata_path, strata)
    strata = read(strata_path)
    boroughs = sorted({r.get("borough") for r in strata}, key=lambda s: s or "")
    predicate = month_where(month) + " AND " + sample_predicate(month, boroughs, config)
    if "expected_rows" not in saved:
        saved["expected_rows"] = int(client.query(select="count(*) as rows", where=predicate)[0]["rows"])
        saved["predicate"] = predicate
    if saved["expected_rows"] > config.max_rows:
        raise ValueError("Monthly sample exceeds the entire version's row budget")
    cursor = saved["pages"][-1]["last_key"] if saved["pages"] else None
    count = sum(p["rows"] for p in saved["pages"])
    while count < saved["expected_rows"]:
        where = predicate + (" AND unique_key > " + literal(cursor) if cursor else "")
        rows = client.query(select=",".join(COLUMNS), where=where, order="unique_key", limit=config.page_size)
        if not rows:
            raise ValueError("Source changed or pagination truncated before expected count")
        keys = [r.get("unique_key") for r in rows]
        if any(not key for key in keys) or keys != sorted(set(keys)) or (cursor and keys[0] <= cursor):
            raise ValueError("Nonunique or unstable source keyset")
        page = folder / f"part-{len(saved['pages']):05d}.parquet"
        if page.exists():
            raise ValueError("Uncommitted raw page exists; inspect it before resuming")
        table = pa.Table.from_pylist([{c: r.get(c) for c in COLUMNS} for r in rows], schema=pa.schema([(c, pa.string()) for c in COLUMNS]))
        temporary = page.with_suffix(".tmp")
        pq.write_table(table, temporary, compression="zstd")
        temporary.replace(page)
        saved["pages"].append(dict(path=page.relative_to(destination).as_posix(), sha256=sha(page), rows=len(rows), last_key=keys[-1]))
        cursor = keys[-1]; count += len(rows)
        write(checkpoint, saved)
    if count != saved["expected_rows"]:
        raise ValueError("Downloaded row count differs from sampled source count")
    saved.update(complete=True, completed_at_utc=now(), population_strata_sha256=sha(strata_path))
    write(checkpoint, saved)
    print(f"Acquired {month}: {count} requests", flush=True)
    return saved


def acquire(destination: Path, config=AcquisitionConfig(), *, workers=3, client=None):
    destination = Path(destination).resolve()
    client = client or Socrata(destination / "acquisition_audit.jsonl")
    plan = plan_acquisition(destination, config, client)
    if (destination / "raw/manifest.json").exists():
        manifest = read(destination / "raw/manifest.json")
        for row in manifest["parts"]:
            if sha(inside(destination, row["path"])) != row["sha256"]:
                raise ValueError("Raw integrity mismatch on offline reuse")
        return manifest
    if workers < 1 or workers > 3:
        raise ValueError("Use 1–3 workers to bound requests and memory")
    checkpoints = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(acquire_month, destination, month, config, client) for month in plan["months"]]
        for future in as_completed(pending):
            checkpoints.append(future.result())
            if sum(sum(p["rows"] for p in c["pages"]) for c in checkpoints) > config.max_rows:
                raise ValueError("Cumulative row budget exceeded")
    parts = [p for c in sorted(checkpoints, key=lambda c: c["month"]) for p in c["pages"]]
    if sum(inside(destination, p["path"]).stat().st_size for p in parts) > config.max_bytes:
        raise ValueError("Raw stored data exceeds configured byte budget")
    schema_end = client.get(f"/api/views/{DATASET}.json")
    manifest = dict(data_version=destination.name, data_kind="real", source_url=HOST + "/resource/" + DATASET + ".json",
                    extracted_at_utc=now(), extraction_started_at_utc=plan["planned_at_utc"],
                    source_timezone=plan["source_timezone"], source_revision_start=plan["source_revision"],
                    source_revision_end=schema_end.get("rowsUpdatedAt"), source_changed_during_extraction=plan["source_revision"] != schema_end.get("rowsUpdatedAt"),
                    period_start_inclusive=config.start + "-01", period_end_exclusive=config.end + "-01",
                    sample_method=plan["strategy"], sample_rows=sum(p["rows"] for p in parts),
                    sample_monthly_counts={c["month"]: sum(p["rows"] for p in c["pages"]) for c in checkpoints},
                    population_monthly_counts=plan["population_monthly_counts"], partial_months=[], parts=parts,
                    config=asdict(config), limitations=plan["limits"])
    write(destination / "raw/manifest.json", manifest)
    return manifest
