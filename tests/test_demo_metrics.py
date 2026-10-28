"""Offline metric arithmetic only; no simulated model score is published."""
import copy
from pathlib import Path
import unittest

from bi_analyst.demo_evaluation import audit_summary, summarize
from bi_analyst.demo_service import load_recording

ROOT = Path(__file__).resolve().parents[1]


class DemoMetricsTests(unittest.TestCase):
    def test_controlled_injection_is_excluded_from_model_denominators(self):
        run = load_recording("controlled_rejection", ROOT)
        audit = audit_summary(run.report, run.events)
        self.assertFalse(audit["genuine_model_run"])
        self.assertGreater(audit["blocked_unverified_assertion_flags"], 0)
        result = summarize([dict(scenario_id="injected", audit=audit, report_elapsed_ms=0)])
        self.assertEqual(result["scenario_executions"], 0)
        self.assertIsNone(result["raw_json_valid_rate"])

    def test_validated_insufficiency_is_not_a_fallback(self):
        run = load_recording("insufficient_history", ROOT)
        value = audit_summary(run.report, run.events)
        self.assertTrue(value["initial_validated"])
        self.assertTrue(value["justified_insufficient_data"])
        self.assertFalse(value["fallback"])

    def test_correction_rate_is_exclusive_of_initial_success(self):
        run = load_recording("compare_boroughs", ROOT)
        request = next(e for e in run.events if e["event"] == "llm_request")
        response = next(e for e in run.events if e["event"] == "llm_response")
        validation = next(e for e in run.events if e["event"] == "validation")
        rejected = copy.deepcopy(validation)
        rejected["result"].update(status="rejected", evidence_valid=False, errors=["unknown_evidence_fact_ids:invented", "unverified_numeric_text"])
        second = [dict(copy.deepcopy(e), attempt=2) for e in (request, response, validation)]
        events = [request, response, rejected, *second]
        audit = audit_summary(run.report, events)
        self.assertFalse(audit["initial_validated"])
        self.assertTrue(audit["validated_after_correction"])
        self.assertEqual(audit["blocked_unverified_assertion_flags"], 2)
        self.assertEqual(audit["model_response_count"], 2)
        row = dict(scenario_id="metric_fixture", audit=audit, report_elapsed_ms=2000, consumers_valid=True, proofs_verified=True)
        metrics = summarize([row])
        self.assertEqual(metrics["granite_first_attempt_validated_rate"], 0)
        self.assertEqual(metrics["granite_validated_after_correction_rate"], 1)
        self.assertEqual(metrics["raw_correct_reference_rate"], .5)
        self.assertEqual(metrics["raw_json_valid_rate"], 1)
        self.assertEqual(metrics["median_elapsed_ms"], 2000)

    def test_distinct_scenarios_and_repetition_denominators(self):
        run = load_recording("compare_boroughs", ROOT)
        audit = audit_summary(run.report, run.events)
        rows = [dict(scenario_id="same", audit=audit, report_elapsed_ms=value, consumers_valid=True, proofs_verified=True) for value in (1000, 2000, 6000)]
        metrics = summarize(rows)
        self.assertEqual(metrics["scenario_executions"], 3)
        self.assertEqual(metrics["distinct_scenarios"], 1)
        self.assertEqual(metrics["mean_elapsed_ms"], 3000)
        self.assertEqual(metrics["median_elapsed_ms"], 2000)


if __name__ == "__main__":
    unittest.main()
