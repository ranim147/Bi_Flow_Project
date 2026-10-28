"""Exact sample KPIs, separate official volumes, semantic contracts and coverage checks."""
from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd

from .common import inside, now, read, sha, write

CORE_COLUMNS = ["unique_key", "created_date", "closed_date", "created_month", "closed_month", "borough", "agency", "complaint_type", "status", "state", "resolution_days"]


def build_coverage(clean, version, metadata):
    dimensions = ["month", "borough", "complaint_type", "agency"]
    sample = clean.groupby(["created_month", *dimensions[1:]]).size().rename("sample_n").reset_index().rename(columns={"created_month":"month"})
    population = []
    for path in sorted((Path(version) / "raw").glob("*/population_strata.json")):
        month = path.parent.name
        strata = read(path)
        if sum(int(r["request_count"]) for r in strata) != metadata["population_monthly_counts"][month]:
            raise ValueError("Official stratum counts differ from planned monthly totals; review source drift")
        for row in strata:
            labels = {c: " ".join(str(row.get(c) or "__MISSING__").upper().split()) or "__MISSING__" for c in dimensions[1:]}
            labels["borough"] = {"UNSPECIFIED":"__MISSING__", "STATEN IS":"STATEN ISLAND"}.get(labels["borough"], labels["borough"])
            population.append(dict(month=month, **labels, population_n=int(row["request_count"])))
    if not population:
        return pd.DataFrame(columns=[*dimensions, "population_n", "sample_n"])
    # Normalize THEN aggregate: joining the sample onto raw case variants would
    # count its records multiple times. Both sides must have canonical unique keys.
    population = pd.DataFrame(population).groupby(dimensions, as_index=False, dropna=False).population_n.sum()
    result = population.merge(sample, how="left", on=dimensions, validate="one_to_one")
    result["sample_n"] = result.sample_n.fillna(0).astype(int)
    return result


def load_clean(version, columns=CORE_COLUMNS, max_rows=500_000):
    version = Path(version)
    manifest = read(version / "cleaned/manifest.json")
    if sum(p["rows"] for p in manifest["parts"]) > max_rows:
        raise ValueError("Prepared detail exceeds the bounded in-memory analyst contract; narrow scope or use an external query engine")
    frames = []
    for part in manifest["parts"]:
        path = inside(version, part["path"])
        if sha(path) != part["sha256"]:
            raise ValueError("Cleaned partition digest mismatch")
        frames.append(pd.read_parquet(path, columns=columns))
    if not frames:
        raise ValueError("No usable records after ETL")
    result = pd.concat(frames, ignore_index=True)
    if result.memory_usage(deep=True).sum() > 512 * 1024**2:
        raise ValueError("Prepared analyst dataframe exceeds the 512 MiB budget")
    if result.unique_key.isna().any() or not result.unique_key.is_unique:
        raise ValueError("Cleaned key integrity failed")
    return result


def build_kpis(version: Path):
    version = Path(version)
    metadata = read(version / "raw/manifest.json")
    clean = load_clean(version)
    clean.attrs["reporting_timezone"] = metadata.get("source_timezone", "UTC")
    folder = version / "processed"; folder.mkdir(exist_ok=True)
    saved = folder / "manifest.json"
    if saved.exists():
        return validate_processed(version)
    valid = clean.resolution_days.dropna()
    n, closed, opened, inconsistent = len(clean), int(clean.state.eq("closed").sum()), int(clean.state.eq("open").sum()), int(clean.state.eq("inconsistent").sum())
    denominator = int(clean.created_date.notna().sum())
    values = dict(request_volume=float(n), closed_request_count=float(closed), open_request_count=float(opened),
                  inconsistent_request_count=float(inconsistent), mean_resolution_days=None if valid.empty else float(valid.mean()),
                  median_resolution_days=None if valid.empty else float(valid.median()),
                  closure_rate=100 * closed / denominator if denominator else None,
                  invalid_or_missing_resolution_rate=100 * (n - len(valid)) / n)
    summary = pd.DataFrame([dict(kpi_name=k, value=v, unit="jours calendaires" if "resolution_days" in k else "%" if "rate" in k else "demandes",
                                 population_or_denominator=f"prepared cohort {metadata['period_start_inclusive']} / {metadata['period_end_exclusive']}; valid delay n={len(valid)}; valid creation n={denominator}; as_of={metadata['extracted_at_utc']}") for k,v in values.items()])
    tables = {"summary": summary}
    groups = []
    for dimension in ("borough", "complaint_type", "agency"):
        table = clean.groupby(dimension, dropna=False).agg(request_count=("unique_key", "size"), sample_size=("resolution_days", "count"),
                    mean_days=("resolution_days", "mean"), median_days=("resolution_days", "median"), p75_days=("resolution_days", lambda s: s.quantile(.75)),
                    closed_count=("state", lambda s: int(s.eq("closed").sum())), open_count=("state", lambda s: int(s.eq("open").sum())))
        table = table.reset_index().rename(columns={dimension: "group_value"})
        table["dimension"] = dimension
        table["sample_status"] = np.where(table.sample_size.ge(30) & table.group_value.ne("__MISSING__"), "meets_minimum", "small_sample")
        table["kpi_definition"] = "Exact descriptive median/mean over valid observed closed durations; unweighted sample"
        groups.append(table)
    tables["groups"] = pd.concat(groups, ignore_index=True)
    tables["borough"] = tables["groups"].loc[tables["groups"].dimension.eq("borough"), ["group_value", "request_count"]].rename(columns={"group_value": "borough"})
    monthly = clean.groupby("created_month").agg(request_count=("unique_key", "size"), valid_delay_n=("resolution_days", "count"),
                mean_resolution_days=("resolution_days", "mean"), median_resolution_days=("resolution_days", "median"),
                closed_count=("state", lambda s: int(s.eq("closed").sum())), open_count=("state", lambda s: int(s.eq("open").sum()))).reset_index()
    monthly["closure_rate"] = 100 * monthly.closed_count / monthly.request_count
    tables["monthly"] = monthly
    population = metadata.get("population_monthly_counts", {})
    trend = pd.DataFrame([dict(month=m, request_count=int(v), sample_count=int(clean.created_month.eq(m).sum())) for m,v in sorted(population.items()) if m not in metadata.get("partial_months", [])])
    trend["month_start"] = pd.to_datetime(trend.month + "-01")
    trend["change_abs"] = trend.request_count.diff()
    trend["change_pct"] = trend.request_count.pct_change(fill_method=None) * 100
    trend["population_kind"] = "official_population_aggregate"
    tables["trend"] = trend
    tables["distribution"] = pd.DataFrame([dict(quantile=q, resolution_days=None if valid.empty else float(valid.quantile(q)), valid_n=len(valid)) for q in (.05, .25, .5, .75, .9, .95, .99)])
    bins = pd.cut(valid, [-.000001, 1/24, 1, 7, 30, 90, 365, 3650], include_lowest=True)
    tables["histogram"] = bins.value_counts(sort=False).rename_axis("interval_days").reset_index(name="count")
    tables["histogram"]["interval_days"] = tables["histogram"].interval_days.astype(str)
    # Historical snapshots are reconstructions of this created-date cohort only.
    from bi_analyst.analytics import prepare_investigation_frame, backlog_snapshots
    prepared = prepare_investigation_frame(clean, metadata["extracted_at_utc"])
    tables["backlog"] = pd.DataFrame(backlog_snapshots(prepared, tuple(trend.month)))
    tables["coverage"] = build_coverage(clean, version, metadata)
    paths = {}
    for name, table in tables.items():
        path = folder / f"{name}.parquet"
        table.to_parquet(path, index=False, compression="zstd")
        paths[name] = dict(path=path.relative_to(version).as_posix(), rows=len(table), sha256=sha(path))
    catalog = []
    formulas = {
        "request_volume": ("Nombre de demandes de la cohorte préparée", "count(unique nonmissing unique_key)", ["unique_key"], "demandes"),
        "closed_request_count": ("Clôtures observées et cohérentes à l'extraction", "sum(state == 'closed')", ["state", "created_date", "closed_date"], "demandes"),
        "open_request_count": ("Demandes ouvertes observables de la cohorte", "sum(state == 'open')", ["state"], "demandes"),
        "inconsistent_request_count": ("Demandes aux dates ou états non interprétables", "sum(state == 'inconsistent')", ["state"], "demandes"),
        "closure_rate": ("Part clôturée des créations valides de la cohorte à la date d'extraction", "100 * closed_request_count / count(created_date nonmissing)", ["state", "created_date"], "%"),
        "mean_resolution_days": ("Délai calendaire moyen observé des clôtures valides", "mean((closed_utc - created_utc).total_seconds / 86400), valid only", ["created_date", "closed_date", "resolution_days"], "jours calendaires"),
        "median_resolution_days": ("Médiane exacte des mêmes durées valides", "median(resolution_days), not a mean of group medians", ["resolution_days"], "jours calendaires"),
        "invalid_or_missing_resolution_rate": ("Part sans durée valide, y compris demandes ouvertes", "100 * (request_volume - valid_delay_n) / request_volume", ["resolution_days"], "%"),
        "monthly_request_volume": ("Volume mensuel officiel, distinct du détail échantillonné", "official API count(*) grouped by local creation month", ["created_date"], "demandes"),
        "delay_distribution": ("Quantiles exacts et histogramme des durées valides", "quantile(.05,.25,.5,.75,.9,.95,.99); disjoint histogram bins", ["resolution_days"], "jours calendaires"),
        "sample_backlog": ("Stock reconstitué de la seule cohorte échantillonnée", "count(created_date < month_end AND (closed_date IS NULL OR closed_date >= month_end)), coherent rows", ["created_date", "closed_date", "state"], "demandes échantillonnées"),
        "open_age": ("Ancienneté du stock reconstitué, non de toute la ville", "quantile((snapshot_utc-created_utc)/86400, .5,.9)", ["created_date", "closed_date"], "jours calendaires"),
    }
    for name, (definition, formula, columns, unit) in formulas.items():
        catalog.append(dict(kpi_name=name, definition=definition, formula=formula, source_columns=columns, unit=unit,
            period=dict(start=metadata["period_start_inclusive"], end_exclusive=metadata["period_end_exclusive"], as_of=metadata["extracted_at_utc"], timezone=metadata.get("source_timezone", "UTC")),
            allowed_dimensions=["borough", "complaint_type", "agency", "created_month", "closed_month"],
            inclusions="Unique keyed cohort; delay KPI: observable valid dates, nonnegative elapsed duration <=3650 days",
            exclusions="Missing keys and repeated keys excluded from clean rows; invalid/missing durations excluded only from delay denominators; n<30 not ranked",
            limitations=["Unweighted nonrandom systematic sample for detailed durations; no population inference", "Monthly cohort closure rate changes with follow-up time", "Monthly official volumes are not sample volumes", "Agency case mix and missing rare strata limit comparisons", "No causal, staffing or SLA conclusion; backlog excludes pre-cohort requests and unobserved reopenings"],
            data_version=metadata["data_version"], semantic_version="kpi-2.0", provenance=dict(raw_manifest_sha256=sha(version / "raw/manifest.json"), cleaned_manifest_sha256=sha(version / "cleaned/manifest.json"))))
    write(folder / "semantic_catalog.json", catalog)
    manifest = dict(contract_version="biflow-prepared-v2", data_version=metadata["data_version"], data_kind=metadata["data_kind"], generated_at=now(),
                    raw_manifest_sha256=sha(version / "raw/manifest.json"), cleaned_manifest_sha256=sha(version / "cleaned/manifest.json"),
                    semantic_catalog_sha256=sha(folder / "semantic_catalog.json"), files=paths,
                    memory_contract=dict(max_rows=500_000, max_dataframe_bytes=512 * 1024**2),
                    metadata={**{k:v for k,v in metadata.items() if k != "parts"}, "sample_rows":len(clean), "raw_sample_rows": metadata["sample_rows"],
                              "sample_monthly_counts":{str(k):int(v) for k,v in clean.created_month.value_counts().sort_index().items()}},
                    quality=dict(rows=n, valid_delay_n=len(valid), missing_delay_n=n-len(valid), closed_missing_delay_n=int((clean.state.eq("closed") & clean.resolution_days.isna()).sum()),
                                 open_request_n=opened, valid_creation_n=denominator, valid_closure_n=closed, inconsistent_n=inconsistent))
    write(saved, manifest)
    return validate_processed(version)


def validate_processed(version):
    version = Path(version)
    if (version / "invalidated.json").exists():
        raise ValueError("This data version has been invalidated; inspect invalidated.json")
    manifest = read(version / "processed/manifest.json")
    if manifest["cleaned_manifest_sha256"] != sha(version / "cleaned/manifest.json") or manifest["raw_manifest_sha256"] != sha(version / "raw/manifest.json"):
        raise ValueError("Prepared provenance changed")
    if read(version / "cleaned/manifest.json")["profile_sha256"] != sha(version / "profiling/report.json"):
        raise ValueError("Profiling provenance changed")
    if manifest["semantic_catalog_sha256"] != sha(version / "processed/semantic_catalog.json"):
        raise ValueError("Semantic catalogue changed")
    tables = {}
    for name, row in manifest["files"].items():
        path = inside(version, row["path"])
        if sha(path) != row["sha256"]:
            raise ValueError("Prepared KPI digest mismatch: " + name)
        tables[name] = pd.read_parquet(path)
        if len(tables[name]) != row["rows"]:
            raise ValueError("Prepared KPI row count mismatch")
    clean = load_clean(version)
    values = tables["summary"].set_index("kpi_name").value
    valid = clean.resolution_days.dropna()
    for name, actual in (("request_volume", len(clean)), ("closed_request_count", int(clean.state.eq("closed").sum())),
                         ("open_request_count", int(clean.state.eq("open").sum())),
                         ("mean_resolution_days", valid.mean()), ("median_resolution_days", valid.median()),
                         ("closure_rate", clean.state.eq("closed").sum() / clean.created_date.notna().sum() * 100)):
        expected = values.loc[name]
        if not ((pd.isna(expected) and pd.isna(actual)) or np.isclose(expected, actual, atol=1e-9, rtol=0)):
            raise ValueError("Recomputed global KPI mismatch: " + name)
    for dimension in ("borough", "complaint_type", "agency"):
        rows = tables["groups"].loc[tables["groups"].dimension.eq(dimension)]
        if rows.request_count.sum() != len(clean) or rows.sample_size.sum() != len(valid):
            raise ValueError("Dimension denominators do not reconcile: " + dimension)
        actual = clean.groupby(dimension).resolution_days.agg(["count", "median", "mean"])
        for row in rows.itertuples():
            data = actual.loc[row.group_value]
            if row.sample_size != data["count"] or not np.allclose([row.median_days, row.mean_days], [data["median"], data["mean"]], equal_nan=True, atol=1e-9, rtol=0):
                raise ValueError("Recomputed group KPI mismatch")
    if tables["monthly"].request_count.sum() != len(clean) - clean.created_month.isna().sum():
        raise ValueError("Monthly volume denominator mismatch")
    expected_months = sorted(set(manifest["metadata"]["population_monthly_counts"]) - set(manifest["metadata"].get("partial_months", [])))
    if tables["trend"].month.tolist() != expected_months or any(int(r.request_count) != manifest["metadata"]["population_monthly_counts"][r.month] for r in tables["trend"].itertuples()):
        raise ValueError("Official monthly population series mismatch")
    checks = dict(passed=True, checked_at=now(), global_kpis_recomputed=True, group_kpis_recomputed=True,
                  dimensional_denominators_reconciled=True, official_and_sample_volumes_separate=True,
                  raw_rows=manifest["metadata"]["raw_sample_rows"], cleaned_rows=len(clean), valid_delay_n=len(valid),
                  complete_periods=len(expected_months), in_memory_dataframe_bytes=int(clean.memory_usage(deep=True).sum()))
    coverage = tables["coverage"]
    if not coverage.empty:
        if coverage.duplicated(["month","borough","complaint_type","agency"]).any() or coverage.sample_n.sum() != clean.created_month.notna().sum() or coverage.population_n.sum() != sum(manifest["metadata"]["population_monthly_counts"].values()):
            raise ValueError("Coverage canonical keys or denominators do not reconcile")
        checks["joint_strata_coverage"] = dict(strata=len(coverage), observed_strata=int(coverage.sample_n.gt(0).sum()),
            missing_strata=int(coverage.sample_n.eq(0).sum()), population_share_in_observed_strata=float(coverage.loc[coverage.sample_n.gt(0), "population_n"].sum() / coverage.population_n.sum()))
    write(version / "processed/verification.json", checks)
    return manifest
