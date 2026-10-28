"""Offline replay of a saved campaign against the real inputs; no model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .analytics import load_real_bundle
from .draft import build_policy, validate_draft
from .evaluation import REAL_SCENARIOS, _factual_metrics
from .models import AnalysisContext, BIAnalysisReport, LLMDraft
from .orchestrator import (
    _LIMITATIONS_FR, _RECOMMENDATIONS_FR, _default_recommendation, _derive_finding, _evidence_candidates,
    _find_required_limits, _include_required_trend_evidence, _insufficiency_finding,
    _render_python_insight,
)
from .tools import execute_tool, select_tools


def verify_report(report: BIAnalysisReport, context: AnalysisContext,
                  raw_response: str | None = None) -> list[str]:
    """Recalculate facts and decisions, then revalidate the accepted Granite draft."""
    errors = []
    bundle = load_real_bundle(context.project_root, context.dataset_version)
    if report.analytical_plan is not None:
        from .validation import validate_scoped_report
        return validate_scoped_report(report, bundle)
    results = [execute_tool(name, report.question, context, bundle)
               for name in select_tools(report.question)]
    if [t.tool for t in report.tool_trace] != select_tools(report.question):
        errors.append("tool_routing_mismatch")
    canonical = {fact.fact_id: fact for result in results for fact in result.evidence}
    facts = {fact.fact_id: fact for fact in report.evidence}
    if len(facts) != len(report.evidence):
        errors.append("duplicate_report_evidence")
    for key, fact in facts.items():
        if key not in canonical or fact != canonical[key]:
            errors.append("recomputed_fact_mismatch:" + key)
    if set(report.source_refs) != {fact.source for fact in report.evidence}:
        errors.append("source_refs_mismatch")
    if report.status == "failed":
        return [*errors, "failed_execution"]
    limits = _find_required_limits(results, bundle, report.question)
    candidates = _include_required_trend_evidence(
        report.question, _evidence_candidates(report.question, list(canonical.values())), list(canonical.values()))
    insufficiency = _insufficiency_finding(report.question, results)
    finding = insufficiency or _derive_finding(candidates, results)
    if report.finding != finding or report.decision != ("insufficient_data" if insufficiency else "answer"):
        errors.append("python_decision_mismatch")
    expected_ids = {fact.fact_id for fact in candidates}
    if not expected_ids.issubset(facts):
        errors.append("required_report_evidence_missing")
    if len(report.insights) != 1:
        return [*errors, "unexpected_insight_count"]
    insight = report.insights[0]
    if set(insight.evidence_fact_ids) != expected_ids or insight.finding != finding:
        errors.append("insight_bindings_mismatch")
    if set(insight.limitation_codes) != set(limits):
        errors.append("limitation_codes_mismatch")
    if report.limitations != [_LIMITATIONS_FR[code] for code in limits]:
        errors.append("limitation_text_mismatch")
    ordered_facts = [canonical[key] for key in insight.evidence_fact_ids if key in canonical]
    python_text = _render_python_insight(report.question, ordered_facts, results, insufficient_finding=insufficiency)
    if report.status == "validated":
        if report.model != "granite3.3:2b" or report.validation.status != "validated":
            errors.append("validated_status_mismatch")
        code = _default_recommendation(candidates, results, report.question)
        policy = build_policy(finding, candidates, limits, _RECOMMENDATIONS_FR[code], code)
        draft, validation = validate_draft(raw_response or "", policy)
        if validation.status != "validated" or draft is None:
            errors.append("saved_draft_rejected")
        elif insight.insight != draft.insight + " " + python_text or insight.recommendation != draft.recommendation:
            errors.append("final_text_materialization_mismatch")
    elif insight.insight != python_text:
        errors.append("fallback_text_mismatch")
    return errors


def verify_campaign(destination: Path, context: AnalysisContext, previous: Path,
                    failed_run: Path) -> dict[str, Any]:
    destination, previous, failed_run = [
        (path if path.is_absolute() else context.project_root / path).resolve()
        for path in (destination, previous, failed_run)
    ]
    rows = []
    old_metrics = json.loads((previous / "metrics.json").read_text(encoding="utf-8"))
    current_metrics = json.loads((destination / "metrics.json").read_text(encoding="utf-8"))
    for scenario in REAL_SCENARIOS:
        saved = json.loads((destination / f"{scenario['id']}.json").read_text(encoding="utf-8"))
        report = BIAnalysisReport.model_validate(saved["agent"])
        artifact = destination / saved["execution"]["audit_dir"]
        events = [json.loads(line) for line in (artifact / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        responses = [e for e in events if e["event"] == "llm_response"]
        errors = verify_report(report, context, responses[-1]["raw_response"] if responses else None)
        coverage = _factual_metrics(report, scenario["expected_metrics"])
        if not coverage["source_and_denominator_complete"]:
            errors.append("expected_metric_coverage_incomplete")
        old = json.loads((previous / f"{scenario['id']}.json").read_text(encoding="utf-8"))
        rows.append(dict(scenario_id=scenario["id"], verification_passed=not errors, errors=errors,
                         previous_status=old["agent"]["status"], current_status=report.status,
                         previous_elapsed_ms=old["agent"]["timing_ms"]["total"],
                         current_elapsed_ms=report.timing_ms["total"],
                         previous_expected_metric_present=old["factual_metrics"]["expected_metric_present"],
                         current_factual_metrics=coverage,
                         timing_ms=report.timing_ms,
                         ollama_metrics=[e["ollama_metrics"] for e in responses]))
    failed = json.loads((failed_run / "report.json").read_text(encoding="utf-8"))
    result = dict(automatic_checks_only=True, human_evaluation_included=False,
                  all_scenarios_verified=all(row["verification_passed"] for row in rows),
                  previous_campaign=str(previous), previous_metrics={k: v for k, v in old_metrics.items() if not k.startswith("human_")},
                  current_metrics=current_metrics, scenarios=rows,
                  failed_demo_reference=dict(execution_id=failed["execution_id"],
                                             status=failed["status"], errors=failed["errors"],
                                             elapsed_ms=failed["timing_ms"]["total"]))
    (destination / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    historical = [*previous.rglob("*.json"), *previous.rglob("*.jsonl"), *previous.rglob("*.csv"),
                  *failed_run.glob("*")]
    hashes = {str(p.relative_to(context.project_root) if p.is_relative_to(context.project_root) else p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in historical if p.is_file()}
    (destination / "historical_reference_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    for name, model in (("BIAnalysisReport.v1.schema.json", BIAnalysisReport), ("GraniteDraft.v2.schema.json", LLMDraft)):
        (destination / name).write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--failed-run", type=Path, required=True)
    args = parser.parse_args()
    result = verify_campaign(args.campaign, AnalysisContext(project_root=args.root), args.previous, args.failed_run)
    print(json.dumps(dict(all_scenarios_verified=result["all_scenarios_verified"],
                          scenarios=[dict(id=r["scenario_id"], errors=r["errors"]) for r in result["scenarios"]]), indent=2))
    return 0 if result["all_scenarios_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
