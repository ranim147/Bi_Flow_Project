"""Business regressions: real Python inputs, explicit mocked inference, adversarial final reports."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from bi_analyst.analytics import load_real_bundle
from bi_analyst.business_evaluation import QUESTIONS, independent_checks
from bi_analyst.demo_service import run_live
from bi_analyst.models import AnalysisContext
from bi_analyst.orchestrator import analyze
from bi_analyst.routing import route_question
from bi_analyst.scoped_analytics import execute_plan
from bi_analyst.validation import validate_scoped_report
from test_benchmark_llm import workspace_trial
from test_granite_finalization import ContextualOllama

ROOT = Path(__file__).resolve().parents[1]


class IntentTests(unittest.TestCase):
    def test_three_exact_questions_have_distinct_ordered_business_plans(self):
        a, b, refusal = [route_question(q) for q in QUESTIONS.values()]
        self.assertEqual(a.metric, "mean_resolution_days")
        self.assertEqual(a.time_scope, ["2026-08", "2026-09"])
        self.assertEqual(a.operation_sequence, ["filter_scope", "compare_periods", "decompose_mean_change"])
        self.assertEqual(b.metric, "median_resolution_days")
        self.assertEqual(b.intent, "ranking")
        self.assertEqual(b.dimensions, ["borough", "complaint_type", "agency"])
        self.assertTrue(b.temporal_forbidden)
        self.assertEqual(b.operation_sequence, ["filter_scope", "rank_groups", "lock_scope", "compare_scoped_groups", "prioritize_segments"])
        self.assertEqual(refusal.intent, "justified_refusal")
        self.assertEqual(refusal.unsupported_request, ["unproven_causality", "staffing_without_capacity_data"])
        self.assertEqual(refusal.operation_sequence, ["check_capabilities"])

    def test_requested_variants(self):
        cases = [
            ("Quel arrondissement a la médiane la plus élevée ?", "median_resolution_days", "ranking", None),
            ("Dans cet arrondissement, quelles catégories ont les délais les plus longs ?", "median_resolution_days", "justified_refusal", "reference_scope_required"),
            ("Compare les médianes, pas les moyennes.", "median_resolution_days", "comparison", None),
            ("Analyse la hausse moyenne entre deux mois.", "mean_resolution_days", "comparison", None),
            ("Identifie les causes possibles, mais ne les affirme pas.", "median_resolution_days", "justified_refusal", "analysis_context_required"),
            ("Combien de personnes faut-il recruter pour réduire les délais ?", "median_resolution_days", "justified_refusal", "staffing_without_capacity_data"),
            ("Prouve que les retards viennent du manque de personnel.", "median_resolution_days", "justified_refusal", "unproven_causality"),
            ("Compare deux agences traitant des catégories différentes.", "median_resolution_days", "comparison", None),
        ]
        for question, metric, intent, unsupported in cases:
            with self.subTest(question=question):
                plan = route_question(question)
                self.assertEqual(plan.metric, metric)
                self.assertEqual(plan.intent, intent)
                if unsupported:
                    self.assertIn(unsupported, plan.unsupported_request)
                if "causes possibles" in question:
                    self.assertNotIn("unproven_causality", plan.unsupported_request)

    def test_negative_mean_does_not_override_median_and_month_years_are_preserved(self):
        self.assertEqual(route_question("Compare les médianes, pas les moyennes").metric, "median_resolution_days")
        self.assertEqual(route_question("Compare les moyennes, pas les médianes").metric, "mean_resolution_days")
        self.assertEqual(route_question("Compare août 2025 et septembre 2026").time_scope, ["2025-08", "2026-09"])
        self.assertEqual(route_question("Compare août et septembre").unsupported_request, ["year_required"])
        self.assertTrue(route_question("Compare les médianes à Brooklyn sans analyse temporelle").temporal_forbidden)
        self.assertTrue(route_question("Compare les médianes, pas d'analyse temporelle.").temporal_forbidden)
        self.assertIn("unsupported_temporal_test", route_question("Analyse la tendance de la moyenne des délais.").unsupported_request)
        self.assertNotIn("compare_periods", route_question("Investiguer les délais à Brooklyn.").operation_sequence)


class BusinessIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_real_bundle(ROOT)

    def run_question(self, question, behavior="valid"):
        with workspace_trial() as folder:
            client = ContextualOllama(behavior)
            report = analyze(question, AnalysisContext(project_root=ROOT), client=client, audit_root=folder)
            path = folder / report.execution_id
            details = json.loads((path / "analysis.json").read_text(encoding="utf-8"))
            events = [json.loads(line) for line in (path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        return report, details, events, client

    def test_exact_a_global_means_contributions_and_ranking_match_independent_python(self):
        report, details, events, client = self.run_question(QUESTIONS["A"])
        self.assertEqual(report.status, "validated", report.errors)
        self.assertTrue(independent_checks("A", report, details, self.bundle)["passed"])
        self.assertEqual([t.tool for t in report.tool_trace], route_question(QUESTIONS["A"]).operation_sequence)
        self.assertTrue(any(e["event"] == "final_semantic_validation" and e["passed"] for e in events))

    def test_exact_b_locks_true_eligible_median_winner_and_all_drilldown_evidence(self):
        report, details, _, client = self.run_question(QUESTIONS["B"])
        self.assertEqual(report.status, "validated", report.errors)
        checks = independent_checks("B", report, details, self.bundle)
        self.assertTrue(checks["passed"], checks["errors"])
        self.assertEqual(report.analytical_plan.locked_filters, {"borough": "STATEN ISLAND"})
        self.assertTrue(all(f.metric in {"median_resolution_days", "scope_valid_delay_n"} for f in report.evidence))
        self.assertTrue(all(f.reference_period is None for f in report.evidence))
        context = json.loads(client.requests[0]["messages"][-1]["content"])
        self.assertEqual(context["analytical_plan"]["locked_filters"], {"borough": "STATEN ISLAND"})
        self.assertTrue(any("agency" in f["scope"] for f in context["facts"]))
        self.assertTrue(any("complaint_type" in f["scope"] for f in context["facts"]))
        self.assertNotIn("mean_resolution_days", json.dumps(context))

    def test_exact_7_explicitly_refuses_cause_and_staffing_and_lists_six_missing_inputs(self):
        report, details, _, _ = self.run_question(QUESTIONS["7"])
        self.assertEqual(report.status, "validated", report.errors)
        self.assertEqual(report.decision, "insufficient_data")
        self.assertTrue(independent_checks("7", report, details, self.bundle)["passed"])
        self.assertEqual(len(details["capabilities"]), 6)
        self.assertTrue(all(not c["available"] for c in details["capabilities"].values()))
        self.assertNotEqual(report.insights[0].category, "data_quality")

    def test_all_request_variants_produce_metric_scoped_or_explicitly_limited_answers(self):
        for question in (
            "Quel arrondissement a la médiane la plus élevée ?",
            "Dans cet arrondissement, quelles catégories ont les délais les plus longs ?",
            "Compare les médianes, pas les moyennes.", "Analyse la hausse moyenne entre deux mois.",
            "Combien de personnes faut-il recruter pour réduire les délais ?",
            "Prouve que les retards viennent du manque de personnel.",
            "Compare deux agences traitant des catégories différentes.",
        ):
            with self.subTest(question=question):
                report, _, _, _ = self.run_question(question)
                self.assertEqual(report.status, "validated", report.errors)
                self.assertEqual(validate_scoped_report(report, self.bundle), [])

    def test_partial_unavailable_or_reversed_periods_never_substitute_another_month(self):
        for question in ("Compare les moyennes entre septembre et octobre 2026.",
                         "Compare les moyennes entre août et septembre 2025.",
                         "Compare les moyennes entre septembre et août 2026."):
            with self.subTest(question=question):
                report, details, _, _ = self.run_question(question)
                self.assertEqual(report.decision, "insufficient_data")
                self.assertNotIn("period_comparison", details)
                self.assertIn("Aucune autre période", report.insights[0].insight)

    def test_filters_survive_ranking_lock_and_drilldown(self):
        report, details, _, _ = self.run_question("À Brooklyn, compare les médianes par catégorie et agence pour HPD en septembre 2026.")
        self.assertEqual(report.status, "validated", report.errors)
        self.assertEqual(report.analytical_plan.filters, {"borough": ["BROOKLYN"], "agency": ["HPD"]})
        for fact in report.evidence:
            scope = json.loads(fact.scope)
            self.assertEqual(scope.get("borough"), "BROOKLYN")
            self.assertEqual(scope.get("agency"), "HPD")
            if fact.metric == "median_resolution_days":
                self.assertEqual(fact.reference_period, "2026-09")

    def test_repair_timeout_wrong_model_and_python_fallback_all_pass_final_semantic_gate(self):
        for behavior, expected in (("repair", "validated"), ("invalid", "fallback"), ("timeout", "fallback"),
                                   ("wrong_model", "fallback"), ("truncated", "fallback"), ("repair_timeout", "fallback")):
            with self.subTest(behavior=behavior):
                report, _, events, _ = self.run_question(QUESTIONS["B"], behavior)
                self.assertEqual(report.status, expected, report.errors)
                self.assertEqual(validate_scoped_report(report, self.bundle), [])
                self.assertTrue([e for e in events if e["event"] == "final_semantic_validation"][-1]["passed"])
                if expected == "fallback":
                    self.assertNotEqual(report.validation.status, "validated")

    def test_final_gate_rejects_true_but_off_topic_reports_mutated_after_generation(self):
        report, _, _, _ = self.run_question(QUESTIONS["B"])
        a, _, _, _ = self.run_question(QUESTIONS["A"])
        damaged = a.model_copy(deep=True)
        damaged.question = QUESTIONS["B"]
        self.assertTrue(validate_scoped_report(damaged, self.bundle))
        for mutation in ("metric", "dimension", "scope", "period", "sequence", "missing_result", "value", "source", "n", "prose", "recommendation", "evidence_binding"):
            with self.subTest(mutation=mutation):
                changed = report.model_copy(deep=True)
                if mutation == "metric": changed.analytical_plan.metric = "mean_resolution_days"
                elif mutation == "dimension": changed.analytical_plan.dimensions.remove("agency")
                elif mutation == "scope": changed.analytical_plan.locked_filters = {"borough": "BROOKLYN"}
                elif mutation == "period": changed.analytical_plan.time_scope = ["2026-08", "2026-09"]
                elif mutation == "sequence": changed.tool_trace.reverse()
                elif mutation == "missing_result": changed.insights.pop()
                elif mutation == "value": changed.evidence[1].value = 1000.0
                elif mutation == "source": changed.evidence[1].source_sha256 = "a" * 64
                elif mutation == "n": changed.evidence[1].n = 1
                elif mutation == "prose": changed.insights[0].insight = "Une hausse moyenne est observée."
                elif mutation == "recommendation": changed.insights[-1].recommendation = "Recruter trois agents."
                elif mutation == "evidence_binding": changed.insights[-1].evidence_fact_ids = changed.insights[0].evidence_fact_ids
                self.assertTrue(validate_scoped_report(changed, self.bundle))

    def test_live_service_checks_exact_question_identity_and_sequential_reports_are_isolated(self):
        with workspace_trial() as folder:
            runs = [run_live(question, ROOT, audit_root=folder, client=ContextualOllama(), dataset_version="legacy") for question in QUESTIONS.values()]
            self.assertEqual(len({r.report.execution_id for r in runs}), 3)
            self.assertEqual([r.report.question for r in runs], list(QUESTIONS.values()))
            self.assertTrue(all(r.report.status == "validated" for r in runs))
            self.assertEqual(runs[1].report.analytical_plan.time_scope, [])
            self.assertEqual(runs[2].report.decision, "insufficient_data")


class SyntheticPlanTests(unittest.TestCase):
    def bundle(self):
        rows = []
        for borough, n, days in (("BROOKLYN", 31, 3), ("BRONX", 30, 3), ("QUEENS", 29, 99)):
            for index in range(n):
                rows.append(dict(unique_key=len(rows), borough=borough, complaint_type="TYPE", agency="AGENCY",
                                 created_date="2026-08-01", closed_date=f"2026-08-{days+1:02}" if days < 30 else "2026-11-08", resolution_days=float(days)))
        frame = pd.DataFrame(rows)
        source = "synthetic://business-regression"
        return dict(clean=frame, kind="synthetic", source_paths={"clean": source}, hashes={source: hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()},
                    metadata=dict(extracted_at_utc="2026-12-01", period_start_inclusive="2026-08-01", period_end_exclusive="2026-12-01", sample_monthly_counts={"2026-08": 90}, partial_months=[]))

    def test_n_29_excluded_n_30_included_and_ties_are_explicit(self):
        question = QUESTIONS["B"]
        results, details = execute_plan(question, route_question(question), self.bundle())
        self.assertEqual(details["winner"]["filters"], {"borough": "BRONX"})
        self.assertIn("Ex æquo", details["sections"][0]["text"])
        ranking = details["tables"]["ranking"]
        self.assertFalse(next(r for r in ranking if r["filters"]["borough"] == "QUEENS")["eligible"])
        self.assertTrue(all(f.n >= 30 for r in results for f in r.evidence if f.metric == "median_resolution_days"))


if __name__ == "__main__":
    unittest.main()
