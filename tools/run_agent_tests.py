"""Run the complete local test suite and retain a UTF-8 log and machine-readable result."""
from pathlib import Path
import argparse
import json
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "outputs/pipeline_delivery/test_suite")
args = parser.parse_args()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
started = time.perf_counter()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
with (output / "unittest.log").open("w", encoding="utf-8") as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
summary = dict(passed=result.wasSuccessful(), tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
               skipped=len(result.skipped), duration_seconds=time.perf_counter() - started,
               failed_tests=[str(test) for test, _ in result.failures], error_tests=[str(test) for test, _ in result.errors])
(output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False), flush=True)
raise SystemExit(0 if result.wasSuccessful() else 1)
