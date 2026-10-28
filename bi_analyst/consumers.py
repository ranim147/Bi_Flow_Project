"""Read-only simulated consumers of the BI Analyst JSON contract.

These adapters verify schema/reference consistency, not the truth of arbitrary
prose or the source calculations. Recorded-run replay performs that second check.
They do not implement or call the other BIFlow agents.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any

from .models import BIAnalysisReport


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("consumer_contract:" + message)


def _ids(values: list[str], label: str, allowed: set[str] | None = None,
         *, nonempty: bool = False) -> set[str]:
    _require(not nonempty or bool(values), label + ":empty")
    _require(len(values) == len(set(values)), label + ":duplicate")
    _require(all(value.strip() and "*" not in value for value in values), label + ":invalid_identifier")
    result = set(values)
    if allowed is not None:
        _require(result <= allowed, label + ":unknown_reference")
    return result


def _timestamp(value: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("consumer_contract:" + label + ":invalid_timestamp") from exc
    _require(parsed.tzinfo is not None, label + ":timezone_required")


def _validated_report(value: BIAnalysisReport | dict[str, Any]) -> BIAnalysisReport:
    # Round-trip model instances too: assignment to a Pydantic object can occur
    # after its original validation and must not bypass contract checks.
    data = value.model_dump(mode="json") if isinstance(value, BIAnalysisReport) else value
    report = BIAnalysisReport.model_validate(data)
    _require(bool(report.execution_id.strip()) and bool(report.question.strip()), "missing_request_identity")
    _timestamp(report.generated_at, "generated_at")
    _require(all(math.isfinite(duration) and duration >= 0 for duration in report.timing_ms.values()), "invalid_timing")

    exported = _ids([fact.fact_id for fact in report.evidence], "evidence")
    sources = [fact.source for fact in report.evidence]
    _require(len(report.source_refs) == len(set(report.source_refs)), "duplicate_source_ref")
    _require(set(report.source_refs) == set(sources), "source_refs_mismatch")
    for fact in report.evidence:
        _require(fact.data_kind == report.data_kind, "mixed_data_kind")
        _require(bool(re.fullmatch(r"[0-9a-fA-F]{64}", fact.source_sha256 or "")), "missing_or_invalid_source_fingerprint:" + fact.fact_id)
        _require((report.data_kind == "synthetic") == fact.source.startswith("synthetic://"), "source_data_kind_mismatch:" + fact.fact_id)
        for field in ("threshold", "p_value"):
            number = getattr(fact, field)
            _require(number is None or math.isfinite(number), "nonfinite_evidence_metadata:" + fact.fact_id)

    traced: set[str] = set()
    for trace in report.tool_trace:
        _timestamp(trace.started_at, "tool_trace.started_at")
        _require(bool(trace.tool.strip()) and math.isfinite(trace.elapsed_ms), "invalid_tool_trace")
        references = _ids(trace.evidence_ids, "tool_trace.evidence_ids")
        _require(trace.status != "error" or not references, "error_tool_exports_evidence")
        traced.update(references)
    # Tools can compute candidates which the controller does not retain. Every
    # published fact must be traced; trace-only candidate IDs remain legitimate.
    _require(exported <= traced, "exported_evidence_not_traced")

    _ids([insight.insight_id for insight in report.insights], "insight_ids")
    for insight in report.insights:
        _ids(insight.evidence_fact_ids, "insight.evidence_fact_ids", exported, nonempty=True)
        _require(bool(insight.insight.strip()) and bool(insight.recommendation.strip()), "empty_insight_text")
        _require(len(insight.limitation_codes) == len(set(insight.limitation_codes)), "duplicate_insight_limitation")

    flags = (report.validation.json_valid, report.validation.schema_valid,
             report.validation.evidence_valid, report.validation.numerical_claims_valid,
             report.validation.causal_claims_clear)
    if report.status == "validated":
        _require(report.model == "granite3.3:2b", "validated_without_granite")
        _require(report.validation.status == "validated" and all(flags), "validated_with_rejected_checks")
        _require(not report.errors and not report.validation.errors, "validated_with_errors")
        _require(report.decision != "refuse", "validated_refusal")
    elif report.status == "failed":
        _require(report.decision == "refuse" and report.validation.status != "validated", "failed_without_refusal")
        _require(bool(report.errors), "failed_without_reason")
        _require(not report.insights and not report.decision_support, "failed_publishes_interpretation")
    elif report.model is not None:
        _require(report.validation.status != "validated", "fallback_hidden_as_llm_validated")

    if report.status != "failed":
        _require(bool(report.evidence) and bool(report.insights), "successful_output_without_evidence")
        _require(bool(report.limitations), "successful_output_without_limitations")
    if report.decision == "insufficient_data":
        _require(report.finding in {"insufficient_history", "small_sample", "small_scope", "missing_data", "insufficient_data"}, "insufficiency_finding_mismatch")

    _ids([plan.problem_id for plan in report.decision_support], "problem_ids")
    for plan in report.decision_support:
        _require(plan.priority_scope.scope_id == plan.problem_id, "scope_identity_mismatch")
        _require(bool(plan.problem.strip()) and bool(plan.limitations), "empty_problem_or_limitations")
        plan_ids = _ids(plan.evidence_ids, "plan.evidence_ids", exported, nonempty=True)
        periods = (plan.priority_scope.baseline_period, plan.priority_scope.current_period)
        _require((periods[0] is None) == (periods[1] is None), "incomplete_period_pair")
        if periods[0] is not None:
            _require(all(re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", period or "") is not None for period in periods), "invalid_period")
            _require(periods[0] < periods[1], "reversed_periods")
        hypotheses = _ids([hypothesis.hypothesis_id for hypothesis in plan.investigation_hypotheses], "hypotheses", nonempty=True)
        for hypothesis in plan.investigation_hypotheses:
            _ids(hypothesis.evidence_ids, "hypothesis.evidence_ids", plan_ids, nonempty=True)
            _require(hypothesis.status == "unverified" and bool(hypothesis.statement.strip()) and bool(hypothesis.additional_data), "hypothesis_not_falsifiable")
        for name in ("investigation_steps", "recommended_actions", "expected_verification"):
            references = _ids([item.hypothesis_id for item in getattr(plan, name)], name, hypotheses, nonempty=True)
            _require(references == hypotheses, "missing_hypothesis_operation:" + name)
        for step in plan.investigation_steps:
            _require(bool(step.fields) and bool(step.comparison.strip()), "incomplete_investigation_step")
            for key, label in plan.priority_scope.filters.items():
                if key in step.filters:
                    _require(step.filters[key] == label, "contradictory_step_scope")
            if "borough" in plan.priority_scope.filters:
                _require(step.filters.get("borough") == plan.priority_scope.filters["borough"], "step_loses_borough_scope")
        _require(sorted(action.priority for action in plan.recommended_actions) == list(range(1, len(hypotheses) + 1)), "invalid_action_priorities")
        for action in plan.recommended_actions:
            _require(all(text.strip() for text in (action.action, action.priority_reason, action.decision_if_supported, action.decision_if_refuted)), "incomplete_conditional_action")
            _require(action.decision_if_supported != action.decision_if_refuted, "action_outcomes_not_distinguished")
        for expected in plan.expected_verification:
            _require(bool(expected.supports.strip()) and bool(expected.refutes.strip()) and expected.supports != expected.refutes, "verification_outcomes_not_distinguished")
        if report.status == "validated":
            _require(plan.origin == "granite_validated", "validated_contains_fallback")
        if report.model is None:
            _require(plan.origin == "python_fallback", "baseline_claims_granite_origin")
        _require(any(set(insight.evidence_fact_ids) == plan_ids for insight in report.insights), "plan_missing_summary_binding")
    if report.contract_version == "biflow-bi-analyst-v2" and report.status != "failed":
        scoped = report.analytical_plan is not None and report.analytical_plan.executor == "scoped"
        _require(bool(report.decision_support) or scoped, "v2_missing_decision_plans")
        if scoped:
            plan = report.analytical_plan
            _require(bool(plan.operation_sequence) and bool(plan.metric), "scoped_missing_metric_or_operations")
            operations = [trace.tool for trace in report.tool_trace]
            if report.decision == "answer":
                _require(operations == plan.operation_sequence, "scoped_operation_sequence_mismatch")
            else:
                _require(all(op in plan.operation_sequence for op in operations), "scoped_unknown_operation")
            _require(not plan.unsupported_request or report.decision == "insufficient_data", "unsupported_request_answered")
            _require(not plan.temporal_forbidden or not any(op in operations for op in ("compare_periods", "decompose_mean_change")), "forbidden_temporal_analysis")
            if plan.metric == "median_resolution_days":
                _require(not any(fact.metric in {"mean_resolution_days", "segment_contribution_days", "delta_mean_days"} for fact in report.evidence), "scoped_metric_mismatch")
        if report.status == "fallback" and not scoped:
            _require(any(plan.origin == "python_fallback" for plan in report.decision_support), "fallback_without_fallback_plan")
    return report


def check_consumers(value: BIAnalysisReport | dict[str, Any]) -> dict[str, Any]:
    """Validate one report and return simulated consumer views.

    Raises ValueError for inconsistent JSON, provenance references or state. A
    successful result checks the interface only; it is not a human rating or an
    independent recalculation of the statistical evidence.
    """
    report = _validated_report(value)
    llm_validated = report.status == "validated"
    route = ("refused" if report.status == "failed" else "insufficient_data"
             if report.decision == "insufficient_data" else "answer")
    return {
        "validated": True,
        "checks_scope": "schema_and_reference_consistency",
        "contract_version": report.contract_version,
        "orchestrator": {
            "execution_id": report.execution_id, "route": route, "status": report.status,
            "decision": report.decision, "llm_validated": llm_validated,
            "python_fallback": report.status == "fallback", "data_kind": report.data_kind,
            "requires_additional_data": report.decision == "insufficient_data",
        },
        "dashboard": {
            "renderable": report.status != "failed", "data_kind": report.data_kind,
            "facts": [fact.model_dump(mode="json") for fact in report.evidence],
            "insights": [insight.model_dump(mode="json") for insight in report.insights],
            "decision_support": [plan.model_dump(mode="json") for plan in report.decision_support],
            "analytical_plan": report.analytical_plan.model_dump(mode="json") if report.analytical_plan else None,
            "limitations": list(report.limitations),
        },
        "auditor": {
            "execution_id": report.execution_id,
            "source_refs": list(report.source_refs),
            "fact_ids": [fact.fact_id for fact in report.evidence],
            "tool_trace": [trace.model_dump(mode="json") for trace in report.tool_trace],
            "validation": report.validation.model_dump(mode="json"), "errors": list(report.errors),
            "causes_confirmed_by_this_check": False,
            "canonical_recalculation_required": True,
        },
    }
