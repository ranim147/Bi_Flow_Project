from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

import pandas as pd

from .analytics import (
    FILES,
    MIN_GROUP_N,
    MIN_REFERENCE_N,
    MIN_TREND_MONTHS,
    TREND_ALPHA,
    compare_group_delays,
    compare_group_volumes,
    high_delay_groups,
    mann_kendall_trend,
    monthly_trend_table,
    scan_group_month_anomalies,
    scan_monthly_anomalies,
)
from .models import AnalysisContext, EvidenceFact, ToolTrace

DIMENSIONS = ("borough", "complaint_type")


@dataclass(frozen=True)
class ToolResult:
    trace: ToolTrace
    evidence: list[EvidenceFact]
    result: dict[str, Any]


ToolFunction = Callable[
    [str, AnalysisContext, dict[str, Any]], tuple[list[EvidenceFact], dict[str, Any], str]
]


def _fact(
    bundle: dict[str, Any],
    context: AnalysisContext,
    *,
    fact_id: str,
    metric: str,
    scope: str,
    value: float | int | None,
    unit: str,
    n: int | None,
    source: str,
    method: str | None = None,
    threshold: float | None = None,
    reference_period: str | None = None,
    p_value: float | None = None,
) -> EvidenceFact:
    source_path = source.split("#", 1)[0]
    return EvidenceFact(
        fact_id=fact_id,
        metric=metric,
        scope=scope,
        value=None if value is None else float(value),
        unit=unit,
        n=n,
        source=source,
        source_sha256=bundle["hashes"].get(source_path),
        data_kind=context.data_kind,
        method=method,
        threshold=threshold,
        reference_period=reference_period,
        p_value=p_value,
    )


def _quality_tool(
    _question: str, context: AnalysisContext, bundle: dict[str, Any]
) -> tuple[list[EvidenceFact], dict[str, Any], str]:
    quality = bundle["quality"]
    source = bundle["source_paths"]["clean"]
    facts = [
        _fact(
            bundle,
            context,
            fact_id="quality.sample_rows",
            metric="sample_rows",
            scope="NYC 311 prepared extract",
            value=quality["rows"],
            unit="requests",
            n=quality["rows"],
            source=source,
        ),
        _fact(
            bundle,
            context,
            fact_id="quality.valid_resolution_days",
            metric="valid_resolution_days",
            scope="closed requests with valid resolution duration",
            value=quality["valid_delay_n"],
            unit="requests",
            n=quality["rows"],
            source=source,
        ),
        _fact(
            bundle,
            context,
            fact_id="quality.missing_resolution_days",
            metric="missing_or_invalid_resolution_days",
            scope="closed requests without a valid resolution duration",
            value=quality["closed_missing_delay_n"],
            unit="requests",
            n=quality["valid_closure_n"],
            source=source,
        ),
        _fact(
            bundle,
            context,
            fact_id="quality.open_requests",
            metric="not_closed_requests",
            scope="prepared extract with a valid creation date",
            value=quality["open_request_n"],
            unit="requests",
            n=quality["valid_creation_n"],
            source=source,
        ),
    ]
    details = {
        "valid_creation_n": quality["valid_creation_n"],
        "valid_closure_n": quality["valid_closure_n"],
        "closed_missing_delay_n": quality["closed_missing_delay_n"],
        "open_request_n": quality["open_request_n"],
        "partial_months": bundle["metadata"].get("partial_months", []),
        "sample_method": bundle["metadata"].get("sample_method", ""),
    }
    return facts, details, "success"


def _compare_tool(
    question: str, context: AnalysisContext, bundle: dict[str, Any]
) -> tuple[list[EvidenceFact], dict[str, Any], str]:
    question_lower = question.casefold()
    dimensions = [
        dimension
        for dimension, words in {
            "borough": ("borough", "arrondissement"),
            "complaint_type": ("complaint", "plainte", "demande", "catégorie", "type"),
        }.items()
        if any(word in question_lower for word in words)
    ]
    if not dimensions:
        dimensions = ["borough", "complaint_type"]
    delay_question = any(
        word in question_lower
        for word in ("délai", "delai", "résolution", "resolution", "traitement", "jours")
    )
    volume_question = any(word in question_lower for word in ("volume", "nombre")) or (
        not delay_question and any(word in question_lower for word in ("plaintes", "demandes"))
    )
    if not delay_question and not volume_question:
        delay_question = True

    facts: list[EvidenceFact] = []
    details: dict[str, Any] = {"dimensions": dimensions, "metrics": []}
    source = bundle["source_paths"]["clean"]
    if delay_question:
        comparisons = compare_group_delays(
            bundle["clean"], dimensions, min_n=MIN_GROUP_N
        )
        high = high_delay_groups(comparisons, dimensions=dimensions)
        details["delay_group_count"] = len(comparisons)
        details["eligible_group_count"] = int(
            comparisons["sample_status"].eq("meets_minimum").sum()
        ) if not comparisons.empty else 0
        details["high_relative_groups"] = high.to_dict("records")
        details["metrics"].append("median_resolution_days")
        for dimension in dimensions:
            dimension_rows = comparisons.loc[
                comparisons["dimension"].eq(dimension)
            ]
            rows = dimension_rows.loc[
                dimension_rows["sample_size"].ge(MIN_GROUP_N)
            ]
            small_group_count = int(
                dimension_rows["sample_status"].eq("small_sample").sum()
            )
            if small_group_count:
                facts.append(
                    _fact(
                        bundle,
                        context,
                        fact_id=f"quality.small_delay_groups.{dimension}",
                        metric="small_sample_group_count",
                        scope=f"{dimension} groups excluded from delay ranking",
                        value=small_group_count,
                        unit="groups",
                        n=len(dimension_rows),
                        source=f"{source}#derived={dimension}_group_sample_filter",
                    )
                )
            high_rows = rows.sort_values(
                ["median_days", "group_value"], ascending=[False, True]
            ).head(3)
            low_rows = rows.sort_values(
                ["median_days", "group_value"], ascending=[True, True]
            ).head(2)
            selected = pd.concat([high_rows, low_rows]).drop_duplicates(
                subset=["dimension", "group_value"]
            )
            for row in selected.to_dict("records"):
                facts.append(
                    _fact(
                        bundle,
                        context,
                        fact_id=f"delay.{dimension}.{row['group_value']}",
                        metric="median_resolution_days",
                        scope=f"{dimension}={row['group_value']}",
                        value=row["median_days"],
                        unit="jours calendaires",
                        n=int(row["sample_size"]),
                        source=(
                            f"{source}#dimension={dimension}"
                            f"&group_value={row['group_value']}&field=median_days"
                        ),
                    )
                )
            if not rows.empty:
                peer_p75 = float(rows["median_days"].quantile(0.75))
                facts.append(
                    _fact(
                        bundle,
                        context,
                        fact_id=f"delay.{dimension}.peer_p75",
                        metric="peer_group_median_p75",
                        scope=f"eligible {dimension} groups",
                        value=peer_p75,
                        unit="calendar days",
                        n=len(rows),
                        source=f"{source}#derived={dimension}_peer_p75",
                    )
                )
    if volume_question:
        details["metrics"].append("sample_request_count")
        for dimension in dimensions:
            volume = compare_group_volumes(bundle["clean"], dimension)
            details[f"{dimension}_volume_groups"] = len(volume)
            for row in volume.head(3).to_dict("records"):
                facts.append(
                    _fact(
                        bundle,
                        context,
                        fact_id=f"volume.{dimension}.{row['group_value']}",
                        metric="sample_request_count",
                        scope=f"{dimension}={row['group_value']} in prepared sample",
                        value=int(row["sample_request_count"]),
                        unit="sample requests",
                        n=int(row["dataset_n"]),
                        source=(
                            f"{source}#dimension={dimension}"
                            f"&group_value={row['group_value']}&field=sample_request_count"
                        ),
                    )
                )
    if delay_question and not details.get("eligible_group_count"):
        return facts, details, "skipped"
    return facts, details, "success"


def _trend_tool(
    _question: str, context: AnalysisContext, bundle: dict[str, Any]
) -> tuple[list[EvidenceFact], dict[str, Any], str]:
    partial_months = bundle["metadata"].get("partial_months", [])
    monthly = bundle["trend"].loc[
        ~bundle["trend"]["month"].astype(str).isin(set(partial_months))
    ]
    trend = monthly_trend_table(monthly)
    significance = mann_kendall_trend(trend)
    source = bundle["source_paths"]["trend"]
    facts = [_fact(
        bundle, context, fact_id="history.trend.complete_periods",
        metric="available_complete_periods", scope="complete monthly periods for seasonal trend test",
        value=len(trend), unit="months", n=len(trend), source=f"{source}#derived=complete_period_count",
        method="Seasonal Mann-Kendall prerequisites", threshold=float(MIN_TREND_MONTHS),
    )]
    if significance["status"] != "irregular_periods":
        for index, row in enumerate(trend.to_dict("records")):
            if pd.isna(row.get("change_pct")):
                continue
            facts.append(
                _fact(
                    bundle,
                    context,
                    fact_id=f"trend.change_pct.{row['month']}",
                    metric="monthly_request_volume_change",
                    scope=f"population volume change during {row['month']}",
                    value=float(row["change_pct"]),
                    unit="percent",
                    n=None
                    if index == 0
                    else int(trend.iloc[index - 1]["request_count"]),
                    source=f"{source}#month={row['month']}&field=change_pct",
                )
            )
    if significance["status"] in {"significant", "not_significant", "no_variation"}:
        facts.append(
            _fact(
                bundle,
                context,
                fact_id="trend.monthly_volume_mann_kendall",
                metric="monthly_trend_seasonal_kendall_tau",
                scope=(
                    f"population monthly request volume, "
                    f"{significance['start_period']} to {significance['end_period']}"
                ),
                value=significance["tau"],
                unit="Kendall tau-b",
                n=significance["n_periods"],
                source=f"{source}#field=request_count&method=mann_kendall",
                method=significance["method"],
                threshold=TREND_ALPHA,
                reference_period=(
                    f"{significance['start_period']}–{significance['end_period']}"
                ),
                p_value=significance["p_value"],
            )
        )
    details = {
        "complete_period_count": len(trend),
        "trend_significance": significance,
        "partial_months_excluded": partial_months,
        "required_periods_for_significance": MIN_TREND_MONTHS,
        "trend_significance_alpha": TREND_ALPHA,
    }
    status = (
        "skipped"
        if significance["status"]
        in {"insufficient_history", "irregular_periods", "invalid_values"}
        else "success"
    )
    return facts, details, status


def _anomaly_tool(
    _question: str, context: AnalysisContext, bundle: dict[str, Any]
) -> tuple[list[EvidenceFact], dict[str, Any], str]:
    trend = bundle["trend"].copy()
    partial_months = set(bundle["metadata"].get("partial_months", []))
    trend = trend.loc[~trend["month"].astype(str).isin(partial_months)]
    trend_frame = trend.rename(columns={"month": "month"}).copy()
    trend_frame["month_start"] = pd.to_datetime(trend_frame["month"] + "-01")
    anomalies = scan_monthly_anomalies(trend_frame)
    clean = bundle["clean"]
    if "month" not in clean and "created_date" in clean:
        clean = clean.copy()
        clean["month"] = pd.to_datetime(
            clean["created_date"], errors="coerce", utc=True
        ).dt.strftime("%Y-%m")
    grouped = [
        scan_group_month_anomalies(
            clean, dimension, partial_months=partial_months
        )
        for dimension in DIMENSIONS
        if dimension in clean
    ]
    grouped_results = pd.concat(
        [table for table in grouped if not table.empty], ignore_index=True
    ) if any(not table.empty for table in grouped) else pd.DataFrame()
    records = pd.concat([anomalies, grouped_results], ignore_index=True)
    available_periods = int(trend_frame["month"].nunique())
    sufficient = available_periods >= MIN_REFERENCE_N + 1
    details = {
        "status": "executed" if sufficient else "insufficient_history",
        "available_complete_periods": available_periods,
        "minimum_prior_periods": MIN_REFERENCE_N,
        "minimum_testable_periods": MIN_REFERENCE_N + 1,
        "anomaly_count": int(records["status"].eq("anomaly").sum())
        if not records.empty
        else 0,
        "partial_months_excluded": sorted(partial_months),
    }
    facts = [_fact(
        bundle, context, fact_id="history.anomaly.complete_periods",
        metric="available_complete_periods", scope="complete monthly periods for anomaly scan",
        value=available_periods, unit="months", n=available_periods,
        source=bundle["source_paths"]["trend"] + "#derived=complete_period_count",
        method="Monthly anomaly prerequisites", threshold=float(MIN_REFERENCE_N + 1),
    )]
    for index, row in enumerate(
        records.loc[records["status"].eq("anomaly")].to_dict("records"), start=1
    ):
        facts.append(
            _fact(
                bundle,
                context,
                fact_id=f"anomaly.{index}",
                metric="statistical_anomaly_score",
                scope=f"{row.get('dimension')}={row.get('group_value')}; period={row['period']}",
                value=row["score"],
                unit="standardized score",
                n=int(row["reference_n"]),
                source=(
                    bundle["source_paths"]["clean"] + "#derived=group_month_volume"
                    if row.get("dimension") != "all"
                    else bundle["source_paths"]["trend"] + "#derived=validated_monthly_series"
                ),
                method=str(row["method"]),
                threshold=float(row["threshold"]),
                reference_period=(
                    None
                    if row.get("reference_period") is None
                    else str(row["reference_period"])
                ),
            )
        )
    return facts, details, "success" if sufficient else "skipped"


def _summary_tool(
    _question: str, context: AnalysisContext, bundle: dict[str, Any]
) -> tuple[list[EvidenceFact], dict[str, Any], str]:
    source = bundle["source_paths"]["summary"]
    summary = bundle["summary"].set_index("kpi_name")
    quality = bundle["quality"]
    facts = []
    for metric in ("request_volume", "mean_resolution_days", "median_resolution_days", "closure_rate"):
        row = summary.loc[metric]
        count = (
            quality["valid_delay_n"]
            if "resolution" in metric
            else quality["valid_creation_n"]
            if metric == "closure_rate"
            else quality["rows"]
        )
        facts.append(
            _fact(
                bundle,
                context,
                fact_id=f"kpi.{metric}",
                metric=metric,
                scope="prepared NYC 311 sample",
                value=float(row["value"]),
                unit=str(row["unit"]),
                n=int(count),
                source=f"{source}#kpi_name={metric}&field=value",
            )
        )
    return facts, {"kpi_count": len(facts)}, "success"


TOOL_REGISTRY: dict[str, ToolFunction] = {
    "data_quality": _quality_tool,
    "compare_groups": _compare_tool,
    "analyze_trend": _trend_tool,
    "detect_anomalies": _anomaly_tool,
    "summarize_kpis": _summary_tool,
}


def _investigation_tool(question: str, context: AnalysisContext, bundle: dict[str, Any]):
    from .decision_tools import build_decision_analysis
    return build_decision_analysis(question, bundle)


TOOL_REGISTRY["investigate_degradation"] = _investigation_tool


def select_tools(question: str) -> list[str]:
    from .routing import route_question
    plan = route_question(question)
    if plan.executor == "scoped":
        return plan.operation_sequence
    from .decision_tools import decision_question
    if decision_question(question):
        return ["investigate_degradation"]
    text = question.casefold()
    selected: list[str] = ["data_quality"]
    if any(word in text for word in ("manqu", "qualité", "qualite", "quality", "incomplet", "non clôt")):
        return selected
    if any(word in text for word in ("comité", "synthèse", "synthese", "pilotage", "résume", "resume")):
        return [*selected, "summarize_kpis"]
    comparison = any(word in text for word in ("compar", "classe", "borough", "arrondissement", "catégorie", "type"))
    trend = any(word in text for word in ("tendance", "évolution", "evolution", "pic", "hausse", "baisse", "mois", "mensuel"))
    anomaly = any(word in text for word in ("anomal", "inhabituel", "pic"))
    if comparison:
        selected.append("compare_groups")
    if trend:
        selected.append("analyze_trend")
    if anomaly:
        selected.append("detect_anomalies")
    if not comparison and not trend and not anomaly:
        selected.append("summarize_kpis")
    return selected


def execute_tool(
    tool_name: str,
    question: str,
    context: AnalysisContext,
    bundle: dict[str, Any],
) -> ToolResult:
    from .routing import route_question
    plan = route_question(question)
    if plan.executor == "scoped":
        from .scoped_analytics import execute_plan
        results, _ = execute_plan(question, plan, bundle)
        result = next((r for r in results if r.trace.tool == tool_name), None)
        if result is None:
            raise ValueError("Tool is not executable for this analytical plan: " + tool_name)
        return result
    if tool_name not in TOOL_REGISTRY:
        raise ValueError(f"Tool is not allowlisted: {tool_name}")
    started = datetime.now(timezone.utc).isoformat()
    before = perf_counter()
    evidence, result, status = TOOL_REGISTRY[tool_name](question, context, bundle)
    elapsed_ms = (perf_counter() - before) * 1000
    trace = ToolTrace(
        tool=tool_name,
        parameters={"question": question[:240]},
        status=status,
        started_at=started,
        elapsed_ms=elapsed_ms,
        evidence_ids=[fact.fact_id for fact in evidence],
        detail=None if status == "success" else str(result.get("status", "insufficient evidence")),
    )
    return ToolResult(trace=trace, evidence=evidence, result=result)
