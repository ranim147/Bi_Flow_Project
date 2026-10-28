from __future__ import annotations

import re
from typing import Any
from typing import Iterable

from pydantic import ValidationError

from benchmark_llm.protocol import strict_json

from .models import EvidenceFact, LLMInterpretation, LLMSelection, ValidationResult

_NUMBER_WORDS = re.compile(
    r"\b(?:zero|zéro|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|"
    r"onze|douze|treize|quatorze|quinze|seize|vingt|trente|quarante|"
    r"cinquante|soixante|cent|mille|premier|première|second|seconde|"
    r"one|two|three|four|five|six|seven|first|second|"
    r"eight|nine|ten|eleven|twelve|twenty|thirty|hundred|thousand)\b",
    re.IGNORECASE,
)
_CAUSAL_LANGUAGE = re.compile(
    r"\b(?:caus(?:e|é|ée|és|ées|ent|ait|aient)|"
    r"s'explique par|en raison de|à cause (?:de|du|des|d')|provoque|provoqué|"
    r"responsable de|résulte de|"
    r"influenc(?:e|ent|er|é|ée|és|ées)|"
    r"affect(?:e|ent|er|é|ée|és|ées|ait|aient)?|"
    r"impact(?:e|ent|er|é|ée|és|ées)|"
    r"entra[iî]n(?:e|ent|er|é|ée|és|ées)|"
    r"caused by|because of|may affect|could affect|influences?)\b",
    re.IGNORECASE,
)
_STRONG_RECOMMENDATION = re.compile(
    r"\b(?:augmenter les effectifs|renforcer les effectifs|sanctionner|"
    r"réallouer|imposer|obliger|licencier|fermer|cut staffing|"
    r"increase staffing|must immediately|should immediately)\b",
    re.IGNORECASE,
)
_REQUIRED_BASE_LIMITS = {"sample_only", "nonrandom_sample", "no_cause"}


def validate_scoped_report(report, bundle) -> list[str]:
    """Recompute the requested plan and validate the FINAL published artifact.

    Exact materialization checks preserve numeric, scope, source, small-sample,
    causal-language and recommendation guarantees, including after fallback.
    A true statement from another analysis cannot pass this gate.
    """
    from .routing import route_question
    from .scoped_analytics import LIMITS, execute_plan
    from .scoped import scoped_policy, materialize_sections
    from .models import BIAnalysisReport, LLMDraft
    errors = []
    try:
        BIAnalysisReport.model_validate_json(report.model_dump_json())
        plan = route_question(report.question)
        if plan.executor != "scoped":
            return ["question_intent_executor_mismatch"]
        results, details = execute_plan(report.question, plan, bundle)
    except (ValueError, KeyError, TypeError) as exc:
        return ["semantic_recomputation_failed:" + str(exc)[:200]]
    canonical = [fact for result in results for fact in result.evidence]
    if report.analytical_plan is None or report.analytical_plan.model_dump(mode="json") != details["plan"]:
        errors.append("metric_dimensions_filters_periods_or_sequence_mismatch")
    if report.evidence != canonical:
        errors.append("recomputed_evidence_or_scope_mismatch")
    if report.source_refs != sorted({fact.source for fact in canonical}):
        errors.append("source_refs_mismatch")
    if report.decision != details["decision"] or report.finding != details["finding"]:
        errors.append("central_question_decision_mismatch")
    if report.limitations != [LIMITS[code] for code in details["limitations"]]:
        errors.append("mandatory_limitations_mismatch")
    actual = [(t.tool, t.parameters, t.status, t.evidence_ids) for t in report.tool_trace]
    expected = [(r.trace.tool, r.trace.parameters, r.trace.status, r.trace.evidence_ids) for r in results]
    if actual != expected:
        errors.append("operation_sequence_or_tool_binding_mismatch")
    policy = scoped_policy(details, canonical)
    possible = [None] if report.status == "fallback" else [
        LLMDraft.model_validate({**policy.example(), "insight": insight, "recommendation": recommendation})
        for insight in policy.insights for recommendation in policy.recommendations]
    if not any(report.insights == materialize_sections(report.execution_id, details, draft) for draft in possible):
        errors.append("final_text_metric_scope_required_results_or_evidence_mismatch")
    if report.status == "validated" and (report.model != "granite3.3:2b" or report.validation.status != "validated"
            or report.errors or report.validation.errors or not all((report.validation.json_valid,
                report.validation.schema_valid, report.validation.evidence_valid,
                report.validation.numerical_claims_valid, report.validation.causal_claims_clear))):
        errors.append("validated_status_inconsistent")
    return errors


def selection_schema(
    allowed_slots: Iterable[str], allowed_recommendations: Iterable[str]
) -> dict[str, Any]:
    schema = LLMSelection.model_json_schema()
    properties = schema["properties"]
    properties["evidence_slots"]["items"]["enum"] = sorted(set(allowed_slots))
    properties["evidence_slots"]["uniqueItems"] = True
    properties["evidence_slots"]["maxItems"] = min(4, len(set(allowed_slots)))
    properties["recommendation_code"]["enum"] = sorted(
        set(allowed_recommendations)
    )
    return schema


def validate_selection(
    raw: str,
    slot_to_fact_id: dict[str, str],
    allowed_recommendations: set[str],
) -> tuple[LLMSelection | None, ValidationResult]:
    try:
        parsed_json = strict_json(raw)
    except (ValueError, TypeError) as exc:
        return None, ValidationResult(
            status="rejected",
            json_valid=False,
            schema_valid=False,
            evidence_valid=False,
            numerical_claims_valid=True,
            causal_claims_clear=True,
            errors=[f"invalid_json:{str(exc)[:240]}"],
        )

    try:
        selection = LLMSelection.model_validate(parsed_json)
    except ValidationError as exc:
        return None, ValidationResult(
            status="rejected",
            json_valid=True,
            schema_valid=False,
            evidence_valid=False,
            numerical_claims_valid=True,
            causal_claims_clear=True,
            errors=[f"schema:{error['loc']}:{error['msg']}" for error in exc.errors()],
        )

    errors: list[str] = []
    if len(selection.evidence_slots) != len(set(selection.evidence_slots)):
        errors.append("duplicate_evidence_slot")
    unknown_slots = sorted(set(selection.evidence_slots) - slot_to_fact_id.keys())
    if unknown_slots:
        errors.append("unknown_evidence_slots:" + ",".join(unknown_slots))
    if selection.recommendation_code not in allowed_recommendations:
        errors.append(
            "recommendation_not_allowed:" + selection.recommendation_code
        )
    evidence_valid = not any(
        error.startswith(("duplicate_evidence_slot", "unknown_evidence_slots:"))
        for error in errors
    )
    return selection, ValidationResult(
        status="validated" if not errors else "rejected",
        json_valid=True,
        schema_valid=not errors,
        evidence_valid=evidence_valid,
        numerical_claims_valid=True,
        causal_claims_clear=True,
        errors=errors,
    )


def validate_interpretation(
    raw: str,
    evidence: Iterable[EvidenceFact],
    required_limitations: set[str],
    *,
    require_insufficient_data: bool = False,
) -> tuple[LLMInterpretation | None, ValidationResult]:
    errors: list[str] = []
    try:
        parsed_json = strict_json(raw)
    except (ValueError, TypeError) as exc:
        return None, ValidationResult(
            status="rejected",
            json_valid=False,
            schema_valid=False,
            evidence_valid=False,
            numerical_claims_valid=False,
            causal_claims_clear=False,
            errors=[f"invalid_json:{str(exc)[:240]}"],
        )

    try:
        interpretation = LLMInterpretation.model_validate(parsed_json)
    except ValidationError as exc:
        return None, ValidationResult(
            status="rejected",
            json_valid=True,
            schema_valid=False,
            evidence_valid=False,
            numerical_claims_valid=False,
            causal_claims_clear=False,
            errors=[f"schema:{error['loc']}:{error['msg']}" for error in exc.errors()],
        )

    facts = {fact.fact_id: fact for fact in evidence}
    ids = interpretation.evidence_fact_ids
    evidence_valid = True
    if len(ids) != len(set(ids)):
        errors.append("duplicate_evidence_fact_id")
        evidence_valid = False
    unknown = sorted(set(ids) - facts.keys())
    if unknown:
        errors.append("unknown_evidence_fact_ids:" + ",".join(unknown))
        evidence_valid = False
    if facts and not ids:
        errors.append("no_evidence_referenced")
        evidence_valid = False
    if not _REQUIRED_BASE_LIMITS.issubset(interpretation.limitation_codes):
        missing_base = sorted(_REQUIRED_BASE_LIMITS - set(interpretation.limitation_codes))
        errors.append("required_limitations_missing:" + ",".join(missing_base))
    missing_limits = sorted(required_limitations - set(interpretation.limitation_codes))
    if missing_limits:
        errors.append("context_limitations_missing:" + ",".join(missing_limits))
    numerical_claims_valid = not (
        re.search(r"\d", interpretation.insight + " " + interpretation.recommendation)
        or _NUMBER_WORDS.search(interpretation.insight + " " + interpretation.recommendation)
    )
    if not numerical_claims_valid:
        errors.append("unverified_numeric_text")
    causal_claims_clear = not _CAUSAL_LANGUAGE.search(
        interpretation.insight + " " + interpretation.recommendation
    )
    if not causal_claims_clear:
        errors.append("possible_unsupported_causal_claim")
    if require_insufficient_data and interpretation.finding not in {
        "insufficient_history",
        "missing_data",
        "small_sample",
    }:
        errors.append("finding_must_reflect_insufficient_data")
    if require_insufficient_data and interpretation.decision != "insufficient_data":
        errors.append("decision_must_be_insufficient_data")
    if (
        interpretation.decision == "insufficient_data"
        and _STRONG_RECOMMENDATION.search(interpretation.recommendation)
    ):
        errors.append("strong_recommendation_on_insufficient_data")

    required_limitations_valid = not any(
        error.startswith(("required_limitations_missing:", "context_limitations_missing:"))
        for error in errors
    )
    schema_valid = True
    passed = all(
        [
            evidence_valid,
            required_limitations_valid,
            numerical_claims_valid,
            causal_claims_clear,
            not errors,
        ]
    )
    return interpretation, ValidationResult(
        status="validated" if passed else "rejected",
        json_valid=True,
        schema_valid=schema_valid,
        evidence_valid=evidence_valid,
        numerical_claims_valid=numerical_claims_valid,
        causal_claims_clear=causal_claims_clear,
        errors=errors,
    )
