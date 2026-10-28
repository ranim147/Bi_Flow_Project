"""Controlled live extraction; the public CLI can also reuse these immutable raw parts offline."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bi_pipeline.acquire import AcquisitionConfig, Socrata, acquire
import requests

session = requests.Session()
session.trust_env = False  # this script is run with authorized direct network access
destination = ROOT / "data/versions/nyc311_202301_202609_v2"
result = acquire(destination, AcquisitionConfig(), client=Socrata(destination / "acquisition_audit.jsonl", session=session))
print(json.dumps({key: result[key] for key in ("data_version", "sample_rows", "sample_monthly_counts", "source_changed_during_extraction")}), flush=True)
