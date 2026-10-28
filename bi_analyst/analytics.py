from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from benchmark_llm.data import FILES, DataValidationError, load_bundle

MIN_REFERENCE_N = 6
Z_THRESHOLD = 3.0
MAD_THRESHOLD = 3.5
MIN_GROUP_N = 30
MIN_STL_MONTHS = 24
MIN_MONTHS_ANOMALY = 6
MIN_TREND_MONTHS = 36
TREND_ALPHA = 0.05
ROBUST_Z_THRESHOLD = 3.5
ZERO_MAD_RELATIVE_THRESHOLD = 0.20
RULE_VERSION = "biflow-bi-analyst-rules-1.0"


def prepare_investigation_frame(frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Normalize timestamps without converting open/invalid durations to zero.

    Only observed closures at extraction time are usable. Recomputed durations
    are checked against any supplied value; invalid rows remain for quality counts.
    """
    required = {"unique_key", "created_date", "closed_date", "borough"}
    if not required.issubset(frame):
        raise ValueError("Investigation columns missing: " + ",".join(sorted(required - set(frame))))
    data = frame.copy()
    if data.unique_key.isna().any() or not data.unique_key.is_unique:
        raise ValueError("Investigation requires unique nonmissing request keys.")
    cutoff = pd.Timestamp(as_of)
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    data["created_date"] = pd.to_datetime(data.created_date, utc=True, errors="coerce")
    data["closed_date"] = pd.to_datetime(data.closed_date, utc=True, errors="coerce")
    data = data.loc[data.created_date.isna() | data.created_date.le(cutoff)].copy()
    # A future closure is not observable at the extraction timestamp.
    data.loc[data.closed_date.gt(cutoff), "closed_date"] = pd.NaT
    actual = (data.closed_date - data.created_date).dt.total_seconds() / 86400
    supplied = pd.to_numeric(data.get("resolution_days", actual), errors="coerce")
    valid = (actual.ge(0) & np.isfinite(actual) & supplied.notna()
             & np.isclose(actual, supplied, atol=1e-7, rtol=0))
    data["valid_delay"] = valid
    data["resolution_days"] = actual.where(valid)
    reporting_timezone = frame.attrs.get("reporting_timezone", "UTC")
    data["closure_month"] = data.closed_date.dt.tz_convert(reporting_timezone).dt.strftime("%Y-%m")
    data["creation_month"] = data.created_date.dt.tz_convert(reporting_timezone).dt.strftime("%Y-%m")
    for dimension in ("borough", "complaint_type", "agency"):
        if dimension not in data:
            data[dimension] = "__MISSING__"
        data[dimension] = data[dimension].fillna("__MISSING__").astype(str)
    return data


def select_comparison_periods(metadata: dict[str, Any]) -> tuple[str, str] | None:
    """Last two contiguous fully observed months, without substituting a gapped pair."""
    cutoff = pd.Timestamp(metadata["extracted_at_utc"])
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    start = pd.Timestamp(metadata["period_start_inclusive"])
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = pd.Timestamp(metadata["period_end_exclusive"])
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    available = metadata.get("sample_monthly_counts", {})
    excluded = set(metadata.get("partial_months", []))
    complete = []
    for month in sorted(available):
        period = pd.Period(month, freq="M")
        left = period.start_time.tz_localize("UTC")
        right = (period + 1).start_time.tz_localize("UTC")
        if month not in excluded and left >= start and right <= min(end, cutoff):
            complete.append(month)
    if len(complete) < 2:
        return None
    previous, current = complete[-2:]
    return (previous, current) if pd.Period(current, "M") - 1 == pd.Period(previous, "M") else None


def compare_delay_periods(data: pd.DataFrame, periods: tuple[str, str],
                          dimensions: Sequence[str] = ("borough",),
                          min_n: int = MIN_GROUP_N) -> pd.DataFrame:
    """Completed-work view: durations of requests closed during each month.

    This is a period-to-period descriptive difference, not a trend or a causal test.
    Volumes are counts in the prepared sample, never population demand estimates.
    """
    previous, current = periods
    selected = data.loc[data.closure_month.isin(periods)].copy()
    records = []
    for keys, group in selected.groupby(list(dimensions), dropna=False, sort=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(dimensions, keys))
        for suffix, month in (("baseline", previous), ("current", current)):
            closed = group.loc[group.closure_month.eq(month)]
            valid = closed.loc[closed.valid_delay, "resolution_days"]
            row.update({f"n_{suffix}": len(valid), f"closed_{suffix}": len(closed),
                        f"missing_{suffix}": int((~closed.valid_delay).sum()),
                        f"mean_{suffix}": None if valid.empty else float(valid.mean()),
                        f"median_{suffix}": None if valid.empty else float(valid.median())})
        eligible = min(row["n_baseline"], row["n_current"]) >= min_n
        row["eligible"] = eligible and all(value != "__MISSING__" for value in keys)
        row["delta_mean_days"] = (row["mean_current"] - row["mean_baseline"]
                                  if row["mean_baseline"] is not None and row["mean_current"] is not None else None)
        row["deterioration_observed"] = bool(row["eligible"] and row["delta_mean_days"] > 1e-10)
        records.append(row)
    return pd.DataFrame(records)


def decompose_delay_change(data: pd.DataFrame, periods: tuple[str, str],
                           dimensions: Sequence[str], min_n: int = MIN_GROUP_N) -> dict[str, Any]:
    """Exact symmetric decomposition of a MEAN (not of a median).

    For common groups: within = (w0+w1)/2 * (m1-m0),
    composition = (m0+m1)/2 * (w1-w0). New/exited groups have an
    explicit unmatched contribution; no unobserved mean is imputed.
    All valid rows enter the identity, including small groups. Small groups,
    unknown labels and unmatched groups are excluded from operational ranking.
    """
    table = compare_delay_periods(data, periods, dimensions, min_n)
    population = data.loc[data.valid_delay & data.closure_month.isin(periods)]
    counts = [int(population.closure_month.eq(month).sum()) for month in periods]
    if table.empty or min(counts) == 0:
        return dict(status="insufficient_data", groups=[], delta_mean_days=None,
                    within_days=None, composition_days=None, unmatched_days=None, residual_days=None)
    rows = []
    for row in table.to_dict("records"):
        for suffix in ("baseline", "current"):
            if row[f"n_{suffix}"] == 0:
                row[f"mean_{suffix}"] = row[f"median_{suffix}"] = None
        if min(row["n_baseline"], row["n_current"]) == 0:
            row["delta_mean_days"] = None
        w0, w1 = row["n_baseline"] / counts[0], row["n_current"] / counts[1]
        m0, m1 = row["mean_baseline"], row["mean_current"]
        common = row["n_baseline"] > 0 and row["n_current"] > 0
        within = (w0 + w1) / 2 * (m1 - m0) if common else 0.0
        composition = (m0 + m1) / 2 * (w1 - w0) if common else 0.0
        unmatched = 0.0 if common else (w1 * m1 if row["n_current"] else -w0 * m0)
        coverage = min(row["n_baseline"] / max(row["closed_baseline"], 1),
                       row["n_current"] / max(row["closed_current"], 1))
        contribution = within + composition + unmatched
        # Volume already enters through the shares; sqrt(n) adds a disclosed
        # evidence-strength weight rather than estimating an operational benefit.
        priority = max(contribution, 0) * math.sqrt(min(row["n_baseline"], row["n_current"])) * coverage
        row.update(weight_baseline=w0, weight_current=w1, within_days=within,
                   composition_days=composition, unmatched_days=unmatched,
                   contribution_days=contribution, evidence_coverage=coverage,
                   rankable=bool(row["eligible"] and common),
                   priority_score=priority if row["eligible"] and common else 0.0)
        rows.append(row)
    means = [float(population.loc[population.closure_month.eq(month), "resolution_days"].mean()) for month in periods]
    within = sum(row["within_days"] for row in rows)
    composition = sum(row["composition_days"] for row in rows)
    unmatched = sum(row["unmatched_days"] for row in rows)
    delta = means[1] - means[0]
    residual = delta - within - composition - unmatched
    if not math.isclose(residual, 0, abs_tol=1e-9):
        raise ValueError("Contribution decomposition does not reconcile.")
    return dict(status="computed", dimensions=list(dimensions), baseline=periods[0], current=periods[1],
                mean_baseline=means[0], mean_current=means[1], n_baseline=counts[0], n_current=counts[1],
                delta_mean_days=delta, within_days=within, composition_days=composition,
                unmatched_days=unmatched, residual_days=residual,
                groups=sorted(rows, key=lambda row: (-row["priority_score"], str([row[d] for d in dimensions]))))


def backlog_snapshots(data: pd.DataFrame, periods: tuple[str, str]) -> list[dict[str, Any]]:
    """Reconstructed backlog among sampled requests, excluding later arrivals.

    Historical closing timestamps do not establish reopening/status history.
    Missing creation dates cannot be assigned an age and are counted separately.
    """
    rows = []
    for month in periods:
        cutoff = (pd.Period(month, "M") + 1).start_time.tz_localize(data.attrs.get("reporting_timezone", "UTC")).tz_convert("UTC")
        arrived = data.created_date.lt(cutoff)
        if "state" in data:
            arrived &= ~data.state.eq("inconsistent")
        opened = data.loc[arrived & (data.closed_date.isna() | data.closed_date.ge(cutoff))]
        ages = (cutoff - opened.created_date).dt.total_seconds() / 86400
        rows.append(dict(period=month, backlog_n=len(opened),
                         age_median_days=None if ages.empty else float(ages.median()),
                         age_p90_days=None if ages.empty else float(ages.quantile(.9)),
                         incoming_sample_n=int(data.creation_month.eq(month).sum()),
                         unknown_creation_n=int(data.created_date.isna().sum())))
    return rows


def agency_comparability(data: pd.DataFrame, periods: tuple[str, str], min_n: int = MIN_GROUP_N) -> list[dict[str, Any]]:
    """Case-mix overlap is diagnostic, never a causal adjustment or an agency ranking."""
    selected = data.loc[data.valid_delay & data.closure_month.eq(periods[1])]
    counts = selected.groupby(["agency", "complaint_type"]).size()
    agencies = sorted(selected.agency.unique())
    rows = []
    for left_index, left in enumerate(agencies):
        for right in agencies[left_index + 1:]:
            a, b = counts.loc[left], counts.loc[right]
            categories = set(a.index) | set(b.index)
            overlap = sum(min(float(a.get(key, 0) / a.sum()), float(b.get(key, 0) / b.sum())) for key in categories)
            common = [key for key in categories if a.get(key, 0) >= min_n and b.get(key, 0) >= min_n and key != "__MISSING__"]
            rows.append(dict(agency_left=left, agency_right=right, category_overlap=float(overlap),
                             eligible_common_categories=sorted(common), directly_comparable=False,
                             reason="Raw agency averages do not control case mix; use common categories and periods."))
    return rows


def load_real_bundle(project_root: Path, dataset_version: str | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if dataset_version is not None:
        from bi_pipeline.loader import load_prepared_bundle
        return load_prepared_bundle(root, dataset_version)
    missing = [relative for relative in FILES.values() if not (root / relative).is_file()]
    if missing:
        raise DataValidationError(
            "Real NYC 311 inputs are incomplete; synthetic fallback is disabled: "
            + ", ".join(missing)
        )
    bundle = load_bundle(root)
    if bundle["kind"] != "real":
        raise DataValidationError("Expected real data; refusing a synthetic bundle.")
    return bundle


def compare_group_delays(
    frame: pd.DataFrame,
    dimensions: Sequence[str],
    min_n: int = MIN_GROUP_N,
    catalog: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if not {"resolution_days", "closed_date"}.issubset(frame.columns):
        return pd.DataFrame()
    population = frame.loc[
        frame["closed_date"].notna()
        & frame["resolution_days"].notna()
        & (frame["resolution_days"] >= 0)
    ]
    formula = "Descriptive median of valid closed-request resolution_days."
    if (
        catalog is not None
        and {"kpi_name", "formula"}.issubset(catalog.columns)
        and catalog["kpi_name"].eq("median_resolution_by_group").any()
    ):
        formula = str(
            catalog.loc[
                catalog["kpi_name"].eq("median_resolution_by_group"), "formula"
            ].iloc[0]
        )
    tables = []
    for dimension in dimensions:
        if dimension not in population:
            continue
        table = (
            population.dropna(subset=[dimension])
            .groupby(dimension)["resolution_days"]
            .agg(
                sample_size="size",
                mean_days="mean",
                median_days="median",
                p75_days=lambda values: values.quantile(0.75),
            )
            .reset_index()
        )
        table["dimension"] = dimension
        table["group_value"] = table[dimension].astype(str)
        table["kpi_definition"] = formula
        table["rank_median_desc"] = table["median_days"].rank(
            method="min", ascending=False
        ).astype(int)
        table["sample_status"] = np.where(
            table["sample_size"] >= min_n, "meets_minimum", "small_sample"
        )
        tables.append(table.drop(columns=[dimension]))
    return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()


def compare_group_volumes(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    if dimension not in frame or "unique_key" not in frame:
        return pd.DataFrame()
    population = frame.dropna(subset=[dimension])
    result = (
        population.groupby(dimension)["unique_key"]
        .nunique()
        .rename("sample_request_count")
        .reset_index()
    )
    result["dimension"] = dimension
    result["group_value"] = result[dimension].astype(str)
    result["dataset_n"] = len(frame)
    return result.drop(columns=[dimension]).sort_values(
        "sample_request_count", ascending=False
    )


def monthly_trend_table(monthly: pd.DataFrame | None) -> pd.DataFrame:
    if monthly is None or monthly.empty:
        return pd.DataFrame()
    required = {"month_start", "request_count"}
    if not required.issubset(monthly.columns):
        raise ValueError(f"Monthly table requires columns: {sorted(required)}")
    trend = monthly.copy().sort_values("month_start").reset_index(drop=True)
    trend["change_abs"] = trend["request_count"].diff()
    trend["change_pct"] = trend["request_count"].pct_change() * 100
    trend["rolling_mean_3"] = trend["request_count"].rolling(3, min_periods=3).mean()
    trend["change_direction"] = np.select(
        [trend["change_abs"] > 0, trend["change_abs"] < 0],
        ["up", "down"],
        default="flat",
    )
    return trend


def persistent_trend(
    trend_table: pd.DataFrame | None,
    min_periods: int = 4,
    required_same_direction: int = 3,
) -> dict[str, Any]:
    if trend_table is None or len(trend_table) < min_periods:
        return {
            "status": "insufficient_periods",
            "direction": "none",
            "n_periods": 0 if trend_table is None else len(trend_table),
        }
    if "month" not in trend_table:
        raise ValueError("Trend table requires a month column.")
    months = pd.PeriodIndex(trend_table["month"].astype(str), freq="M")
    expected_months = pd.period_range(months.min(), months.max(), freq="M")
    if not months.is_unique or not months.equals(expected_months):
        return {
            "status": "irregular_periods",
            "direction": "none",
            "n_periods": len(trend_table),
        }
    if required_same_direction < 1:
        raise ValueError("required_same_direction must be positive.")
    differences = trend_table["request_count"].diff().dropna()
    signs = np.sign(differences.to_numpy())
    if len(signs) < required_same_direction:
        return {
            "status": "insufficient_periods",
            "direction": "none",
            "n_periods": len(trend_table),
        }
    recent_signs = signs[-required_same_direction:]
    up = bool(np.all(recent_signs > 0))
    down = bool(np.all(recent_signs < 0))
    direction = "up" if up else "down" if down else "none"
    first = float(trend_table.iloc[0]["request_count"])
    last = float(trend_table.iloc[-1]["request_count"])
    return {
        "status": "persistent" if direction != "none" else "no_persistent_direction",
        "direction": direction,
        "n_periods": int(len(trend_table)),
        "start_period": str(trend_table.iloc[0]["month"]),
        "end_period": str(trend_table.iloc[-1]["month"]),
        "start_value": first,
        "end_value": last,
        "change_abs": last - first,
        "change_pct": (last / first - 1) * 100 if first else np.nan,
        "required_same_direction": required_same_direction,
    }


def mann_kendall_trend(
    table: pd.DataFrame | None,
    value_col: str = "request_count",
    *,
    min_periods: int = MIN_TREND_MONTHS,
    alpha: float = TREND_ALPHA,
) -> dict[str, Any]:
    """Test monotonic trend within calendar months, controlling for seasonality."""
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "direction": "none",
        "n_periods": 0 if table is None else len(table),
        "tau": None,
        "z_score": None,
        "p_value": None,
        "alpha": float(alpha),
        "method": (
            "Seasonal Mann-Kendall by calendar month; normal approximation "
            "with tie correction; serial autocorrelation not adjusted"
        ),
        "start_period": None,
        "end_period": None,
    }
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1.")
    if table is None or table.empty:
        return result
    if not {"month", value_col}.issubset(table.columns):
        raise ValueError(f"Trend test requires columns month and {value_col}.")

    ordered = table.sort_values("month").reset_index(drop=True)
    periods = pd.PeriodIndex(ordered["month"].astype(str), freq="M")
    result["n_periods"] = len(periods)
    result["start_period"] = str(periods.min())
    result["end_period"] = str(periods.max())
    expected = pd.period_range(periods.min(), periods.max(), freq="M")
    if not periods.is_unique or not periods.equals(expected):
        result["status"] = "irregular_periods"
        return result
    if len(periods) < min_periods:
        return result

    values = pd.to_numeric(ordered[value_col], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(values).all():
        result["status"] = "invalid_values"
        return result

    seasonal_groups = [
        values[periods.month == month] for month in range(1, 13)
    ]
    score = 0
    variance = 0.0
    pair_count = 0.0
    tied_pairs = 0.0
    for seasonal_values in seasonal_groups:
        for left in range(len(seasonal_values) - 1):
            score += int(
                np.sign(seasonal_values[left + 1 :] - seasonal_values[left]).sum()
            )
        _, tie_counts = np.unique(seasonal_values, return_counts=True)
        tie_term = sum(
            int(count * (count - 1) * (2 * count + 5))
            for count in tie_counts
            if count > 1
        )
        n_season = len(seasonal_values)
        variance += (
            n_season * (n_season - 1) * (2 * n_season + 5) - tie_term
        ) / 18
        pair_count += n_season * (n_season - 1) / 2
        tied_pairs += sum(count * (count - 1) / 2 for count in tie_counts)
    tau_denominator = math.sqrt(pair_count * (pair_count - tied_pairs))
    tau = score / tau_denominator if tau_denominator else 0.0
    if variance <= 0:
        result.update(status="no_variation", tau=float(tau))
        return result

    z_score = (
        (score - 1) / math.sqrt(variance)
        if score > 0
        else (score + 1) / math.sqrt(variance)
        if score < 0
        else 0.0
    )
    p_value = math.erfc(abs(z_score) / math.sqrt(2))
    direction = "up" if score > 0 else "down" if score < 0 else "none"
    result.update(
        status="significant" if p_value < alpha else "not_significant",
        direction=direction if p_value < alpha else "none",
        tau=float(tau),
        z_score=float(z_score),
        p_value=float(p_value),
    )
    return result


def detect_zscore(
    reference: Sequence[float],
    observed: float,
    threshold: float = Z_THRESHOLD,
    min_n: int = MIN_REFERENCE_N,
    period: str | None = None,
) -> dict[str, Any]:
    values = pd.to_numeric(pd.Series(reference), errors="coerce").to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    result: dict[str, Any] = {
        "observed": float(observed) if pd.notna(observed) else np.nan,
        "reference_mean": np.nan,
        "reference_std": np.nan,
        "score": np.nan,
        "threshold": float(threshold),
        "direction": "none",
        "period": period,
        "reference_n": int(len(values)),
        "reference_period": None,
        "status": "not_calculable",
    }
    if len(values) < min_n or not np.isfinite(observed):
        result["status"] = "insufficient_history_or_invalid_value"
        return result
    mean = float(values.mean())
    standard_deviation = float(values.std(ddof=1))
    result.update(
        reference_mean=mean,
        reference_std=standard_deviation,
        reference_period=f"{len(values)} previous periods",
    )
    if not np.isfinite(standard_deviation) or standard_deviation == 0:
        result["status"] = "zero_or_invalid_std"
        return result
    score = (float(observed) - mean) / standard_deviation
    result.update(
        score=score,
        direction=(
            "positive"
            if score >= threshold
            else "negative"
            if score <= -threshold
            else "none"
        ),
        status="anomaly" if abs(score) >= threshold else "normal",
    )
    return result


def detect_mad(
    reference: Sequence[float],
    observed: float,
    threshold: float = MAD_THRESHOLD,
    min_n: int = MIN_REFERENCE_N,
    period: str | None = None,
) -> dict[str, Any]:
    values = pd.to_numeric(pd.Series(reference), errors="coerce").to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    median = float(np.median(values)) if len(values) else np.nan
    mad = float(np.median(np.abs(values - median))) if len(values) else np.nan
    result: dict[str, Any] = {
        "observed": float(observed) if pd.notna(observed) else np.nan,
        "reference_median": median,
        "mad": mad,
        "score": np.nan,
        "threshold": float(threshold),
        "direction": "none",
        "period": period,
        "reference_n": int(len(values)),
        "reference_period": f"{len(values)} previous periods" if len(values) else None,
        "status": "not_calculable",
    }
    if len(values) < min_n or not np.isfinite(observed):
        result["status"] = "insufficient_history_or_invalid_value"
        return result
    if not np.isfinite(mad) or mad == 0:
        result["status"] = "zero_or_invalid_mad"
        return result
    score = 0.6745 * (float(observed) - median) / mad
    result.update(
        score=score,
        direction=(
            "positive"
            if score >= threshold
            else "negative"
            if score <= -threshold
            else "none"
        ),
        status="anomaly" if abs(score) >= threshold else "normal",
    )
    return result


def scan_monthly_anomalies(
    table: pd.DataFrame | None,
    value_col: str = "request_count",
    methods: Sequence[str] = ("zscore", "mad"),
) -> pd.DataFrame:
    columns = [
        "observed",
        "reference_mean",
        "reference_std",
        "score",
        "threshold",
        "direction",
        "period",
        "reference_n",
        "reference_period",
        "status",
        "method",
        "dimension",
        "group_value",
        "rule_version",
    ]
    if table is None or table.empty:
        return pd.DataFrame(columns=columns)
    required = {"month_start", "month", value_col}
    if not required.issubset(table.columns):
        raise ValueError(f"Anomaly table requires columns: {sorted(required)}")
    unknown_methods = set(methods) - {"zscore", "mad"}
    if unknown_methods:
        raise ValueError(f"Unsupported anomaly methods: {sorted(unknown_methods)}")
    ordered = table.sort_values("month_start").reset_index(drop=True)
    months = pd.PeriodIndex(ordered["month"].astype(str), freq="M")
    expected_months = pd.period_range(months.min(), months.max(), freq="M")
    if not months.is_unique or not months.equals(expected_months):
        raise ValueError(
            "Monthly anomaly series must be unique and contiguous; no gap filling is performed."
        )
    rows = []
    for index in range(MIN_REFERENCE_N, len(ordered)):
        reference = ordered.loc[: index - 1, value_col]
        period = str(ordered.loc[index, "month"])
        observed = float(ordered.loc[index, value_col])
        reference_period = (
            f"{ordered.loc[0, 'month']}–{ordered.loc[index - 1, 'month']}"
        )
        for method in methods:
            result = (
                detect_zscore(reference, observed, period=period)
                if method == "zscore"
                else detect_mad(reference, observed, period=period)
            )
            result["reference_period"] = reference_period
            result.update(
                method=method,
                dimension="all",
                group_value="all",
                rule_version=RULE_VERSION,
            )
            rows.append(result)
    return pd.DataFrame(rows, columns=columns)


def scan_group_month_anomalies(
    frame: pd.DataFrame,
    dimension: str,
    partial_months: set[str] | None = None,
) -> pd.DataFrame:
    if dimension not in frame or "month" not in frame:
        return pd.DataFrame()
    excluded_months = partial_months or set()
    clean_frame = frame.loc[
        frame["month"].notna()
        & ~frame["month"].astype(str).isin(excluded_months)
    ]
    month_periods = pd.PeriodIndex(
        clean_frame["month"].astype(str).drop_duplicates(), freq="M"
    ).sort_values()
    if not len(month_periods):
        return pd.DataFrame()
    complete_periods = pd.period_range(
        month_periods.min(), month_periods.max(), freq="M"
    )
    if not month_periods.equals(complete_periods):
        return pd.DataFrame()
    results = []
    for group_value, part in clean_frame.dropna(subset=[dimension, "month"]).groupby(dimension):
        monthly_counts = part.groupby("month").size()
        counts = (
            monthly_counts.reindex(complete_periods.astype(str), fill_value=0)
            .rename("request_count")
            .rename_axis("month")
            .reset_index()
        )
        counts["month_start"] = complete_periods.to_timestamp()
        group_results = scan_monthly_anomalies(counts)
        if not group_results.empty:
            group_results["dimension"] = dimension
            group_results["group_value"] = str(group_value)
            results.append(group_results)
    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def robust_month_anomalies(
    frame: pd.DataFrame,
    group_column: str | None = None,
    group_label: str = "all",
    partial_months: set[str] | None = None,
) -> list[dict[str, Any]]:
    if "month" not in frame.columns or frame["month"].notna().sum() == 0:
        return []
    excluded_months = partial_months or set()
    subset = frame.loc[
        frame["month"].notna() & ~frame["month"].astype(str).isin(excluded_months)
    ]
    group_iter = (
        [(group_label, subset)]
        if group_column is None
        else [
            (str(value), group)
            for value, group in subset.dropna(subset=[group_column]).groupby(group_column)
        ]
    )
    found = []
    for group_value, group in group_iter:
        counts = group.groupby("month").size().rename("volume").reset_index()
        counts["month_start"] = pd.PeriodIndex(counts["month"], freq="M").to_timestamp()
        counts = counts.sort_values("month_start")
        if len(counts) < MIN_MONTHS_ANOMALY:
            continue
        values = counts["volume"].astype(float)
        median = float(values.median())
        mad = float((values - median).abs().median())
        if mad > 0:
            scores = 0.6745 * (values - median) / mad
            mask = scores.abs() >= ROBUST_Z_THRESHOLD
            method = "robust_z_mad"
        else:
            scores = pd.Series(np.nan, index=values.index)
            mask = (values - median).abs() >= max(
                1.0, median * ZERO_MAD_RELATIVE_THRESHOLD
            )
            method = "fallback_relative_deviation_mad_zero"
        for index in counts.index[mask]:
            found.append(
                {
                    "dimension": group_label if group_column is None else group_column,
                    "group": group_value,
                    "period": counts.loc[index, "month"],
                    "volume": int(counts.loc[index, "volume"]),
                    "baseline_median": median,
                    "mad": mad,
                    "score": float(scores.loc[index])
                    if pd.notna(scores.loc[index])
                    else np.nan,
                    "method": method,
                    "threshold": ROBUST_Z_THRESHOLD
                    if mad > 0
                    else ZERO_MAD_RELATIVE_THRESHOLD,
                    "n_months": int(len(counts)),
                }
            )
    return found


def run_stl(
    series: pd.Series | None,
    period: int = 12,
    min_months: int = MIN_STL_MONTHS,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "not_run",
        "reason": None,
        "decomposition": None,
    }
    if importlib.util.find_spec("statsmodels") is None:
        result.update(
            status="skipped",
            reason="statsmodels is absent; install it to enable STL.",
        )
        return result
    if series is None or len(series) < max(min_months, 2 * period):
        result.update(
            status="skipped",
            reason=f"Insufficient history: at least {max(min_months, 2 * period)} months.",
        )
        return result
    values = series.copy().sort_index()
    if not isinstance(values.index, pd.PeriodIndex):
        result.update(status="skipped", reason="Monthly PeriodIndex required.")
        return result
    full_index = pd.period_range(values.index.min(), values.index.max(), freq="M")
    if not values.index.equals(full_index):
        result.update(status="skipped", reason="Irregular monthly index; no interpolation.")
        return result
    if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
        result.update(status="skipped", reason="Missing or non-finite values; no imputation.")
        return result
    from statsmodels.tsa.seasonal import STL

    fit = STL(values.astype(float), period=period, robust=True).fit()
    result.update(
        status="executed",
        decomposition=pd.DataFrame(
            {
                "observed": values,
                "trend": fit.trend,
                "seasonal": fit.seasonal,
                "resid": fit.resid,
            }
        ),
    )
    return result


def stl_residual_signals(
    decomposition: pd.DataFrame | None,
    threshold: float = MAD_THRESHOLD,
    min_n: int = MIN_REFERENCE_N,
) -> pd.DataFrame:
    columns = [
        "period",
        "observed",
        "reference_median",
        "mad",
        "score",
        "threshold",
        "direction",
        "status",
        "reference_n",
        "reference_period",
        "method",
        "dimension",
        "group_value",
        "rule_version",
    ]
    if decomposition is None or decomposition.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    residual = decomposition["resid"]
    for index in range(min_n, len(residual)):
        period = str(residual.index[index])
        result = detect_mad(
            residual.iloc[:index],
            float(residual.iloc[index]),
            threshold=threshold,
            min_n=min_n,
            period=period,
        )
        result.update(
            method="STL_residual_MAD",
            dimension="all",
            group_value="all",
            rule_version=RULE_VERSION,
        )
        rows.append(result)
    return pd.DataFrame(rows, columns=columns)


def delay_groups(frame: pd.DataFrame, group_column: str) -> pd.DataFrame | None:
    if group_column not in frame.columns or "resolution_days" not in frame.columns:
        return None
    mask = (
        frame["closed_date"].notna()
        & frame["resolution_days"].notna()
        & (frame["resolution_days"] >= 0)
    )
    population = frame.loc[mask & frame[group_column].notna()]
    table = (
        population.groupby(group_column)["resolution_days"]
        .agg(median_days="median", sample_size="size")
        .reset_index()
    )
    return table.loc[table["sample_size"] >= MIN_GROUP_N].sort_values(
        "median_days", ascending=False
    )


def high_delay_groups(
    group_comparisons: pd.DataFrame,
    dimensions: Sequence[str] = ("complaint_type", "borough"),
    min_n: int = MIN_GROUP_N,
) -> pd.DataFrame:
    if group_comparisons.empty:
        return pd.DataFrame(
            columns=[
                "dimension",
                "group",
                "median_days",
                "sample_size",
                "peer_p75_days",
                "minimum_group_n",
            ]
        )
    eligible = group_comparisons.loc[
        group_comparisons["dimension"].isin(dimensions)
        & group_comparisons["sample_size"].ge(min_n)
    ]
    results = []
    for dimension, table in eligible.groupby("dimension"):
        cutoff = float(table["median_days"].quantile(0.75))
        for _, row in table.loc[table["median_days"].ge(cutoff)].iterrows():
            results.append(
                {
                    "dimension": dimension,
                    "group": str(row["group_value"]),
                    "median_days": float(row["median_days"]),
                    "sample_size": int(row["sample_size"]),
                    "peer_p75_days": cutoff,
                    "minimum_group_n": min_n,
                }
            )
    return pd.DataFrame(
        results,
        columns=[
            "dimension",
            "group",
            "median_days",
            "sample_size",
            "peer_p75_days",
            "minimum_group_n",
        ],
    )


def compare_to_target(
    kpi: float | None,
    target: float | None = None,
    direction: str = "lower_is_better",
    period: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    if target is None:
        return {
            "kpi": kpi,
            "target": None,
            "period": period,
            "source": source,
            "status": "business target not configured",
            "gap": None,
        }
    if direction not in {"lower_is_better", "higher_is_better"}:
        raise ValueError("Unsupported target direction.")
    gap = float(kpi) - float(target) if kpi is not None else None
    attained = (
        kpi <= target
        if direction == "lower_is_better"
        else kpi >= target
    ) if kpi is not None else False
    return {
        "kpi": None if kpi is None else float(kpi),
        "target": float(target),
        "period": period,
        "source": source,
        "direction": direction,
        "gap": gap,
        "status": "attained" if attained else "missed",
    }
