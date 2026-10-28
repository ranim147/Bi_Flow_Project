"""Real-data evaluation, population-change disclosure and saved Granite evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import pandas as pd

from bi_analyst.analytics import load_real_bundle, mann_kendall_trend, scan_monthly_anomalies
from bi_analyst.business_evaluation import QUESTIONS, independent_checks, verify_ui
from bi_analyst.demo_service import run_live
from bi_analyst.models import AnalysisContext
from bi_analyst.verification import verify_report
from .common import log, read, sha, write
from .loader import version_path


def evaluate(root: Path, version: str, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    context = AnalysisContext(project_root=root, dataset_version=version)
    bundle = load_real_bundle(root, version)
    old = load_real_bundle(root)
    comparisons = dict(old_period={k:old["metadata"].get(k) for k in ("period_start_inclusive","period_end_exclusive","extracted_at_utc","sample_method")},
                       new_period={k:bundle["metadata"].get(k) for k in ("period_start_inclusive","period_end_exclusive","extracted_at_utc","sample_method","source_timezone")},
                       old_summary=old["summary"].to_dict("records"), new_summary=bundle["summary"].to_dict("records"),
                       shared_month_official_counts=[dict(month=m, old=n,new=bundle["metadata"]["population_monthly_counts"][m]) for m,n in old["metadata"]["population_monthly_counts"].items() if m in bundle["metadata"]["population_monthly_counts"]],
                       interpretation="Different creation cohorts, sampling designs, reporting timezones and follow-up cutoffs. Differences in delay/closure KPIs are not service improvements. Official shared-month counts may be revised by the source. Detailed statistics are unweighted sample statistics, not population estimates.")
    write(output / "old_new_comparison.json", comparisons)
    monthly = bundle["trend"]
    trend = mann_kendall_trend(monthly)
    anomalies = scan_monthly_anomalies(monthly)
    write(output / "statistical_checks.json", dict(complete_periods=len(monthly), trend=trend,
           anomaly_tests=json.loads(anomalies.to_json(orient="records")),
           cautions=["Seasonal trend does not correct serial autocorrelation", "Z-score/MAD anomaly scans do not adjust seasonal patterns or multiple testing", "Signals trigger investigation; no causal or staffing inference"]))
    scenarios = {**QUESTIONS, "trend":"Analyse la tendance mensuelle des volumes de demandes.",
                 "anomalies":"Y a-t-il un pic anormal de plaintes dans les séries mensuelles ?",
                 "rates":"Compare les taux de clôture par agence, sans analyse temporelle."}
    rows, runs = [], []
    for name, question in scenarios.items():
        before = time.perf_counter()
        run = run_live(question, root, audit_root=output / "runs", dataset_version=version)
        report = run.report
        responses = [e for e in run.events if e["event"] == "llm_response"]
        errors = report.errors.copy() if report.status == "failed" else verify_report(report, context, responses[-1]["raw_response"] if responses else None)
        if name in QUESTIONS and report.status != "failed":
            extra = independent_checks(name, report, run.analysis or {}, bundle)
            errors.extend(extra["errors"])
            write(run.path / "independent_verification.json", extra)
        if name == "trend" and report.status != "failed":
            facts = [f for f in report.evidence if f.metric == "monthly_trend_seasonal_kendall_tau"]
            if trend["status"] in {"significant","not_significant","no_variation"} and (not facts or abs(facts[0].value-trend["tau"])>1e-9):
                errors.append("independent_trend_mismatch")
        if name == "anomalies" and report.status != "failed":
            count = int(anomalies.status.eq("anomaly").sum())
            if count and not any(f.metric == "statistical_anomaly_score" for f in report.evidence):
                errors.append("independent_anomaly_coverage_mismatch")
        if name == "rates" and report.status != "failed":
            expected = bundle["clean"].loc[bundle["clean"].created_date.notna()].groupby("agency").state.agg(lambda s:100*s.eq("closed").sum()/len(s))
            for fact in report.evidence:
                if fact.metric == "closure_rate":
                    agency = json.loads(fact.scope)["agency"]
                    if abs(expected.loc[agency]-fact.value)>1e-9:
                        errors.append("independent_rate_denominator_mismatch")
        row = dict(scenario=name, dataset_version=version, question=question, execution_id=report.execution_id,
                   status=report.status, decision=report.decision, finding=report.finding, passed=not errors, errors=sorted(set(errors)),
                   wall_seconds=time.perf_counter()-before, report_timing_ms=report.timing_ms,
                   tools=[t.tool for t in report.tool_trace], fallback=report.status=="fallback",
                   corrections=sum(e["event"]=="llm_request" and e["attempt"]>1 for e in run.events),
                   raw_granite_responses=[e["raw_response"] for e in responses], audit_dir=run.path.relative_to(output).as_posix())
        rows.append(row); runs.append((name,run))
        write(output / "results.json", rows)
        print(f"{name}: {report.status}/{report.decision}, verified={not errors}, {row['wall_seconds']:.2f}s, id={report.execution_id}",flush=True)
    ui = verify_ui(runs,root)
    write(output / "streamlit_verification.json",ui)
    result=dict(model="granite3.3:2b", model_calls_real=True, dataset_version=version,
                prepared_manifest_sha256=sha(version_path(root,version)/"processed/manifest.json"),
                all_passed=all(r["passed"] for r in rows) and ui["passed"], results=rows, streamlit=ui)
    write(output / "summary.json",result)
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path.cwd()); parser.add_argument("--version",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=evaluate(args.root.resolve(),args.version,args.output.resolve())
    print(json.dumps(dict(all_passed=result["all_passed"])),flush=True)
    raise SystemExit(0 if result["all_passed"] else 1)
