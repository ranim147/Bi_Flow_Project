"""Archive reproducibility information without running model inference."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bi_analyst.analytics import load_real_bundle
from benchmark_llm.runner import OllamaClient, environment_report

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, help="New directory for this check; existing campaigns are never overwritten.")
args = parser.parse_args()
destination = args.output or ROOT / "outputs/project_cleanup" / (
    "decision_delivery_check_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
if not destination.is_absolute():
    destination = ROOT / destination
destination = destination.resolve()
destination.mkdir(parents=True, exist_ok=False)
sources = [*ROOT.joinpath("bi_analyst").glob("*.py"), *ROOT.joinpath("schemas").glob("*.json"),
           ROOT / "tests/test_decision_support.py", ROOT / "README.md", ROOT / "archive/documentation/BI_ANALYST_FINAL_REPORT.md",
           ROOT / "tools/export_bi_analyst_schemas.py", Path(__file__)]
manifest = dict(created_at_utc=datetime.now(timezone.utc).isoformat(),
                model="granite3.3:2b", contract_version="biflow-bi-analyst-v2",
                inference_during_archiving=False, human_evaluation_included=False,
                source_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
                real_input_sha256=load_real_bundle(ROOT)["hashes"],
                original_campaign="outputs/decision_support/granite_investigation_20261028",
                focused_retest="outputs/decision_support/insufficient_recommendation_retest_20261028",
                test_log="outputs/decision_support/tests_complete.log")
(destination / "delivery_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
client = OllamaClient()
environment = dict(environment=environment_report(ROOT), server=client.call("version"), resident_models=client.call("ps"))
(destination / "environment.json").write_text(json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8")
print("Source and data fingerprints archived; no inference performed. Output:", destination)
