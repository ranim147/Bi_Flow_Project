from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import pandas as pd

from bi_analyst.analytics import (
    MIN_GROUP_N,
    compare_group_delays,
    detect_mad,
    detect_zscore,
    load_real_bundle,
    mann_kendall_trend,
    scan_monthly_anomalies,
)
from bi_analyst.evaluation import _summary_metrics
from bi_analyst.models import AnalysisContext, EvidenceFact
from bi_analyst.orchestrator import (
    MODEL_NAME,
    _include_required_trend_evidence,
    analyze,
)
from bi_analyst.tools import execute_tool, select_tools
from bi_analyst.validation import validate_interpretation, validate_selection

ROOT = Path(__file__).resolve().parents[1]


class FakeOllama:
    def __init__(self, responses: list[str] | None = None, *, installed=None, error=None):
        self.responses = list(responses or [])
        self.installed_models = installed or {MODEL_NAME: {"name": MODEL_NAME}}
        self.error = error
        self.calls = []

    def installed(self):
        if self.error is not None:
            raise self.error
        return self.installed_models

    def call(self, endpoint, payload=None, timeout=None):
        self.calls.append((endpoint, payload, timeout))
        if endpoint == "version":
            return {"version": "test"}
        if not self.responses:
            raise AssertionError("Unexpected Ollama call.")
        return {"message": {"content": self.responses.pop(0)}}


class AnalyticsTests(unittest.TestCase):
    def test_group_delays_preserve_small_sample_status(self):
        frame = pd.DataFrame(
            {
                "borough": ["A"] * MIN_GROUP_N + ["B"] * 5,
                "closed_date": pd.to_datetime(["2026-01-01"] * (MIN_GROUP_N + 5)),
                "resolution_days": [1.0] * MIN_GROUP_N + [10.0] * 5,
            }
        )
        result = compare_group_delays(frame, ["borough"])
        statuses = result.set_index("group_value")["sample_status"].to_dict()
        self.assertEqual(statuses, {"A": "meets_minimum", "B": "small_sample"})
        self.assertEqual(int(result.loc[result["group_value"].eq("B"), "sample_size"].iloc[0]), 5)

    def test_zscore_and_mad_refuse_insufficient_history_and_zero_dispersion(self):
        short = detect_zscore([2, 2, 2, 2, 2], 40)
        zero_dispersion = detect_mad([2] * 6, 40)
        self.assertEqual(short["status"], "insufficient_history_or_invalid_value")
        self.assertEqual(zero_dispersion["status"], "zero_or_invalid_mad")

    def test_monthly_anomaly_uses_local_ordered_period_reference(self):
        periods = pd.period_range("2025-01", periods=7, freq="M")
        table = pd.DataFrame(
            {
                "month": periods.astype(str),
                "month_start": periods.to_timestamp(),
                "request_count": [100, 101, 99, 100, 102, 98, 250],
            }
        )
        result = scan_monthly_anomalies(table)
        self.assertEqual(len(result), 2)
        self.assertTrue(result["reference_period"].eq("2025-01–2025-06").all())
        self.assertTrue(result["status"].eq("anomaly").all())

    def test_anomaly_scan_refuses_gapped_monthly_series(self):
        periods = pd.PeriodIndex(["2025-01", "2025-02", "2025-04", "2025-05",
                                  "2025-06", "2025-07", "2025-08"], freq="M")
        table = pd.DataFrame(
            {
                "month": periods.astype(str),
                "month_start": periods.to_timestamp(),
                "request_count": [100, 101, 99, 100, 102, 98, 250],
            }
        )
        with self.assertRaisesRegex(ValueError, "unique and contiguous"):
            scan_monthly_anomalies(table)

    def test_seasonal_mann_kendall_requires_36_contiguous_periods(self):
        periods = pd.period_range("2025-01", periods=4, freq="M")
        table = pd.DataFrame(
            {"month": periods.astype(str), "request_count": [10, 12, 14, 16]}
        )
        self.assertEqual(mann_kendall_trend(table)["status"], "insufficient_history")

    def test_seasonal_mann_kendall_detects_increase_within_seasons(self):
        periods = pd.period_range("2024-01", periods=36, freq="M")
        seasonal_baseline = [
            100, 150, 200, 250, 300, 350, 400, 350, 300, 250, 200, 150
        ]
        table = pd.DataFrame(
            {
                "month": periods.astype(str),
                "request_count": [
                    seasonal_baseline[index % 12] + (index // 12) * 50
                    for index in range(len(periods))
                ],
            }
        )
        result = mann_kendall_trend(table)
        self.assertEqual(result["status"], "significant")
        self.assertEqual(result["direction"], "up")
        self.assertLess(result["p_value"], result["alpha"])

    def test_seasonal_mann_kendall_does_not_mistake_seasonality_for_trend(self):
        periods = pd.period_range("2024-01", periods=36, freq="M")
        seasonal_pattern = [
            100, 150, 200, 250, 300, 350, 400, 350, 300, 250, 200, 150
        ]
        table = pd.DataFrame(
            {
                "month": periods.astype(str),
                "request_count": [
                    seasonal_pattern[index % 12] for index in range(len(periods))
                ],
            }
        )
        result = mann_kendall_trend(table)
        self.assertEqual(result["status"], "no_variation")
        self.assertEqual(result["direction"], "none")

    def test_mann_kendall_refuses_gapped_periods(self):
        periods = pd.PeriodIndex(
            ["2024-01", "2024-02", "2024-04", *[
                str(period) for period in pd.period_range("2024-05", periods=22, freq="M")
            ]],
            freq="M",
        )
        table = pd.DataFrame(
            {"month": periods.astype(str), "request_count": range(len(periods))}
        )
        self.assertEqual(mann_kendall_trend(table)["status"], "irregular_periods")

    def test_missing_real_inputs_never_fall_back_to_synthetic(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "synthetic fallback is disabled"):
                load_real_bundle(Path(directory))

    def test_trend_tool_excludes_partial_periods_and_refuses_gaps(self):
        bundle = load_real_bundle(ROOT)
        bundle["trend"] = pd.concat(
            [
                bundle["trend"],
                pd.DataFrame(
                    {
                        "month": ["2026-10"],
                        "month_start": [pd.Timestamp("2026-10-01")],
                        "request_count": [999_999],
                    }
                ),
            ],
            ignore_index=True,
        )
        bundle["trend"] = bundle["trend"].loc[
            ~bundle["trend"]["month"].astype(str).eq("2026-07")
        ]
        result = execute_tool(
            "analyze_trend",
            "Analyse la tendance mensuelle.",
            AnalysisContext(project_root=ROOT),
            bundle,
        )
        self.assertEqual(result.trace.status, "skipped")
        self.assertEqual(
            result.result["trend_significance"]["status"], "irregular_periods"
        )
        self.assertEqual(result.result["complete_period_count"], 3)
        self.assertFalse(
            any(fact.metric == "monthly_request_volume_change" for fact in result.evidence)
        )


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.fact = EvidenceFact(
            fact_id="delay.borough.A",
            metric="median_resolution_days",
            scope="borough=A",
            value=3.5,
            unit="calendar days",
            n=40,
            source="data/processed/nyc311_clean.csv#borough=A",
            source_sha256="a" * 64,
        )
        self.required = {"sample_only", "nonrandom_sample", "no_cause"}
        self.response = {
            "decision": "answer",
            "finding": "descriptive",
            "evidence_fact_ids": [self.fact.fact_id],
            "insight": "Les médianes varient entre les groupes comparables.",
            "recommendation": "Examiner les dossiers et vérifier leur couverture.",
            "limitation_codes": sorted(self.required),
        }

    def test_pydantic_context_accepts_windows_path_string(self):
        context = AnalysisContext.model_validate({"project_root": str(ROOT)})
        self.assertEqual(context.project_root, ROOT.resolve())

    def test_valid_response_can_reference_only_allowlisted_facts(self):
        parsed, result = validate_interpretation(
            json.dumps(self.response),
            [self.fact],
            self.required,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(result.status, "validated")

    def test_invented_fact_number_and_causal_claim_are_rejected(self):
        bad = {
            **self.response,
            "evidence_fact_ids": ["invented"],
            "insight": "La valeur de 42 augmente à cause du personnel.",
        }
        _, result = validate_interpretation(json.dumps(bad), [self.fact], self.required)
        self.assertEqual(result.status, "rejected")
        self.assertIn("unknown_evidence_fact_ids:invented", result.errors)
        self.assertIn("unverified_numeric_text", result.errors)
        self.assertIn("possible_unsupported_causal_claim", result.errors)

    def test_possible_causal_influence_is_rejected(self):
        bad = {
            **self.response,
            "recommendation": (
                "Les conditions locales pourraient influencer les délais de résolution."
            ),
        }
        _, result = validate_interpretation(
            json.dumps(bad),
            [self.fact],
            self.required,
        )
        self.assertFalse(result.causal_claims_clear)
        self.assertIn("possible_unsupported_causal_claim", result.errors)

    def test_dynamic_selection_rejects_unknown_evidence_slots(self):
        selection, result = validate_selection(
            json.dumps(
                {
                    "evidence_slots": ["E*", "E1"],
                    "recommendation_code": "review_comparison",
                }
            ),
            {"E1": self.fact.fact_id, "E2": "delay.borough.B"},
            {"review_comparison"},
        )
        self.assertIsNotNone(selection)
        self.assertEqual(result.status, "rejected")
        self.assertFalse(result.schema_valid)
        self.assertFalse(result.evidence_valid)
        self.assertIn("unknown_evidence_slots:E*", result.errors)

    def test_duplicate_json_keys_are_rejected(self):
        _, result = validate_interpretation(
            '{"decision":"answer","decision":"refuse"}',
            [self.fact],
            self.required,
        )
        self.assertFalse(result.json_valid)

    def test_schema_forbids_additional_properties(self):
        bad = {**self.response, "invented_score": 0.99}
        _, result = validate_interpretation(json.dumps(bad), [self.fact], self.required)
        self.assertFalse(result.schema_valid)


class OrchestrationTests(unittest.TestCase):
    def test_requested_trend_always_carries_statistical_test_evidence(self):
        monthly_fact = EvidenceFact(
            fact_id="trend.change_pct.2026-07",
            metric="monthly_request_volume_change",
            scope="population volume change during 2026-07",
            value=2,
            unit="percent",
            n=100,
            source="data/processed/kpi_monthly.csv#month=2026-07",
        )
        test_fact = EvidenceFact(
            fact_id="trend.monthly_volume_mann_kendall",
            metric="monthly_trend_seasonal_kendall_tau",
            scope="population monthly request volume, 2024-01 to 2026-12",
            value=0.8,
            unit="Kendall tau-b",
            n=36,
            source="data/processed/kpi_monthly.csv#method=seasonal_mann_kendall",
            method="Seasonal Mann-Kendall",
            threshold=0.05,
            p_value=0.01,
        )
        result = _include_required_trend_evidence(
            "La tendance est-elle significative ?",
            [monthly_fact],
            [monthly_fact, test_fact],
        )
        self.assertEqual(
            [fact.fact_id for fact in result],
            [monthly_fact.fact_id, test_fact.fact_id],
        )

    def test_question_routes_to_allowlisted_tools(self):
        self.assertEqual(
            select_tools("Compare les délais par arrondissement."),
            ["data_quality", "compare_groups"],
        )
        self.assertEqual(
            select_tools("Y a-t-il un pic anormal de plaintes ?"),
            ["data_quality", "analyze_trend", "detect_anomalies"],
        )
        self.assertIn(
            "summarize_kpis",
            select_tools("Prépare des insights pour le comité de pilotage."),
        )

    def test_preflight_refuses_wrong_or_missing_model_before_tools(self):
        client = FakeOllama(installed={"qwen3:4b": {"name": "qwen3:4b"}})
        with tempfile.TemporaryDirectory() as directory:
            report = analyze(
                "Compare les délais par arrondissement.",
                AnalysisContext(project_root=ROOT),
                client=client,
                audit_root=Path(directory),
            )
        self.assertEqual(report.status, "failed")
        self.assertIn("required_model_not_installed", report.errors[0])
        self.assertEqual(report.tool_trace, [])
        self.assertEqual(client.calls, [])

    def test_two_invalid_model_responses_fall_back_and_are_audited(self):
        client = FakeOllama(responses=["{invalid", "{still invalid"])
        with tempfile.TemporaryDirectory() as directory:
            report = analyze(
                "Compare les délais de résolution par arrondissement.",
                AnalysisContext(project_root=ROOT),
                client=client,
                audit_root=Path(directory),
            )
            audit_files = list(Path(directory).glob("*/audit.jsonl"))
            events = [json.loads(line) for line in audit_files[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(report.status, "fallback")
        self.assertEqual(
            len([call for call in client.calls if call[0] == "chat"]),
            2,
        )
        self.assertEqual(
            len([event for event in events if event["event"] == "llm_response"]),
            2,
        )
        self.assertTrue(report.evidence)
        response_events = [event for event in events if event["event"] == "llm_response"]
        self.assertEqual(
            [event["raw_response"] for event in response_events],
            ["{invalid", "{still invalid"],
        )

    def test_valid_model_response_is_bound_to_python_evidence(self):
        question = "Compare les délais de résolution par arrondissement."
        class DraftOllama(FakeOllama):
            def call(self, endpoint, payload=None, timeout=None):
                if endpoint == "chat":
                    self.responses.append(json.dumps(json.loads(payload["messages"][-1]["content"])["examples"][0]))
                return super().call(endpoint, payload, timeout)

        with tempfile.TemporaryDirectory() as directory:
            client = DraftOllama()
            report = analyze(
                question,
                AnalysisContext(project_root=ROOT),
                client=client,
                audit_root=Path(directory) / "agent",
            )
        self.assertEqual(report.status, "validated")
        self.assertEqual(report.model, MODEL_NAME)
        self.assertEqual(report.validation.status, "validated")
        self.assertEqual(
            report.insights[0].evidence_fact_ids,
            [
                "delay.borough.STATEN ISLAND",
                "delay.borough.BROOKLYN",
                "delay.borough.MANHATTAN",
                "delay.borough.BRONX",
                "delay.borough.QUEENS",
            ],
        )
        self.assertIn("écart descriptif calculé", report.insights[0].insight)
        self.assertEqual(
            report.insights[0].recommendation,
            "Examiner les groupes concernés et vérifier la comparabilité des dossiers avant toute action.",
        )
        chat_payload = next(call[1] for call in client.calls if call[0] == "chat")
        self.assertEqual(
            chat_payload["format"]["properties"]["evidence_ids"]["items"]["enum"],
            report.insights[0].evidence_fact_ids,
        )
        self.assertNotIn("value", str(chat_payload["messages"]))
        self.assertNotIn("fact_id", str(chat_payload["messages"]))
        self.assertEqual(
            len([call for call in client.calls if call[0] == "chat"]),
            1,
        )

    def test_real_baseline_is_auditable_and_excludes_partial_month(self):
        with tempfile.TemporaryDirectory() as directory:
            report = analyze(
                "Compare les délais de résolution par arrondissement.",
                AnalysisContext(project_root=ROOT),
                use_llm=False,
                audit_root=Path(directory),
            )
        self.assertEqual(report.status, "fallback")
        self.assertIsNone(report.model)
        self.assertTrue(report.evidence)
        self.assertIn("partial_month", report.insights[0].limitation_codes)
        self.assertTrue(all(fact.source_sha256 for fact in report.evidence))
        self.assertEqual(
            next(
                fact.value
                for fact in report.evidence
                if fact.metric == "missing_or_invalid_resolution_days"
            ),
            6.0,
        )
        self.assertIn(
            "quality.missing_resolution_days",
            {fact.fact_id for fact in report.evidence},
        )

    def test_quality_report_distinguishes_open_requests_and_invalid_closed_delays(self):
        report = analyze(
            "Quels sont les effets des données manquantes sur l'analyse des délais ?",
            AnalysisContext(project_root=ROOT),
            use_llm=False,
        )
        facts = {fact.metric: fact.value for fact in report.evidence}
        self.assertEqual(facts["missing_or_invalid_resolution_days"], 6.0)
        self.assertEqual(facts["not_closed_requests"], 709.0)

    def test_broad_summary_only_computes_requested_kpis(self):
        report = analyze(
            "Prépare des insights prudents pour le comité de pilotage.",
            AnalysisContext(project_root=ROOT),
            use_llm=False,
        )
        self.assertEqual(report.decision, "answer")
        self.assertEqual([trace.tool for trace in report.tool_trace], ["data_quality", "summarize_kpis"])
        self.assertTrue(report.insights[0].insight)

    def test_real_trend_question_is_refused_without_36_complete_months(self):
        report = analyze(
            "La tendance des volumes mensuels est-elle significative ?",
            AnalysisContext(project_root=ROOT),
            use_llm=False,
        )
        self.assertEqual(report.decision, "insufficient_data")
        self.assertEqual(report.finding, "insufficient_history")
        self.assertIn("insufficient_history", report.insights[0].limitation_codes)
        self.assertIn("Mann–Kendall", report.insights[0].insight)
        self.assertIn("36 périodes", report.insights[0].insight)
        self.assertTrue(
            any(fact.metric == "monthly_request_volume_change" for fact in report.evidence)
        )
        self.assertFalse(
            any(
                fact.metric == "monthly_trend_seasonal_kendall_tau"
                for fact in report.evidence
            )
        )

    def test_steering_committee_baseline_renders_multiple_python_kpis(self):
        report = analyze(
            "Prépare des insights pour le comité de pilotage.",
            AnalysisContext(project_root=ROOT),
            use_llm=False,
        )
        self.assertIn("Synthèse des KPI", report.insights[0].insight)
        self.assertIn("volume :", report.insights[0].insight)
        self.assertIn("durée moyenne :", report.insights[0].insight)
        self.assertIn("taux de clôture :", report.insights[0].insight)


class EvaluationTests(unittest.TestCase):
    def test_baseline_only_metrics_do_not_claim_llm_comparison(self):
        baseline = SimpleNamespace(
            validation=SimpleNamespace(status="validated"),
            evidence=[],
            decision="answer",
            status="fallback",
            timing_ms={"total": 12.0},
        )
        result = _summary_metrics(
            [{"baseline": baseline, "agent": baseline}],
            model_inference_enabled=False,
        )
        self.assertIsNone(result["factual_evidence_accuracy"])
        self.assertIsNone(result["agent_fully_validated_rate"])
        self.assertIsNone(result["agent_mean_elapsed_ms"])
        self.assertIsNone(result["agent_refusal_or_controlled_failure_rate"])
        self.assertEqual(result["baseline_mean_elapsed_ms"], 12.0)
        self.assertEqual(result["baseline_refusal_or_controlled_failure_rate"], 0.0)

    def test_evaluation_counts_unknown_selection_slots(self):
        baseline = SimpleNamespace(
            validation=SimpleNamespace(status="validated", errors=[]),
            evidence=[],
            decision="answer",
            status="fallback",
            timing_ms={"total": 12.0},
        )
        agent = SimpleNamespace(
            validation=SimpleNamespace(
                status="rejected",
                errors=["unknown_evidence_slots:E*"],
            ),
            evidence=[],
            decision="answer",
            status="fallback",
            timing_ms={"total": 12.0},
        )
        result = _summary_metrics(
            [{"baseline": baseline, "agent": agent}],
            model_inference_enabled=True,
        )
        self.assertEqual(result["unsupported_evidence_selection_errors"], 1)


if __name__ == "__main__":
    unittest.main()
