from __future__ import annotations
import copy
import json
from pathlib import Path
import unittest

import pandas as pd
from jsonschema import Draft202012Validator
from benchmark_llm.runner import OllamaFailure

from bi_analyst.analytics import (prepare_investigation_frame, select_comparison_periods,
                                decompose_delay_change, backlog_snapshots, agency_comparability)
from bi_analyst.decision import analyze_synthetic_decision
from bi_analyst.decision_fixtures import SCENARIOS, synthetic_case
from bi_analyst.investigation import policy_for, validate_plan
from bi_analyst.models import AnalysisContext, BIAnalysisReport
from bi_analyst.orchestrator import analyze
from bi_analyst.tools import select_tools
from test_benchmark_llm import workspace_trial

ROOT = Path(__file__).resolve().parents[1]


class DecisionClient:
    def __init__(self, behavior="valid"):
        self.behavior, self.calls = behavior, []

    def installed(self):
        return {"granite3.3:2b": {"digest": "TEST ONLY"}}

    def call(self, endpoint, payload=None, timeout=None):
        if endpoint == "version":
            return {"version": "test"}
        self.calls.append(payload)
        if self.behavior == "timeout":
            raise OllamaFailure("timeout", "TEST ONLY")
        value = json.loads(payload["messages"][-1]["content"])["example"]
        if self.behavior == "invalid" or (self.behavior == "repair" and len(self.calls) == 1):
            value["evidence_ids"] = ["invented.*"]
        return dict(model="unexpected:1b" if self.behavior == "wrong_model" else "granite3.3:2b", done=True,
                    done_reason="length" if self.behavior == "truncated" else "stop", message={"content": json.dumps(value)},
                    load_duration=1000000, eval_duration=2000000, prompt_eval_duration=3000000)


class BusinessAnalyticsTests(unittest.TestCase):
    def prepared(self, name):
        frame, metadata, question = synthetic_case(name)
        return prepare_investigation_frame(frame, metadata["extracted_at_utc"]), metadata, question

    def test_increase_concentrated_in_category_reconciles_exactly(self):
        data, _, _ = self.prepared("category_increase")
        result = decompose_delay_change(data, ("2026-08", "2026-09"), ("complaint_type", "agency"))
        self.assertAlmostEqual(result["delta_mean_days"], 3.0)
        self.assertAlmostEqual(result["within_days"], 3.0)
        self.assertAlmostEqual(result["composition_days"], 0.0)
        self.assertEqual(result["groups"][0]["complaint_type"], "TARGET")
        self.assertAlmostEqual(result["residual_days"], 0)

    def test_apparent_increase_is_composition_not_slower_segments(self):
        data, _, _ = self.prepared("composition_shift")
        result = decompose_delay_change(data, ("2026-08", "2026-09"), ("complaint_type",))
        self.assertAlmostEqual(result["delta_mean_days"], 5.4)
        self.assertAlmostEqual(result["within_days"], 0.0)
        self.assertAlmostEqual(result["composition_days"], 5.4)

    def test_backlog_snapshot_excludes_future_arrivals_and_tracks_ages(self):
        data, _, _ = self.prepared("backlog_increase")
        result = backlog_snapshots(data, ("2026-08", "2026-09"))
        self.assertEqual(result[0]["backlog_n"], 0)
        self.assertEqual(result[1]["backlog_n"], 40)
        self.assertEqual(result[1]["age_median_days"], 26.0)

    def test_agencies_without_common_categories_are_not_comparable(self):
        data, _, _ = self.prepared("agency_case_mix")
        result = agency_comparability(data, ("2026-08", "2026-09"))
        self.assertEqual(result[0]["category_overlap"], 0)
        self.assertEqual(result[0]["eligible_common_categories"], [])
        self.assertFalse(result[0]["directly_comparable"])

    def test_new_and_disappearing_groups_have_no_invented_within_mean(self):
        data, _, _ = self.prepared("category_increase")
        data.loc[data.closure_month.eq("2026-09") & data.complaint_type.eq("TARGET"), "complaint_type"] = "NEW"
        result = decompose_delay_change(data, ("2026-08", "2026-09"), ("complaint_type",))
        self.assertAlmostEqual(result["residual_days"], 0)
        for row in result["groups"]:
            if row["complaint_type"] in {"NEW", "TARGET"}:
                self.assertEqual(row["within_days"], 0)
                self.assertFalse(row["rankable"])
        json.dumps(result, allow_nan=False)

    def test_small_groups_are_not_ranked_but_not_dropped_from_identity(self):
        data, _, _ = self.prepared("small_group")
        result = decompose_delay_change(data, ("2026-08", "2026-09"), ("complaint_type",))
        self.assertEqual(result["groups"][0]["priority_score"], 0)
        self.assertFalse(result["groups"][0]["rankable"])
        self.assertAlmostEqual(result["delta_mean_days"], result["within_days"])

    def test_partial_and_gapped_periods_cannot_be_substituted(self):
        _, metadata, _ = synthetic_case("category_increase")
        self.assertEqual(select_comparison_periods(metadata), ("2026-08", "2026-09"))
        metadata["sample_monthly_counts"] = {"2026-07": 100, "2026-09": 100, "2026-10": 100}
        metadata["period_start_inclusive"] = "2026-07-01"
        self.assertIsNone(select_comparison_periods(metadata))

    def test_future_closures_missing_dates_and_bad_duration_remain_exclusions(self):
        frame, metadata, _ = synthetic_case("category_increase")
        frame.loc[0, "resolution_days"] = 999.0
        frame.loc[1, "created_date"] = pd.NaT
        frame.loc[2, "closed_date"] = pd.Timestamp("2027-01-01T00:00:00Z")
        result = prepare_investigation_frame(frame, metadata["extracted_at_utc"])
        self.assertFalse(result.loc[:2, "valid_delay"].any())
        self.assertTrue(pd.isna(result.loc[2, "closed_date"]))


class DecisionIntegrationTests(unittest.TestCase):
    def run_case(self, name, client=None):
        frame, metadata, question = synthetic_case(name)
        with workspace_trial() as folder:
            report = analyze_synthetic_decision(question, frame, metadata, AnalysisContext(project_root=ROOT),
                                                client=client or DecisionClient(), audit_root=folder)
        return report

    def test_eight_business_cases_have_precise_falsifiable_plans_and_separate_data_kind(self):
        for name in SCENARIOS:
            with self.subTest(name=name):
                report = self.run_case(name)
                self.assertEqual(report.status, "validated", report.errors)
                self.assertEqual(report.data_kind, "synthetic")
                self.assertTrue(all(fact.data_kind == "synthetic" for fact in report.evidence))
                self.assertEqual(report, BIAnalysisReport.model_validate_json(report.model_dump_json()))
                plan = report.decision_support[0]
                self.assertEqual(len(plan.investigation_hypotheses), len(plan.expected_verification))
                self.assertTrue(all(h.status == "unverified" and h.additional_data for h in plan.investigation_hypotheses))
                self.assertTrue(all(step.fields and step.comparison for step in plan.investigation_steps))
                self.assertTrue(all(v.supports and v.refutes for v in plan.expected_verification))
                self.assertTrue(all(action.priority_reason and action.decision_if_supported and action.decision_if_refuted for action in plan.recommended_actions))
                if name in {"small_group", "insufficient_history", "insufficient_recommendation"}:
                    self.assertEqual(report.decision, "insufficient_data")
                elif name == "composition_shift":
                    self.assertEqual(report.finding, "composition_shift")
                elif name == "backlog_increase":
                    self.assertIn("backlog", [h.hypothesis_id for h in plan.investigation_hypotheses])
                    backlog_step = next(step for step in plan.investigation_steps if step.hypothesis_id == "backlog")
                    self.assertIn("current_snapshot_end_exclusive", backlog_step.filters)
                    self.assertNotIn("current_closure_month", backlog_step.filters)
                elif name == "agency_case_mix":
                    self.assertEqual(report.finding, "agency_noncomparable")
                elif name == "category_increase":
                    self.assertEqual(plan.priority_scope.filters["complaint_type"], "TARGET")
                with self.assertRaises(ValueError):
                    report.to_legacy_dict()

    def test_invented_ids_are_repaired_once_or_fall_back_explicitly(self):
        for behavior, expected in (("repair", "validated"), ("invalid", "fallback"), ("wrong_model", "fallback"),
                                   ("truncated", "fallback"), ("timeout", "fallback")):
            client = DecisionClient(behavior)
            report = self.run_case("category_increase", client)
            self.assertEqual(report.status, expected)
            self.assertEqual(len(client.calls), 1 if behavior == "timeout" else 2)
            self.assertEqual(report.decision_support[0].origin, "granite_validated" if expected == "validated" else "python_fallback")

    def test_real_end_to_end_dispatch_and_v1_projection(self):
        with workspace_trial() as folder:
            report = analyze("Investiguer la hausse des délais à Brooklyn.", AnalysisContext(project_root=ROOT),
                             client=DecisionClient(), audit_root=folder)
        self.assertEqual(report.status, "validated", report.errors)
        self.assertEqual(report.data_kind, "real")
        self.assertEqual(report.contract_version, "biflow-bi-analyst-v2")
        self.assertEqual(report.decision_support[0].priority_scope.filters["borough"], "BROOKLYN")
        legacy = report.to_legacy_dict()
        self.assertNotIn("decision_support", legacy)
        self.assertEqual(legacy["contract_version"], "biflow-bi-analyst-v1")
        for version, payload in (("v1", legacy), ("v2", report.model_dump(mode="json"))):
            schema = json.loads((ROOT / "schemas" / f"BIAnalysisReport.{version}.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(payload)
        self.assertEqual(select_tools(report.question), ["investigate_degradation"])

    def test_unsupported_operations_scopes_numbers_and_causal_prose_are_rejected(self):
        from bi_analyst.decision_tools import build_decision_analysis
        from bi_analyst.analytics import load_real_bundle
        _, details, _ = build_decision_analysis("Investiguer la hausse des délais à Brooklyn", load_real_bundle(ROOT))
        policy = policy_for(details["problems"][0], details["periods"])
        for mutation in (dict(priority_scope="Queens"), dict(evidence_ids=["ds.*"]),
                         dict(investigation_hypotheses=["increase_staffing"]),
                         dict(problem="Les effectifs causent une hausse de 20 jours"),
                         dict(recommended_actions=["improve_performance"]), dict(limitations=[]),
                         dict(expected_verification=["verify_backlog"])):
            value = copy.deepcopy(policy.example())
            value.update(mutation)
            self.assertEqual(validate_plan(json.dumps(value), policy)[1].status, "rejected")

    def test_automatic_evaluation_recomputes_scopes_provenance_and_plan(self):
        from bi_analyst.analytics import load_real_bundle
        from bi_analyst.decision_evaluation import verify_decision_report
        context = AnalysisContext(project_root=ROOT)
        with workspace_trial() as folder:
            report = analyze("Investiguer la hausse des délais à Brooklyn.", context, client=DecisionClient(), audit_root=folder)
            events = [json.loads(line) for line in (folder / report.execution_id / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        bundle = load_real_bundle(ROOT)
        self.assertTrue(verify_decision_report(report, bundle, events)["passed"])
        changed = report.model_copy(deep=True)
        changed.evidence[0].value = 123456.0
        self.assertFalse(verify_decision_report(changed, bundle, events)["passed"])
        changed = report.model_copy(deep=True)
        changed.decision_support[0].priority_scope.filters["borough"] = "QUEENS"
        self.assertFalse(verify_decision_report(changed, bundle, events)["passed"])
        changed = report.model_copy(deep=True)
        changed.decision_support[0].investigation_hypotheses[0].statement = "Le manque de personnel cause cette dégradation."
        self.assertFalse(verify_decision_report(changed, bundle, events)["passed"])
        changed = report.model_copy(deep=True)
        changed.insights[0].insight = "La cause est prouvée."
        self.assertFalse(verify_decision_report(changed, bundle, events)["passed"])
        changed = report.model_copy(deep=True)
        changed.source_refs = ["invented://source"]
        self.assertFalse(verify_decision_report(changed, bundle, events)["passed"])

    def test_real_inputs_missing_never_activate_synthetic_analysis(self):
        with workspace_trial() as folder:
            report = analyze("Investiguer une dégradation des délais.", AnalysisContext(project_root=folder),
                             client=DecisionClient(), audit_root=folder / "audit")
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.data_kind, "real")
        self.assertEqual(report.decision_support, [])

    def test_pydantic_contract_refuses_mixed_real_and_synthetic_evidence(self):
        report = self.run_case("category_increase")
        payload = report.model_dump(mode="json")
        payload["data_kind"] = "real"
        with self.assertRaises(ValueError):
            BIAnalysisReport.model_validate(payload)
        schema = json.loads((ROOT / "schemas/BIAnalysisReport.v2.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(Draft202012Validator(schema).is_valid(payload))

    def test_single_hypothesis_schema_prevents_duplicate_operation_generation(self):
        from bi_analyst.decision_evaluation import run_decision_evaluation
        client = DecisionClient()
        with workspace_trial() as folder:
            metrics = run_decision_evaluation(AnalysisContext(project_root=ROOT), folder / "focused", client=client,
                                              include_real=False, scenario_ids={"insufficient_recommendation"})
        self.assertEqual(metrics["synthetic"]["scenario_count"], 1)
        for field in ("investigation_hypotheses", "investigation_steps", "recommended_actions", "expected_verification"):
            self.assertEqual(client.calls[0]["format"]["properties"][field]["maxItems"], 1)

    def test_campaign_records_separate_synthetic_results_and_can_replay_offline(self):
        from bi_analyst.decision_evaluation import run_decision_evaluation, replay_decision_evaluation
        with workspace_trial() as folder:
            destination = folder / "campaign"
            context = AnalysisContext(project_root=ROOT)
            metrics = run_decision_evaluation(context, destination, client=DecisionClient(), include_real=False)
            self.assertEqual(metrics["real"]["scenario_count"], 0)
            self.assertEqual(metrics["synthetic"]["scenario_count"], 8)
            self.assertEqual(metrics["synthetic"]["granite_validated_without_fallback_rate"], 1)
            self.assertTrue(replay_decision_evaluation(context, destination)["all_passed"])
            self.assertFalse((destination / "human_review.csv").exists())
            with self.assertRaises(FileExistsError):
                run_decision_evaluation(context, destination, client=DecisionClient(), include_real=False)


if __name__ == "__main__":
    unittest.main()
