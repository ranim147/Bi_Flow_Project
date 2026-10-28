from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from benchmark_llm.runner import OllamaFailure
from bi_analyst.draft import build_policy, draft_payload, validate_draft
from bi_analyst.evaluation import REAL_SCENARIOS, run_evaluation
from bi_analyst.models import AnalysisContext, BIAnalysisReport, EvidenceFact
from bi_analyst.orchestrator import AGENT_OPTIONS, MODEL_NAME, _RECOMMENDATIONS_FR, _insufficiency_finding, analyze
from bi_analyst.tools import select_tools
from bi_analyst.verification import verify_campaign, verify_report

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.fact = EvidenceFact(fact_id="delay.borough.A", metric="median_resolution_days",
                                 scope="borough=A", value=0.3, unit="days", n=40,
                                 source="data/processed/nyc311_clean.csv", source_sha256="a" * 64)
        self.policy = build_policy("descriptive", [self.fact],
                                   ["sample_only", "nonrandom_sample", "no_cause", "no_sla"],
                                   _RECOMMENDATIONS_FR["review_comparison"], "review_comparison")

    def check_rejected(self, changes, expected):
        value = self.policy.example()
        value.update(changes)
        _, result = validate_draft(json.dumps(value), self.policy)
        self.assertEqual(result.status, "rejected")
        self.assertTrue(any(error.startswith(expected) for error in result.errors), result.errors)
        self.assertFalse(result.schema_valid)

    def test_two_contextual_examples_pass_independent_validation(self):
        for index in range(2):
            _, result = validate_draft(json.dumps(self.policy.example(index)), self.policy)
            self.assertEqual(result.status, "validated", result.errors)

    def test_schema_and_strict_json(self):
        raw = json.dumps(self.policy.example())
        for invalid in ("```json\n" + raw + "\n```", raw[:-1],
                        '{"finding":"a","finding":"b"}', '{"value":NaN}'):
            with self.subTest(raw=invalid):
                self.assertEqual(validate_draft(invalid, self.policy)[1].status, "rejected")
        self.check_rejected({"value": 3}, "schema:")
        self.check_rejected({"evidence_ids": "delay.borough.A"}, "schema:")

    def test_evidence_exactness_completeness_and_uniqueness(self):
        self.check_rejected({"evidence_ids": ["delay.borough.*"]}, "unknown_evidence_fact_ids:")
        self.check_rejected({"evidence_ids": ["invented"]}, "unknown_evidence_fact_ids:")
        self.check_rejected({"evidence_ids": [self.fact.fact_id] * 2}, "duplicate_evidence_fact_id")
        self.check_rejected({"evidence_ids": []}, "schema:")

    def test_python_finding_and_limits_cannot_be_overruled(self):
        self.check_rejected({"finding": "statistical_anomaly"}, "finding_inconsistent_with_python")
        self.check_rejected({"limitation_codes": ["no_cause"]}, "context_limitations_mismatch")
        self.check_rejected({"limitation_codes": list(self.policy.limitation_codes) * 2}, "duplicate_limitation_code")

    def test_numeric_causal_and_operational_assertions_are_blocked(self):
        for text in ("Les délais sont de 3 jours.", "Le délai est de trois jours.", "The median is twenty days."):
            self.check_rejected({"insight": text}, "unverified_numeric_text")
        for text in ("Cela s'explique par les effectifs.", "Les processus influencent les délais.",
                     "Les conditions locales pouvant affecter les délais."):
            self.check_rejected({"insight": text}, "possible_unsupported_causal_claim")
        for text in ("Augmenter les effectifs.", "Imposer un SLA.", "Fermer les services."):
            self.check_rejected({"recommendation": text}, "unsupported_recommendation")

    def test_wrong_ordering_and_invented_source_are_rejected_without_numbers(self):
        self.check_rejected({"insight": "BROOKLYN est le groupe le plus rapide."}, "insight_outside_verified_language")
        self.check_rejected({"insight": "Selon la mairie, les groupes sont comparables."}, "insight_outside_verified_language")

    def test_inference_contract_is_small_fixed_and_granite_only(self):
        payload = draft_payload("Compare les délais.", self.policy, AGENT_OPTIONS)
        self.assertEqual(payload["model"], "granite3.3:2b")
        self.assertEqual(payload["options"]["seed"], 42)
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertLessEqual(payload["options"]["num_predict"], 384)
        self.assertEqual(payload["keep_alive"], "10m")
        self.assertNotIn("source_sha256", str(payload["messages"]))
        self.assertNotIn("0.3", str(payload["messages"]))
        corrected = draft_payload("Compare.", self.policy, AGENT_OPTIONS, ["unverified_numeric_text"])
        self.assertIn("correction_errors", corrected["messages"][-1]["content"])


class ContextualOllama:
    def __init__(self, behavior="valid"):
        self.behavior = behavior
        self.requests = []

    def installed(self):
        return {MODEL_NAME: {"digest": "test"}}

    def call(self, endpoint, payload=None, timeout=None):
        if endpoint == "version":
            return {"version": "test"}
        self.requests.append(copy.deepcopy(payload))
        count = len(self.requests)
        if self.behavior == "timeout" or (self.behavior == "repair_timeout" and count == 2):
            raise OllamaFailure("timeout", "test timeout")
        example = json.loads(payload["messages"][-1]["content"])["examples"][0]
        if self.behavior in {"invalid", "repair", "repair_timeout"} and (count == 1 or self.behavior == "invalid"):
            example["evidence_ids"] = ["delay.borough.*"]
            example["insight"] = "BROOKLYN est le plus rapide avec 99 jours."
        return {"message": {"content": json.dumps(example, ensure_ascii=False)},
                "model": "unexpected:1b" if self.behavior == "wrong_model" else MODEL_NAME,
                "done": True, "done_reason": "length" if self.behavior == "truncated" else "stop",
                "load_duration": 1000000, "prompt_eval_duration": 2000000, "eval_duration": 3000000}


class IntegrationTests(unittest.TestCase):
    def test_all_six_scenarios_round_trip_final_contract_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            for scenario in REAL_SCENARIOS:
                with self.subTest(scenario=scenario["id"]):
                    report = analyze(scenario["question"], AnalysisContext(project_root=ROOT),
                                     client=ContextualOllama(), audit_root=Path(directory))
                    self.assertEqual(report.status, "validated", report.errors)
                    self.assertEqual(report, BIAnalysisReport.model_validate_json(report.model_dump_json()))
                    facts = {fact.fact_id: fact for fact in report.evidence}
                    self.assertTrue(all(fact.source_sha256 for fact in facts.values()))
                    self.assertTrue(set(scenario["expected_metrics"]) <= {fact.metric for fact in facts.values()})
                    if scenario["id"] == "complaint_type_comparison":
                        self.assertNotIn("sample_request_count", {fact.metric for fact in facts.values()})
                    self.assertTrue(set(report.insights[0].evidence_fact_ids) <= facts.keys())
                    self.assertEqual(set(report.source_refs), {fact.source for fact in facts.values()})
                    if scenario["id"] in {"monthly_anomaly", "insufficient_history"}:
                        self.assertEqual(report.decision, "insufficient_data")
                        self.assertEqual(report.finding, "insufficient_history")
                        self.assertTrue(any(fact.metric == "available_complete_periods" for fact in facts.values()))
                    artifact = Path(directory) / report.execution_id
                    self.assertTrue((artifact / "response_attempt_1.txt").is_file())
                    self.assertTrue((artifact / "validation_attempt_1.json").is_file())

    def test_one_correction_then_explicit_fallback_or_acceptance(self):
        for behavior, status, calls in (("repair", "validated", 2), ("invalid", "fallback", 2),
                                         ("timeout", "fallback", 1), ("truncated", "fallback", 2),
                                         ("wrong_model", "fallback", 2),
                                         ("repair_timeout", "fallback", 2)):
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory() as directory:
                client = ContextualOllama(behavior)
                report = analyze(REAL_SCENARIOS[0]["question"], AnalysisContext(project_root=ROOT),
                                 client=client, audit_root=Path(directory))
                self.assertEqual(report.status, status, report.errors)
                self.assertEqual(len(client.requests), calls)
                self.assertTrue(report.evidence)
                self.assertIn("borough=STATEN ISLAND", report.insights[0].insight)
                self.assertIn("borough=BRONX", report.insights[0].insight)
                if calls == 2:
                    self.assertIn("correction_errors", client.requests[1]["messages"][-1]["content"])
                if behavior == "repair_timeout":
                    self.assertTrue(any(e.startswith("unknown_evidence_fact_ids") for e in report.errors))

    def test_scenario_tools_do_not_execute_unrequested_scans(self):
        self.assertEqual(select_tools(REAL_SCENARIOS[3]["question"]), ["data_quality", "summarize_kpis"])
        self.assertEqual(select_tools(REAL_SCENARIOS[4]["question"]), ["data_quality"])
        self.assertIn("analyze_trend", select_tools(REAL_SCENARIOS[5]["question"]))

    def test_executed_anomaly_scan_does_not_require_trend_history(self):
        results = [SimpleNamespace(trace=SimpleNamespace(tool="detect_anomalies", status="success")),
                   SimpleNamespace(trace=SimpleNamespace(tool="analyze_trend", status="skipped"))]
        self.assertIsNone(_insufficiency_finding("Y a-t-il une anomalie mensuelle ?", results))

    def test_offline_replay_detects_corrupted_value_provenance_and_final_text(self):
        with tempfile.TemporaryDirectory() as directory:
            context = AnalysisContext(project_root=ROOT)
            report = analyze(REAL_SCENARIOS[0]["question"], context,
                             client=ContextualOllama(), audit_root=Path(directory))
            raw = (Path(directory) / report.execution_id / "response_attempt_1.txt").read_text(encoding="utf-8")
            self.assertEqual(verify_report(report, context, raw), [])
            for field, replacement in (("value", 999.0), ("source_sha256", "b" * 64), ("n", 999)):
                damaged = report.model_copy(deep=True)
                setattr(damaged.evidence[0], field, replacement)
                self.assertTrue(any(e.startswith("recomputed_fact_mismatch:") for e in verify_report(damaged, context, raw)))
            report.insights[0].insight = "BROOKLYN est le plus rapide."
            self.assertIn("final_text_materialization_mismatch", verify_report(report, context, raw))

    def test_evaluation_creates_no_human_review_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "campaign"
            with patch("bi_analyst.evaluation.analyze", side_effect=lambda q, c, **kw: analyze(q, c, client=ContextualOllama(), **kw)):
                result = run_evaluation(AnalysisContext(project_root=ROOT), use_llm=True,
                                        limit=1, output_dir=destination)
            self.assertFalse((destination / "human_review.csv").exists())
            self.assertFalse(result["metrics"]["human_evaluation_included"])
            self.assertEqual(result["metrics"]["initial_validated_count"], 1)
            with self.assertRaises(FileExistsError):
                run_evaluation(AnalysisContext(project_root=ROOT), use_llm=True,
                               limit=1, output_dir=destination)

    def test_campaign_replay_accepts_project_relative_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as directory:
            context = AnalysisContext(project_root=ROOT)
            destination = Path(directory) / "new"
            previous = Path(directory) / "previous"
            with patch("bi_analyst.evaluation.analyze", side_effect=lambda q, c, **kw: analyze(q, c, client=ContextualOllama(), **kw)):
                run_evaluation(context, use_llm=True, output_dir=destination)
            # Read-only reference fixture, separate from verification destination.
            import shutil
            shutil.copytree(destination, previous)
            first = json.loads((destination / f"{REAL_SCENARIOS[0]['id']}.json").read_text(encoding="utf-8"))
            failed_fixture = destination / "audit" / first["baseline"]["execution_id"]
            result = verify_campaign(destination.relative_to(ROOT), context,
                                     previous.relative_to(ROOT), failed_fixture.relative_to(ROOT))
            self.assertTrue(result["all_scenarios_verified"], result["scenarios"])
            self.assertTrue((destination / "historical_reference_hashes.json").is_file())


if __name__ == "__main__":
    unittest.main()
