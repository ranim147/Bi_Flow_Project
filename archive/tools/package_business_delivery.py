"""Package the business correction and its recorded evidence, without datasets or secrets."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
base = ROOT / "outputs/business_intent"
campaign = base / "granite_20261028"
tests = json.loads((base / "test_suite/summary.json").read_text(encoding="utf-8"))
replay = json.loads((campaign / "replay_verification.json").read_text(encoding="utf-8"))
assert tests["passed"] and replay["all_passed"]
historical = json.loads((campaign / "historical_hashes.json").read_text(encoding="utf-8"))
assert all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest for path, digest in historical.items())
names = [
    "README.md", "BI_ANALYST_BUSINESS_FIXES.md", "bi_analyst/models.py", "bi_analyst/routing.py",
    "bi_analyst/scoped_analytics.py", "bi_analyst/scoped.py", "bi_analyst/business_evaluation.py",
    "bi_analyst/tools.py", "bi_analyst/orchestrator.py", "bi_analyst/decision.py",
    "bi_analyst/validation.py", "bi_analyst/verification.py", "bi_analyst/consumers.py",
    "bi_analyst/demo_service.py", "demo/streamlit_app.py", "tests/test_business_intent.py",
    "tests/test_demo_service.py", "tests/test_demo_ui.py", "tools/export_bi_analyst_schemas.py",
    "tools/run_agent_tests.py", "tools/package_business_delivery.py",
    "schemas/BIAnalysisReport.v1.schema.json", "schemas/BIAnalysisReport.v2.schema.json",
]
files = [ROOT / name for name in names] + [
    path for folder in (campaign, base / "test_suite") for path in folder.rglob("*") if path.is_file()]
assert all(path.resolve().is_relative_to(ROOT) for path in files)
manifest = dict(unit_tests=tests, live_campaign="outputs/business_intent/granite_20261028/summary.json",
                offline_verification=replay, historical_files_unchanged=True,
                contents={path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
manifest_path = base / "delivery_manifest.json"
with manifest_path.open("x", encoding="utf-8") as stream:
    json.dump(manifest, stream, ensure_ascii=False, indent=2)
archive = base / "delivery_20261028.zip"
with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as stream:
    for path in [*files, manifest_path]:
        stream.write(path, path.relative_to(ROOT).as_posix())
with zipfile.ZipFile(archive) as stream:
    assert stream.testzip() is None
print(json.dumps(dict(archive=str(archive), files=len(files) + 1, bytes=archive.stat().st_size,
                      tests_passed=tests["tests"], historical_files_unchanged=True)), flush=True)
