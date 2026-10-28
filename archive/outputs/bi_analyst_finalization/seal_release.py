"""Archive source digests and check that historical references stayed unchanged."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parents[2]
release = root / "outputs/bi_analyst_finalization/granite_v2_release_20261028"
first = root / "outputs/bi_analyst_finalization/granite_v2_final_20261028/historical_reference_hashes.json"
current = json.loads((release / "historical_reference_hashes.json").read_text(encoding="utf-8"))
assert current == json.loads(first.read_text(encoding="utf-8")), "Historical references changed"
sources = list((root / "bi_analyst").glob("*.py")) + [
    root / name for name in ("tests/test_bi_analyst.py", "tests/test_granite_finalization.py",
                            "README.md", "BI_ANALYST_FINAL_REPORT.md", "requirements-agent.txt")
]
manifest = dict(
    model="granite3.3:2b", internal_contract="granite-draft-v2",
    external_contract="biflow-bi-analyst-v1", human_evaluation_included=False,
    historical_references_unchanged_since_first_verification=True,
    source_sha256={str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sources},
)
(release / "reproducibility_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print("Historical references unchanged; manifest saved.")
