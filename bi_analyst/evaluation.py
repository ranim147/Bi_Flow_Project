from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import uuid
from typing import Any

from .models import AnalysisContext, BIAnalysisReport
from .orchestrator import analyze

REAL_SCENARIOS = [
    {
        "id": "borough_resolution_delays",
        "question": "Compare les délais de résolution par arrondissement.",
        "expected_metrics": ["median_resolution_days"],
    },
    {
        "id": "complaint_type_comparison",
        "question": "Quels types de plaintes ont des délais de résolution relativement élevés ?",
        "expected_metrics": ["median_resolution_days"],
    },
    {
        "id": "monthly_anomaly",
        "question": "Y a-t-il un pic anormal de plaintes dans les séries mensuelles ?",
        "expected_metrics": ["monthly_request_volume_change"],
    },
    {
        "id": "steering_committee",
        "question": "Prépare des insights prudents pour le comité de pilotage.",
        "expected_metrics": ["request_volume", "median_resolution_days"],
    },
    {
        "id": "missing_data",
        "question": "Quels sont les effets des données manquantes sur l'analyse des délais ?",
        "expected_metrics": ["missing_or_invalid_resolution_days"],
    },
    {
        "id": "insufficient_history",
        "question": "Peut-on conclure à l'absence d'anomalie avec cet historique mensuel ?",
        "expected_metrics": ["monthly_request_volume_change"],
    },
]


def _factual_metrics(report: BIAnalysisReport, expected_metrics: list[str]) -> dict[str, Any]:
    allowed = set(expected_metrics)
    expected = [fact for fact in report.evidence if fact.metric in allowed]
    missing_metrics = sorted(allowed - {fact.metric for fact in expected})
    invalid = [
        fact.fact_id
        for fact in expected
        if not fact.source or not fact.source_sha256 or fact.n is None
    ]
    return {
        "expected_metric_facts": len(expected),
        "expected_metric_present": not missing_metrics,
        "missing_metrics": missing_metrics,
        "source_and_denominator_complete": not missing_metrics and not invalid,
        "invalid_fact_ids": invalid,
    }


def _summary_metrics(
    paired_results: list[dict[str, Any]],
    *,
    model_inference_enabled: bool,
) -> dict[str, Any]:
    model_reports = [row["agent"] for row in paired_results]
    baseline_reports = [row["baseline"] for row in paired_results]
    facts_compared = 0
    numeric_mismatches = 0
    provenance_mismatches = 0
    unsupported_evidence_selections = 0
    human_ratings = None
    for row in paired_results:
        if not model_inference_enabled:
            continue
        baseline = {fact.fact_id: fact for fact in row["baseline"].evidence}
        for fact in row["agent"].evidence:
            facts_compared += 1
            reference = baseline.get(fact.fact_id)
            if reference is None:
                numeric_mismatches += 1
                provenance_mismatches += 1
            else:
                numeric_mismatches += int(reference.value != fact.value)
                provenance_mismatches += int(
                    any(
                        getattr(reference, field) != getattr(fact, field)
                        for field in (
                            "metric",
                            "scope",
                            "unit",
                            "n",
                            "source",
                            "source_sha256",
                        )
                    )
                )
        unsupported_evidence_selections += sum(
            "unknown_evidence_slots" in error
            or "unknown_evidence_fact_ids" in error
            for error in row["agent"].validation.errors
        )
    baseline_elapsed = [
        report.timing_ms.get("total", 0.0) for report in baseline_reports
    ]
    baseline_refusals = [
        report.decision in {"insufficient_data", "refuse"}
        or report.status == "failed"
        for report in baseline_reports
    ]
    return {
        "scenario_count": len(paired_results),
        "baseline_validated_rate": (
            sum(report.validation.status == "validated" for report in baseline_reports)
            / len(baseline_reports)
            if baseline_reports
            else None
        ),
        "agent_fully_validated_rate": (
            sum(report.status == "validated" for report in model_reports)
            / len(model_reports)
            if model_reports and model_inference_enabled
            else None
        ),
        "structured_facts_compared": facts_compared if model_inference_enabled else None,
        "numeric_mismatches": numeric_mismatches if model_inference_enabled else None,
        "provenance_mismatches": provenance_mismatches if model_inference_enabled else None,
        "factual_evidence_accuracy": (
            1 - (numeric_mismatches + provenance_mismatches) / (2 * facts_compared)
            if facts_compared and model_inference_enabled
            else None
        ),
        "unsupported_evidence_selection_errors": (
            unsupported_evidence_selections if model_inference_enabled else None
        ),
        "human_insight_relevance": human_ratings,
        "agent_mean_elapsed_ms": (
            sum(report.timing_ms.get("total", 0.0) for report in model_reports)
            / len(model_reports) if model_reports and model_inference_enabled
            else None
        ),
        "agent_refusal_or_controlled_failure_rate": (
            sum(
                report.decision in {"insufficient_data", "refuse"}
                or report.status in {"fallback", "failed"}
                for report in model_reports
            )
            / len(model_reports) if model_reports and model_inference_enabled
            else None
        ),
        "baseline_mean_elapsed_ms": (
            sum(baseline_elapsed) / len(baseline_elapsed) if baseline_elapsed else None
        ),
        "baseline_refusal_or_controlled_failure_rate": (
            sum(baseline_refusals) / len(baseline_refusals)
            if baseline_refusals
            else None
        ),
        "human_evaluation_included": False,
        "human_ratings_note": "Automatic checks only; human evaluation is outside this phase.",
        "expected_metrics_coverage_rate": (
            sum(_factual_metrics(row["agent"], row["scenario"]["expected_metrics"])["expected_metric_present"] for row in paired_results)
            / len(paired_results) if paired_results and model_inference_enabled else None
        ) if all("scenario" in row for row in paired_results) else None,
    }


def run_evaluation(
    context: AnalysisContext,
    *,
    use_llm: bool,
    limit: int | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer.")
    scenarios = REAL_SCENARIOS[:limit] if limit else REAL_SCENARIOS
    run_id = uuid.uuid4().hex
    destination = output_dir or context.project_root / "outputs" / "bi_analyst_evaluations" / run_id
    # A campaign is immutable: never overwrite an existing directory.
    destination.mkdir(parents=True, exist_ok=False)
    paired = []
    outcomes = []
    for scenario in scenarios:
        baseline = analyze(
            scenario["question"],
            context,
            use_llm=False,
            audit_root=destination / "audit",
        )
        before = time.perf_counter()
        agent = (
            analyze(
                scenario["question"],
                context,
                use_llm=True,
                audit_root=destination / "audit",
            )
            if use_llm
            else baseline
        )
        agent_elapsed = (time.perf_counter() - before) * 1000
        paired.append({"scenario": scenario, "baseline": baseline, "agent": agent})
        audit_dir = destination / "audit" / agent.execution_id
        events = [json.loads(line) for line in (audit_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        validations = [event["result"] for event in events if event["event"] == "validation"]
        responses = [event["raw_response"] for event in events if event["event"] == "llm_response"]
        outcome = dict(scenario_id=scenario["id"], status=agent.status, decision=agent.decision,
                       initial_response=responses[0] if responses else None,
                       initial_validation=validations[0] if validations else None,
                       correction_response=responses[1] if len(responses) > 1 else None,
                       correction_validation=validations[1] if len(validations) > 1 else None,
                       fallback=agent.status == "fallback", timing_ms=agent.timing_ms,
                       evidence_used=[fact.fact_id for fact in agent.evidence],
                       audit_dir=str(audit_dir.relative_to(destination)))
        outcomes.append(outcome)
        (destination / f"{scenario['id']}.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "baseline": baseline.model_dump(mode="json"),
                    "agent": agent.model_dump(mode="json"),
                    "agent_wall_time_ms": agent_elapsed,
                    "execution": outcome,
                    "factual_metrics": _factual_metrics(
                        agent, scenario["expected_metrics"]
                    ),
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        # Checkpoint each completed scenario, including failed model calls.
        (destination / "progress.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{scenario['id']}: {agent.status}, {agent.decision}, {agent_elapsed / 1000:.2f}s", flush=True)
    metrics = _summary_metrics(paired, model_inference_enabled=use_llm)
    metrics["run_id"] = run_id
    metrics["data_kind"] = "real"
    metrics["model_inference_enabled"] = use_llm
    metrics["scenario_ids"] = [scenario["id"] for scenario in scenarios]
    metrics["internal_contract"] = "granite-draft-v2" if use_llm else None
    metrics["initial_validated_count"] = sum(bool(row["initial_validation"] and row["initial_validation"]["status"] == "validated") for row in outcomes) if use_llm else None
    metrics["correction_count"] = sum(row["correction_response"] is not None for row in outcomes) if use_llm else None
    metrics["fallback_count"] = sum(row["fallback"] for row in outcomes) if use_llm else None
    metrics["failed_count"] = sum(row["status"] == "failed" for row in outcomes)
    metrics["insufficient_data_count"] = sum(row["decision"] == "insufficient_data" for row in outcomes)
    metrics["llm_fallback_rate"] = (sum(row["fallback"] for row in outcomes) / len(outcomes)) if use_llm else None
    (destination / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return {"output_dir": str(destination), "metrics": metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare the BI Analyst to its Python baseline.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    result = run_evaluation(
        AnalysisContext(project_root=args.root),
        use_llm=not args.baseline_only,
        limit=args.limit,
        output_dir=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
