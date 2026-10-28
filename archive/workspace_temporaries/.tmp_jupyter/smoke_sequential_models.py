"""Essai technique réel : un cas par modèle, pas une campagne de sélection."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark_llm.data import build_cases, load_bundle
from benchmark_llm.runner import OllamaClient, new_run_directory, run_benchmark

bundle = load_bundle(ROOT)
cases = build_cases(bundle)[:1]
destination = new_run_directory(ROOT)
print("SMOKE_TEST_DIRECTORY", destination, flush=True)
runs, manifest = run_benchmark(ROOT, bundle, cases, OllamaClient(timeout=300), destination,
                              memory_policy="attempt")
summary = {"purpose": "technical_smoke_test_one_case_per_model_not_selection", "output": str(destination),
           "results": [{k: r[k] for k in ["model", "case_id", "status", "latency_seconds", "error"]} for r in runs]}
(destination / "smoke_validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
