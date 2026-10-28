"""Recheck saved demo/campaign artifacts without inference and archive reproducibility."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packaging.requirements import Requirement
from bi_analyst.analytics import load_real_bundle
from bi_analyst.consumers import check_consumers
from bi_analyst.decision_evaluation import verify_decision_report
from bi_analyst.demo_evaluation import DRILLDOWN, audit_summary, summarize
from bi_analyst.demo_preflight import preflight
from bi_analyst.demo_service import load_recording, load_scenarios
from bi_analyst.models import BIAnalysisReport, AnalysisContext
from bi_analyst.verification import verify_report
from benchmark_llm.runner import environment_report

BASE = ROOT / "outputs/bi_analyst_demo_readiness/20261028"
CAMPAIGN = BASE / "granite_campaign"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory for this check; existing campaigns are never overwritten.")
    args = parser.parse_args()
    destination = args.output or ROOT / "outputs/project_cleanup" / (
        "demo_delivery_check_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    if not destination.is_absolute():
        destination = ROOT / destination
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    metrics = read(CAMPAIGN / "metrics.json")
    context = AnalysisContext(project_root=ROOT)
    bundle = load_real_bundle(ROOT)
    checks = []
    for row in metrics["rows"]:
        report = BIAnalysisReport.model_validate(read(CAMPAIGN / row["report_file"]))
        events = [json.loads(line) for line in (CAMPAIGN / row["audit_file"]).read_text(encoding="utf-8").splitlines()]
        check_consumers(report)
        if report.contract_version.endswith("v2"):
            errors = verify_decision_report(report, bundle, events)["errors"]
        else:
            responses = [event for event in events if event["event"] == "llm_response"]
            errors = verify_report(report, context, responses[-1]["raw_response"] if responses else None)
        if audit_summary(report, events) != row["audit"]:
            errors.append("saved_audit_metrics_mismatch")
        if report.timing_ms["total"] != row["report_elapsed_ms"]:
            errors.append("saved_timing_mismatch")
        checks.append(dict(scenario_id=row["scenario_id"], repetition=row["repetition"], errors=errors, passed=not errors))
    for key, selected in (("core_six", [row for row in metrics["rows"] if row["scenario_id"] != DRILLDOWN["id"]]),
                          ("additional_drilldown", [row for row in metrics["rows"] if row["scenario_id"] == DRILLDOWN["id"]]),
                          ("all_genuine_runs", metrics["rows"])):
        if metrics[key] != summarize(selected):
            raise ValueError("Aggregate metrics mismatch: " + key)
    recordings = []
    for scenario in load_scenarios(ROOT):
        run = load_recording(scenario["id"], ROOT)
        info = read(run.path / "metadata.json")
        if "source_report" in info:
            if hashlib.sha256((ROOT / info["source_report"]).read_bytes()).hexdigest() != info["source_report_sha256"]:
                raise ValueError("Historical recording source changed.")
        recordings.append(dict(scenario_id=scenario["id"], execution_id=run.report.execution_id,
                               mode=run.mode, source_mode=run.display_metadata["source_mode"], verified=True))
    controlled = load_recording("controlled_rejection", ROOT)
    injection = audit_summary(controlled.report, controlled.events)
    write(destination / "controlled_injection_metrics.json", dict(excluded_from_genuine_model_metrics=True, **injection))
    raw_log = (BASE / "tests_full.log").read_bytes()
    log = raw_log.decode("utf-16" if raw_log.startswith((b'\xff\xfe', b'\xfe\xff')) else "utf-8-sig", errors="replace")
    counts = re.findall(r"Ran (\d+) tests? in ([\d.]+)s", log)
    if not counts or not re.search(r"\nOK\s*$", log):
        raise ValueError("Full test suite is not successful.")
    test_results = dict(count=int(counts[-1][0]), elapsed_seconds=float(counts[-1][1]), passed=True,
                        log="outputs/bi_analyst_demo_readiness/20261028/tests_full.log")
    pre = preflight(ROOT, check_ollama=True, check_data=True)
    write(destination / "preflight.json", pre)
    result = dict(no_inference_performed=True, all_verified=all(row["passed"] for row in checks),
                  campaign=checks, recordings=recordings, tests=test_results,
                  checked_at_utc=datetime.now(timezone.utc).isoformat())
    write(destination / "delivery_verification.json", result)
    packages, pending = {}, ["pandas", "numpy", "psutil", "jsonschema", "pydantic", "streamlit"]
    while pending:
        name = pending.pop()
        canonical = name.lower().replace("_", "-")
        if canonical in packages:
            continue
        dist = metadata.distribution(name)
        packages[canonical] = dict(name=dist.metadata["Name"], version=dist.version)
        for text in dist.requires or []:
            requirement = Requirement(text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    lock = "# Resolved local demo dependency graph: Windows / Python 3.13.2\n" + "\n".join(
        f"{row['name']}=={row['version']}" for _, row in sorted(packages.items())) + "\n"
    (destination / "requirements-demo.lock.txt").write_text(lock, encoding="utf-8")
    write(destination / "environment.json", dict(environment=environment_report(ROOT), packages=packages, ollama=pre["ollama"]))
    paths = [*ROOT.joinpath("bi_analyst").glob("*.py"), *ROOT.joinpath("tests").glob("test_*.py"),
             *ROOT.joinpath("schemas").glob("*.json"), *ROOT.joinpath("demo").glob("*.*"),
             ROOT / "README.md", ROOT / "archive/documentation/BI_ANALYST_DEMO_READINESS.md", ROOT / "requirements-demo.txt",
             ROOT / "requirements-demo.lock.txt", ROOT / "tools/build_demo_recordings.py", Path(__file__)]
    write(destination / "delivery_manifest.json", dict(model="granite3.3:2b", no_human_scores=True,
          source_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()},
          data_sha256=bundle["hashes"], campaign="outputs/bi_analyst_demo_readiness/20261028/granite_campaign",
          recording_manifest_sha256=hashlib.sha256((ROOT / "demo/recordings/manifest.json").read_bytes()).hexdigest(),
          tests=test_results))
    print(json.dumps(dict(all_verified=result["all_verified"], genuine_runs=len(checks), recordings=len(recordings), tests=test_results["count"], preflight_ready=pre["ready"], output=str(destination)), indent=2))
    return 0 if result["all_verified"] and pre["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
