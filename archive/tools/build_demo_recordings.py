"""Package genuine saved executions; injected validation tests are labelled separately."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bi_analyst.analytics import load_real_bundle
from bi_analyst.decision_evaluation import verify_decision_report
from bi_analyst.demo_service import load_recording, run_rejection_demo
from bi_analyst.models import AnalysisContext, BIAnalysisReport
from bi_analyst.verification import verify_report


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Replace this demo package, never source campaigns.")
    args = parser.parse_args()
    base = ROOT / "demo/recordings"
    if (base / "manifest.json").exists() and not args.refresh:
        raise FileExistsError("Le dossier contient déjà la démonstration; utiliser --refresh explicitement.")
    base.mkdir(parents=True, exist_ok=True)
    context = AnalysisContext(project_root=ROOT)
    bundle = load_real_bundle(ROOT)
    descriptive = ROOT / "outputs/bi_analyst_finalization/granite_v2_release_20261028"
    decision = ROOT / "outputs/decision_support/granite_investigation_20261028"
    sources = [
        ("compare_boroughs", descriptive / "borough_resolution_delays.json", "agent"),
        ("drilldown_brooklyn", decision / "real_brooklyn.json", "report"),
        ("insufficient_history", descriptive / "insufficient_history.json", "agent"),
    ]
    manifest = dict(format="biflow-demo-recordings-v1", created_at_utc=datetime.now(timezone.utc).isoformat(),
                    note="Restitutions de véritables exécutions locales; l'injection contrôlée est distincte.", recordings=[])

    def register(scenario_id, report, folder):
        paths = [path for path in folder.iterdir() if path.is_file()]
        manifest["recordings"].append(dict(scenario_id=scenario_id, directory=scenario_id,
                                            execution_id=report.execution_id,
                                            sha256={str(path.relative_to(base)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}))
        write_json(base / "manifest.json", manifest)

    for scenario_id, source, key in sources:
        saved = json.loads(source.read_text(encoding="utf-8"))
        report = BIAnalysisReport.model_validate(saved[key])
        audit = source.parent / "audit" / report.execution_id
        events = [json.loads(line) for line in (audit / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        if report.contract_version.endswith("v2"):
            verification = verify_decision_report(report, bundle, events)
        else:
            responses = [event for event in events if event["event"] == "llm_response"]
            errors = verify_report(report, context, responses[-1]["raw_response"] if responses else None)
            verification = dict(passed=not errors, errors=errors)
        if not verification["passed"]:
            raise ValueError(f"Source non vérifiée {scenario_id}: {verification}")
        folder = base / scenario_id
        folder.mkdir(exist_ok=True)
        write_json(folder / "report.json", report.model_dump(mode="json"))
        shutil.copyfile(audit / "audit.jsonl", folder / "audit.jsonl")
        if (audit / "analysis.json").exists():
            shutil.copyfile(audit / "analysis.json", folder / "analysis.json")
        write_json(folder / "verification.json", dict(**verification, proofs_recomputed=True, real_input_sha256=bundle["hashes"]))
        write_json(folder / "metadata.json", dict(execution_id=report.execution_id, source_mode="live", injected=False,
                   origin="Exécution locale authentique de granite3.3:2b via Ollama, antérieurement enregistrée",
                   source_report=str(source.relative_to(ROOT)), source_report_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                   recorded_at=report.generated_at, model=report.model, original_elapsed_ms=report.timing_ms.get("total"),
                   copied_at_utc=datetime.now(timezone.utc).isoformat(), excluded_from_model_metrics=False))
        register(scenario_id, report, folder)
    injected = run_rejection_demo(ROOT)
    folder = base / "controlled_rejection"
    folder.mkdir(exist_ok=True)
    for name in ("report.json", "audit.jsonl", "metadata.json"):
        shutil.copyfile(injected.path / name, folder / name)
    errors = verify_report(injected.report, context)
    if errors:
        raise ValueError("Fallback injecté non vérifié : " + str(errors))
    write_json(folder / "verification.json", dict(passed=True, errors=[], proofs_recomputed=True,
               explicit_controlled_injection=True, actual_model_inference=False, excluded_from_model_metrics=True,
               real_input_sha256=bundle["hashes"]))
    register("controlled_rejection", injected.report, folder)
    for item in manifest["recordings"]:
        run = load_recording(item["scenario_id"], ROOT)
        print(f"{item['scenario_id']}: replay verified, original status={run.report.status}, source={run.display_metadata['source_mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
