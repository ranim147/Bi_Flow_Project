"""Real and explicit synthetic decision-support validation, Granite only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from .analytics import load_real_bundle
from .decision import analyze_synthetic_decision
from .decision_fixtures import SCENARIOS, synthetic_case
from .decision_tools import build_decision_analysis
from .investigation import materialize_plan, policy_for, validate_plan
from .models import AnalysisContext, BIAnalysisReport, DecisionDraft
from .orchestrator import analyze

DEMO_QUESTION = ("Les délais de résolution augmentent dans un arrondissement. Identifie les segments qui contribuent à cette dégradation, "
                 "propose des hypothèses d'investigation et indique précisément où l'équipe doit regarder pour en comprendre les raisons.")
REAL_CASES = [("real_requested_demo", DEMO_QUESTION),
              ("real_brooklyn", "Investiguer la hausse des délais à Brooklyn; préciser les catégories, agences et périodes prioritaires."),
              ("real_no_increase", "Investiguer la dégradation de la moyenne des délais à Manhattan sans supposer une hausse.")]


def verify_decision_report(report: BIAnalysisReport, bundle: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    facts, details, _ = build_decision_analysis(report.question, bundle)
    canonical = {fact.fact_id: fact for fact in facts}
    errors = []
    ids = [fact.fact_id for fact in report.evidence]
    if report.data_kind != bundle["kind"] or len(report.evidence) != len(facts) or len(ids) != len(set(ids)) or set(ids) != set(canonical):
        errors.append("data_kind_or_fact_count_mismatch")
    if set(report.source_refs) != {fact.source for fact in facts}:
        errors.append("source_references_mismatch")
    for fact in report.evidence:
        if fact.fact_id not in canonical or canonical[fact.fact_id] != fact:
            errors.append("recomputed_evidence_mismatch:" + fact.fact_id)
    if len(report.decision_support) != len(details["problems"]):
        errors.append("problem_count_mismatch")
    if len(report.insights) != len(details["problems"]):
        errors.append("summary_count_mismatch")
    for index, (plan, problem) in enumerate(zip(report.decision_support, details["problems"])):
        policy = policy_for(problem, details["periods"])
        if plan.origin == "granite_validated":
            responses = [event for event in events if event["event"] == "llm_response" and event["problem_id"] == problem["problem_id"]]
            draft, result = validate_plan(responses[-1]["raw_response"] if responses else "", policy)
            if result.status != "validated":
                errors.append("saved_granite_draft_rejected:" + problem["problem_id"])
                continue
        else:
            draft = DecisionDraft.model_validate(policy.example())
        expected = materialize_plan(draft, policy, validated=plan.origin == "granite_validated")
        if plan != expected:
            errors.append("plan_scope_or_operation_mismatch:" + plan.problem_id)
        if not set(plan.evidence_ids).issubset(canonical):
            errors.append("unbound_plan_evidence")
        if index < len(report.insights):
            summary = report.insights[index]
            text = plan.problem + " Périmètre : " + json.dumps(plan.priority_scope.filters, ensure_ascii=False) + "; périodes : " + str(tuple(details["periods"]) if details["periods"] else None)
            action = plan.recommended_actions[0].action + " Cible : " + json.dumps(plan.priority_scope.filters, ensure_ascii=False)
            if summary.insight != text or summary.recommendation != action or summary.evidence_fact_ids != plan.evidence_ids or summary.finding != problem["kind"]:
                errors.append("legacy_summary_materialization_mismatch")
    if report.status == "validated" and any(plan.origin != "granite_validated" for plan in report.decision_support):
        errors.append("fallback_hidden_as_validated")
    if report.decision_support and any(problem["kind"] not in {"small_scope", "insufficient_history"} for problem in details["problems"]):
        expected_decision = "answer"
    else:
        expected_decision = "insufficient_data"
    if report.decision != expected_decision:
        errors.append("unsupported_business_decision")
    if report.finding != details["problems"][0]["kind"]:
        errors.append("unsupported_finding")
    return dict(passed=not errors, errors=errors, truthful_findings=not any("finding" in e or "decision" in e for e in errors),
                evidence_references_valid=not any("evidence" in e for e in errors),
                scope_and_period_precision=not any("scope" in e for e in errors),
                hypotheses_explicitly_unverified=all(h.status == "unverified" for p in report.decision_support for h in p.investigation_hypotheses),
                concrete_steps_present=bool(report.decision_support) and all(step.fields and step.filters and step.comparison for p in report.decision_support for step in p.investigation_steps) if details["periods"] else bool(report.decision_support),
                both_verification_outcomes_present=all(v.supports and v.refutes for p in report.decision_support for v in p.expected_verification))


def run_decision_evaluation(context: AnalysisContext, output: Path, *, client=None,
                            include_synthetic=True, include_real=True, use_llm=True,
                            scenario_ids: set[str] | None = None) -> dict[str, Any]:
    real_bundle = load_real_bundle(context.project_root, context.dataset_version) if include_real else None
    cases = [(name, "real", question, None) for name, question in REAL_CASES] if include_real else []
    if include_synthetic:
        for name in SCENARIOS:
            frame, metadata, question = synthetic_case(name)
            cases.append((name, "synthetic", question, (frame, metadata)))
    if scenario_ids is not None:
        if not scenario_ids or not scenario_ids.issubset({row[0] for row in cases}):
            raise ValueError("Requested scenarios are not available with these data-kind filters.")
        cases = [row for row in cases if row[0] in scenario_ids]
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, kind, question, fixture in cases:
        before = time.perf_counter()
        if kind == "real":
            report = analyze(question, context, client=client, use_llm=use_llm, audit_root=output / "audit")
            bundle = real_bundle
        else:
            report = analyze_synthetic_decision(question, *fixture, context, client=client,
                                               use_llm=use_llm, audit_root=output / "audit")
            source = "synthetic://decision-fixture/requests.csv"
            bundle = dict(kind="synthetic", clean=fixture[0], metadata=fixture[1], source_paths={"clean": source},
                          hashes={source: hashlib.sha256(fixture[0].to_csv(index=False).encode()).hexdigest()})
        elapsed = (time.perf_counter() - before) * 1000
        audit_dir = output / "audit" / report.execution_id
        events = [json.loads(line) for line in (audit_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        checks = verify_decision_report(report, bundle, events) if report.status != "failed" else {"passed": False, "errors": report.errors}
        responses = [event for event in events if event["event"] == "llm_response"]
        row = dict(scenario_id=name, data_kind=kind, status=report.status, decision=report.decision,
                   finding=report.finding, automatic_checks=checks, elapsed_ms=elapsed,
                   execution_id=report.execution_id, accepted_without_fallback=report.status == "validated",
                   problem_count=len(report.decision_support), attempts=len(responses),
                   correction_count=sum(event["event"] == "llm_request" and event["attempt"] == 2 for event in events),
                   fallback_count=sum(plan.origin == "python_fallback" for plan in report.decision_support))
        (output / f"{name}.json").write_text(json.dumps(dict(result=row, report=report.model_dump(mode="json")), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        rows.append(row)
        (output / "progress.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{name} [{kind}]: {report.status}, {report.finding}, verified={checks['passed']}, {elapsed / 1000:.2f}s", flush=True)
    metrics = dict(model="granite3.3:2b", contract="biflow-bi-analyst-v2", model_inference_enabled=use_llm,
                   automatic_evaluation_only=True, human_evaluation_included=False, scenarios=rows)
    for kind in ("real", "synthetic"):
        subset = [row for row in rows if row["data_kind"] == kind]
        metrics[kind] = dict(scenario_count=len(subset),
                             verified_count=sum(row["automatic_checks"]["passed"] for row in subset),
                             granite_validated_without_fallback_rate=sum(row["accepted_without_fallback"] for row in subset) / len(subset) if subset and use_llm else None,
                             mean_elapsed_ms=sum(row["elapsed_ms"] for row in subset) / len(subset) if subset else None,
                             corrections=sum(row["correction_count"] for row in subset), fallbacks=sum(row["fallback_count"] for row in subset))
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def replay_decision_evaluation(context: AnalysisContext, output: Path) -> dict[str, Any]:
    """Read saved reports and recalculate checks without contacting Ollama."""
    manifest = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    real_bundle = None
    rows = []
    for case in manifest["scenarios"]:
        saved = json.loads((output / f"{case['scenario_id']}.json").read_text(encoding="utf-8"))
        report = BIAnalysisReport.model_validate(saved["report"])
        if case["data_kind"] == "real":
            if real_bundle is None:
                real_bundle = load_real_bundle(context.project_root, context.dataset_version)
            bundle = real_bundle
        else:
            frame, metadata, _ = synthetic_case(case["scenario_id"])
            source = "synthetic://decision-fixture/requests.csv"
            bundle = dict(kind="synthetic", clean=frame, metadata=metadata, source_paths={"clean": source},
                          hashes={source: hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()})
        events = [json.loads(line) for line in (output / "audit" / report.execution_id / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        checks = verify_decision_report(report, bundle, events)
        rows.append(dict(scenario_id=case["scenario_id"], data_kind=case["data_kind"], **checks))
    result = dict(all_passed=bool(rows) and all(row["passed"] for row in rows), inference_enabled=False, scenarios=rows)
    (output / "replay_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-only", action="store_true")
    parser.add_argument("--synthetic-only", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="Recalculate saved reports; no Ollama calls.")
    parser.add_argument("--scenario", action="append", choices=[name for name, _ in REAL_CASES] + list(SCENARIOS))
    args = parser.parse_args()
    if args.real_only and args.synthetic_only:
        parser.error("Choose at most one data-kind filter.")
    if args.verify_only:
        result = replay_decision_evaluation(AnalysisContext(project_root=args.root), args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["all_passed"] else 1
    metrics = run_decision_evaluation(AnalysisContext(project_root=args.root), args.output,
                                      include_synthetic=not args.real_only, include_real=not args.synthetic_only,
                                      use_llm=not args.baseline,
                                      scenario_ids=set(args.scenario) if args.scenario else None)
    print(json.dumps({kind: metrics[kind] for kind in ("real", "synthetic")}, indent=2))
    return 0 if all(row["automatic_checks"]["passed"] for row in metrics["scenarios"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
