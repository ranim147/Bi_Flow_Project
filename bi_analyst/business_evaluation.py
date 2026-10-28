"""Reproduce the three reported business failures with real Granite, separately from benchmarks."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from .analytics import FILES, load_real_bundle, prepare_investigation_frame
from .demo_service import run_live
from .validation import validate_scoped_report

QUESTIONS = {
    "A": "Analyse les variations des délais moyens de clôture entre août et septembre 2026. Identifie les segments qui contribuent le plus à une hausse éventuelle, puis propose des investigations précises pour en comprendre les raisons possibles.",
    "B": "Identifie d'abord l'arrondissement ayant le délai médian de résolution le plus élevé parmi les groupes éligibles. Dans cet arrondissement uniquement, compare les types de plaintes et les agences avec leurs médianes et effectifs. Indique les segments à investiguer en priorité, sans effectuer d'analyse temporelle.",
    "7": "Prouve que l'arrondissement ayant les délais les plus élevés souffre d'un manque de personnel et indique combien d'agents supplémentaires il faut recruter.",
}


def independent_checks(name, report, analysis, bundle):
    """Direct pandas oracle, independent of the controller's grouping and decomposition."""
    errors = validate_scoped_report(report, bundle)
    data = prepare_investigation_frame(bundle["clean"], bundle["metadata"]["extracted_at_utc"])
    valid = data.loc[data.valid_delay]
    oracle = {}
    if name == "A":
        means = valid.groupby("closure_month").resolution_days.agg(["mean", "count"])
        before, after = means.loc["2026-08"], means.loc["2026-09"]
        delta = float(after["mean"] - before["mean"])
        oracle = dict(mean_baseline=float(before["mean"]), mean_current=float(after["mean"]),
                      n_baseline=int(before["count"]), n_current=int(after["count"]), delta=delta)
        actual = analysis.get("period_comparison", {})
        if actual.get("periods") != ["2026-08", "2026-09"] or abs(actual.get("delta", float("inf")) - delta) > 1e-9:
            errors.append("independent_global_mean_mismatch")
        groups = analysis.get("tables", {}).get("global_decomposition", {}).get("groups", [])
        segment_sums = valid.loc[valid.closure_month.isin(["2026-08", "2026-09"])].groupby(
            ["borough", "complaint_type", "agency", "closure_month"]).resolution_days.sum()
        total = 0.0
        for group in groups:
            key = (group["borough"], group["complaint_type"], group["agency"])
            sums = [float(segment_sums.get((*key, p), 0)) for p in ("2026-08", "2026-09")]
            contribution = sums[1] / after["count"] - sums[0] / before["count"]
            total += contribution
            if abs(group["contribution_days"] - contribution) > 1e-9:
                errors.append("independent_contribution_mismatch")
        if abs(total - delta) > 1e-9:
            errors.append("independent_decomposition_identity_mismatch")
        priorities = analysis.get("tables", {}).get("priority_segments", [])
        if any(min(g["n_baseline"], g["n_current"]) < 30 or not g["rankable"] for g in priorities):
            errors.append("small_group_operationally_ranked")
        if priorities != sorted(priorities, key=lambda g: (-g["contribution_days"], g["borough"], g["complaint_type"], g["agency"])):
            errors.append("contribution_ranking_mismatch")
        oracle["total_contributions"] = total
        oracle["top_segments"] = priorities[:3]
    elif name == "B":
        ranking = valid.loc[valid.borough.isin(["BROOKLYN", "BRONX", "MANHATTAN", "QUEENS", "STATEN ISLAND"])].groupby("borough").resolution_days.agg(["median", "count"])
        ranking = ranking.loc[ranking["count"].ge(30)].sort_values(["median", "borough"], ascending=[False, True])
        winner = str(ranking.index[0])
        oracle = dict(winner=winner, ranking=ranking.reset_index().to_dict("records"),
                      drilldowns={dim: valid.loc[valid.borough.eq(winner)].groupby(dim).resolution_days.agg(["median", "count"]).reset_index().to_dict("records") for dim in ("complaint_type", "agency")})
        if report.analytical_plan.locked_filters != {"borough": winner}:
            errors.append("independent_winner_scope_mismatch")
        for section in report.insights[1:]:
            for fid in section.evidence_fact_ids:
                fact = next(f for f in report.evidence if f.fact_id == fid)
                if json.loads(fact.scope).get("borough") != winner or fact.metric != "median_resolution_days" or fact.reference_period:
                    errors.append("independent_drilldown_scope_metric_or_period_mismatch")
    elif name == "7":
        text = " ".join(s.insight for s in report.insights)
        if report.decision != "insufficient_data" or "Je ne peux pas démontrer" not in text or "Je ne peux pas calculer" not in text:
            errors.append("missing_explicit_causal_or_staffing_refusal")
        oracle = dict(required_data=analysis.get("capabilities"), numerical_staffing_estimate=None)
    return dict(passed=not errors, errors=sorted(set(errors)), oracle=oracle)


def verify_ui(runs, root):
    """Replay actual reports through Streamlit's UI harness; no extra inference."""
    from streamlit.testing.v1 import AppTest
    rows = []
    with patch("bi_analyst.demo_service.run_live") as live:
        app = AppTest.from_file(str(root / "demo/streamlit_app.py"), default_timeout=30).run()
        app.radio(key="mode_choice").set_value("live").run()
        for name, run in runs:
            app.text_area(key="question").set_value(run.report.question).run()
            cleared = "active_run" not in app.session_state
            live.return_value = run
            app.button(key="run_live").click().run()
            visible = app.session_state["active_run"].report
            captions = " ".join(str(item.value) for item in app.caption)
            texts = " ".join(str(item.value) for item in app.markdown)
            passed = (not list(app.exception) and cleared and visible.execution_id == run.report.execution_id
                      and visible.question == run.report.question and run.report.execution_id in captions
                      and all(s.insight in texts for s in run.report.insights))
            if live.call_args.args != (run.report.question, root):
                passed = False
            rows.append(dict(test=name, execution_id=visible.execution_id, passed=passed, previous_result_cleared=cleared,
                             exceptions=[str(e.value) for e in app.exception]))
    return dict(method="Streamlit AppTest replay of real saved live reports; no additional model calls", passed=all(r["passed"] for r in rows), scenarios=rows)


def run_campaign(root: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=False)
    bundle = load_real_bundle(root)
    historical_paths = [p for folder in ("benchmark_llm/results", "outputs/llm_benchmark_local", "outputs/bi_analyst_finalization", "outputs/decision_support", "outputs/demo_live", "demo/recordings", "notebooks")
                        if (root / folder).exists() for p in (root / folder).rglob("*") if p.is_file()]
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in historical_paths}
    (destination / "historical_hashes.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    rows, runs = [], []
    for name, question in QUESTIONS.items():
        run = run_live(question, root, audit_root=destination / "runs")
        report = run.report
        checks = independent_checks(name, report, run.analysis or {}, bundle) if report.status != "failed" else dict(passed=False, errors=report.errors)
        (run.path / "verification.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        responses = [e for e in run.events if e["event"] == "llm_response"]
        row = dict(test=name, question=question, execution_id=report.execution_id,
                   analytical_plan=report.analytical_plan.model_dump(mode="json") if report.analytical_plan else None,
                   tools=[t.tool for t in report.tool_trace], evidence_ids=[f.fact_id for f in report.evidence],
                   granite_responses=[e["raw_response"] for e in responses],
                   corrections=sum(e["event"] == "llm_request" and e["attempt"] > 1 for e in run.events),
                   fallback=report.status == "fallback", status=report.status, decision=report.decision,
                   duration_seconds=report.timing_ms.get("total", 0) / 1000, verification=checks,
                   audit_dir=str(run.path.relative_to(destination)))
        rows.append(row); runs.append((name, run))
        (destination / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{name}: {report.status}, {report.decision}, {row['duration_seconds']:.2f}s, verified={checks['passed']}, id={report.execution_id}", flush=True)
    ui = verify_ui(runs, root)
    (destination / "streamlit_verification.json").write_text(json.dumps(ui, ensure_ascii=False, indent=2), encoding="utf-8")
    unchanged = all(hashlib.sha256((root / path).read_bytes()).hexdigest() == digest for path, digest in before.items())
    summary = dict(model="granite3.3:2b", real_inference=True, all_passed=all(r["verification"]["passed"] for r in rows) and ui["passed"] and unchanged,
                   historical_files_unchanged=unchanged, source_hashes=bundle["hashes"], streamlit=ui, results=rows)
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def verify_campaign(root: Path, destination: Path):
    """Recheck final reports, saved Granite completions and historical hashes without inference."""
    from .draft import validate_draft
    from .models import BIAnalysisReport
    from .scoped import materialize_sections, scoped_policy
    bundle = load_real_bundle(root)
    saved = json.loads((destination / "results.json").read_text(encoding="utf-8"))
    rows = []
    for row in saved:
        folder = destination / row["audit_dir"]
        report = BIAnalysisReport.model_validate_json((folder / "report.json").read_text(encoding="utf-8"))
        details = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (folder / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        checks = independent_checks(row["test"], report, details, bundle)
        errors = checks["errors"].copy()
        requests = [e for e in events if e["event"] == "request_received"]
        if len(requests) != 1 or requests[0]["question"] != QUESTIONS[row["test"]] or report.question != QUESTIONS[row["test"]] or requests[0]["execution_id"] != report.execution_id:
            errors.append("saved_request_identity_mismatch")
        responses = [e for e in events if e["event"] == "llm_response"]
        if report.status == "validated":
            policy = scoped_policy(details, report.evidence)
            draft, validation = validate_draft(responses[-1]["raw_response"] if responses else "", policy)
            if validation.status != "validated" or draft is None:
                errors.append("saved_granite_draft_rejected")
            elif report.insights != materialize_sections(report.execution_id, details, draft):
                errors.append("saved_granite_final_text_binding_mismatch")
            if not responses or responses[-1].get("response_model") != "granite3.3:2b" or responses[-1].get("done") is False or responses[-1].get("done_reason") == "length":
                errors.append("saved_model_identity_or_completion_mismatch")
        rows.append(dict(test=row["test"], execution_id=report.execution_id, passed=not errors, errors=errors))
    original = json.loads((destination / "historical_hashes.json").read_text(encoding="utf-8"))
    unchanged = all(hashlib.sha256((root / path).read_bytes()).hexdigest() == digest for path, digest in original.items())
    result = dict(model_calls=0, all_passed=all(r["passed"] for r in rows) and unchanged,
                  historical_files_unchanged=unchanged, results=rows)
    (destination / "replay_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (verify_campaign if args.verify_only else run_campaign)(args.root.resolve(), args.output.resolve())
    print(json.dumps(dict(all_passed=result["all_passed"], historical_files_unchanged=result["historical_files_unchanged"])))
    raise SystemExit(0 if result["all_passed"] else 1)
