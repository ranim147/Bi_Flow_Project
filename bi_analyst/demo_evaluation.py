"""Academic final evaluation: genuine Granite calls, explicit metric denominators."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time
from typing import Any

from .analytics import load_real_bundle
from .consumers import check_consumers
from .decision_evaluation import verify_decision_report
from .evaluation import REAL_SCENARIOS, _factual_metrics
from .models import AnalysisContext, BIAnalysisReport
from .orchestrator import analyze
from .verification import verify_report

DRILLDOWN = dict(id="drilldown_brooklyn", question="Investiguer la hausse des délais à Brooklyn; préciser les catégories, agences et périodes prioritaires.", expected_metrics=["delta_mean_days", "segment_contribution_days"])
FLAG_PREFIXES = {
    "unknown_evidence_fact_ids": "unverified_reference",
    "invalid_evidence_references": "unverified_reference",
    "unverified_numeric_text": "unsupported_numeric_text",
    "possible_unsupported_causal_claim": "unsupported_causal_text",
    "unsupported_recommendation": "unsupported_recommendation",
    "insight_outside_verified_language": "unverified_interpretation",
    "finding_inconsistent_with_python": "inconsistent_finding",
    "problem_inconsistent_with_python": "inconsistent_finding",
    "invented_priority_scope": "invented_scope",
}


def audit_summary(report: BIAnalysisReport, events: list[dict[str, Any]]) -> dict[str, Any]:
    requests = [e for e in events if e["event"] == "llm_request"]
    responses = [e for e in events if e["event"] == "llm_response"]
    validations = [e for e in events if e["event"] == "validation"]
    injected = any(e["event"] == "controlled_fault_injection" for e in events)
    by_unit = defaultdict(list)
    for event in validations:
        by_unit[event.get("problem_id", "main")].append(event)
    expected_units = {e.get("problem_id", "main") for e in requests}
    initial = bool(expected_units) and report.status == "validated" and all(
        any(e["attempt"] == 1 and e["result"]["status"] == "validated" for e in by_unit[unit])
        for unit in expected_units)
    correction_requests = sum(e["attempt"] == 2 for e in requests)
    # Historical v1 records can lack llm_request; current evaluation records it.
    if not requests:
        correction_requests = sum(e["attempt"] == 2 for e in responses)
    blocked = []
    histogram = Counter()
    for event in validations:
        errors = event["result"]["errors"]
        histogram.update(error.split(":", 1)[0] for error in errors)
        categories = sorted({category for error in errors for prefix, category in FLAG_PREFIXES.items() if error.startswith(prefix)})
        blocked.extend(dict(problem_id=event.get("problem_id", "main"), attempt=event["attempt"], category=category) for category in categories)
    return dict(genuine_model_run=not injected, model_response_count=len(responses),
                json_valid_responses=sum(e["result"]["json_valid"] for e in validations),
                correct_reference_responses=sum(e["result"]["evidence_valid"] for e in validations),
                validation_response_count=len(validations), initial_validated=initial,
                validated_after_correction=report.status == "validated" and not initial and correction_requests > 0,
                correction_requests=correction_requests, fallback=report.status == "fallback", failed=report.status == "failed",
                justified_insufficient_data=report.status == "validated" and report.decision == "insufficient_data",
                rejected_responses=sum(e["result"]["status"] == "rejected" for e in validations),
                blocked_unverified_assertion_flags=len(blocked), blocked_flag_details=blocked,
                validation_error_histogram=dict(histogram))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    genuine = [row for row in rows if row["audit"]["genuine_model_run"]]
    n = len(genuine)
    count = sum(row["audit"]["model_response_count"] for row in genuine)
    durations = [row["report_elapsed_ms"] for row in genuine]
    return dict(
        scenario_executions=n, distinct_scenarios=len({row["scenario_id"] for row in genuine}),
        model_response_count=count,
        raw_json_valid_rate=sum(row["audit"]["json_valid_responses"] for row in genuine) / count if count else None,
        raw_correct_reference_rate=sum(row["audit"]["correct_reference_responses"] for row in genuine) / count if count else None,
        granite_first_attempt_validated_rate=sum(row["audit"]["initial_validated"] for row in genuine) / n if n else None,
        granite_validated_after_correction_rate=sum(row["audit"]["validated_after_correction"] for row in genuine) / n if n else None,
        fallback_rate=sum(row["audit"]["fallback"] for row in genuine) / n if n else None,
        failure_rate=sum(row["audit"]["failed"] for row in genuine) / n if n else None,
        justified_insufficient_data_count=sum(row["audit"]["justified_insufficient_data"] for row in genuine),
        blocked_unverified_assertion_flags=sum(row["audit"]["blocked_unverified_assertion_flags"] for row in genuine),
        rejected_model_responses=sum(row["audit"]["rejected_responses"] for row in genuine),
        correction_requests=sum(row["audit"]["correction_requests"] for row in genuine),
        final_report_schema_valid_rate=sum(row["consumers_valid"] for row in genuine) / n if n else None,
        recomputed_proofs_valid_rate=sum(row["proofs_verified"] for row in genuine) / n if n else None,
        mean_elapsed_ms=statistics.mean(durations) if durations else None,
        median_elapsed_ms=statistics.median(durations) if durations else None,
        metric_definitions={
            "raw_json_valid_rate": "Validated JSON response bodies / all genuine Ollama response bodies, including corrections.",
            "raw_correct_reference_rate": "Response bodies whose evidence references passed Python / all genuine response bodies; malformed schemas fail conservatively.",
            "first_and_correction_rates": "Exclusive rates over complete genuine analyze() executions; all problems must pass initially for first-attempt success.",
            "fallback_rate": "Final status=fallback / genuine executions; validated insufficient_data is counted separately.",
            "blocked_unverified_assertion_flags": "Distinct validator flag categories per response (references, numerical/causal prose, recommendations, interpretation, finding or scope); not a count of natural-language statements or a semantic accuracy score.",
            "elapsed": "Mean/median of report.timing_ms.total over genuine executions, including corrections and model loading when incurred.",
        })


def run_final_evaluation(context: AnalysisContext, output: Path, repetitions: int = 2,
                         include_drilldown=True) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    output.mkdir(parents=True, exist_ok=False)
    bundle = load_real_bundle(context.project_root, context.dataset_version)
    rows = []
    cases = [(scenario, repetition) for repetition in range(1, repetitions + 1) for scenario in REAL_SCENARIOS]
    if include_drilldown:
        cases.append((DRILLDOWN, 1))
    for scenario, repetition in cases:
        started = time.perf_counter()
        report = analyze(scenario["question"], context, audit_root=output / "audit")
        folder = output / "audit" / report.execution_id
        events = [json.loads(line) for line in (folder / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        consumer_errors = []
        try:
            check_consumers(report)
        except ValueError as exc:
            consumer_errors.append(str(exc))
        if report.status == "failed":
            errors = [*report.errors]
        elif report.contract_version.endswith("v2"):
            checks = verify_decision_report(report, bundle, events)
            errors = checks["errors"]
        else:
            responses = [e for e in events if e["event"] == "llm_response"]
            errors = verify_report(report, context, responses[-1]["raw_response"] if responses else None)
        coverage = _factual_metrics(report, scenario["expected_metrics"])
        if not coverage["source_and_denominator_complete"]:
            errors.append("expected_metric_coverage_incomplete")
        row = dict(scenario_id=scenario["id"], repetition=repetition, execution_id=report.execution_id,
                   status=report.status, decision=report.decision, finding=report.finding,
                   report_elapsed_ms=report.timing_ms.get("total", 0), wall_elapsed_ms=(time.perf_counter() - started) * 1000,
                   audit=audit_summary(report, events), consumers_valid=not consumer_errors,
                   consumer_errors=consumer_errors, proofs_verified=not errors, verification_errors=errors,
                   factual_coverage=coverage, report_file=f"audit/{report.execution_id}/report.json",
                   audit_file=f"audit/{report.execution_id}/audit.jsonl")
        rows.append(row)
        (output / f"{scenario['id']}_r{repetition}.json").write_text(json.dumps(dict(result=row, report=report.model_dump(mode="json")), ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "progress.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{scenario['id']} r{repetition}: {report.status}, {report.decision}, {report.timing_ms.get('total', 0) / 1000:.2f}s, verified={not errors and not consumer_errors}", flush=True)
    metrics = dict(model="granite3.3:2b", model_inference_enabled=True, data_kind="real", human_evaluation_included=False,
                   created_at_utc=datetime.now(timezone.utc).isoformat(), repetitions=repetitions,
                   core_six=summarize([row for row in rows if row["scenario_id"] != DRILLDOWN["id"]]),
                   additional_drilldown=summarize([row for row in rows if row["scenario_id"] == DRILLDOWN["id"]]),
                   all_genuine_runs=summarize(rows), rows=rows, input_sha256=bundle["hashes"])
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--without-drilldown", action="store_true")
    args = parser.parse_args()
    metrics = run_final_evaluation(AnalysisContext(project_root=args.root), args.output, args.repetitions, not args.without_drilldown)
    print(json.dumps({key: metrics[key] for key in ("core_six", "additional_drilldown")}, ensure_ascii=False, indent=2))
    return 0 if all(row["proofs_verified"] and row["consumers_valid"] for row in metrics["rows"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
