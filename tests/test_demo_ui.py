"""AppTest fixtures exercise UI behavior only; they are not model observations."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from bi_analyst.demo_service import DemoRun
from bi_analyst.models import BIAnalysisReport

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "demo/streamlit_app.py"

SCENARIOS = [
    dict(id="compare_boroughs", title="Comparer les arrondissements", question="Comparer les délais par arrondissement.",
         description="Fixture UI, aucun résultat réel.", supports_live=True),
    dict(id="drilldown", title="Investiguer une dégradation", question="Investiguer la hausse des délais à Brooklyn.",
         description="Fixture UI avec plan d'investigation.", supports_live=True),
    dict(id="history", title="Historique insuffisant", question="Peut-on conclure sur cet historique ?",
         description="Fixture UI illustrant une limite.", supports_live=True),
    dict(id="rejection", title="Rejet contrôlé", question="Démontrer le rejet d'une réponse injectée.",
         description="Fixture UI contrôlée; aucune sortie authentique du modèle.", supports_live=False),
]


def fixture_run(*, mode="replay", status="validated", decision="answer", controlled=False):
    report = BIAnalysisReport.model_validate(dict(
        contract_version="biflow-bi-analyst-v2", execution_id="UNIT-TEST-ONLY", generated_at="2026-10-28T00:00:00+00:00",
        question="Question de fixture synthétique UI; aucune inférence réelle.", model="granite3.3:2b", data_kind="synthetic",
        status=status, decision=decision, finding="descriptive",
        insights=[dict(insight_id="unit-insight", category="comparison", finding="descriptive",
                       insight="Constat de fixture UI uniquement.", recommendation="Vérifier les dossiers du segment de test.",
                       evidence_fact_ids=["unit.fact"], limitation_codes=["synthetic_only"])],
        evidence=[dict(fact_id="unit.fact", metric="median_resolution_days", scope="unit-test",
                       value=0.25, unit="days", n=40, source="synthetic://ui-fixture",
                       source_sha256="a" * 64, data_kind="synthetic")],
        limitations=["SCÉNARIO SYNTHÉTIQUE DE TEST, pas une observation NYC 311."],
        source_refs=["synthetic://ui-fixture"], validation=dict(
            status="rejected" if status == "fallback" else "not_run" if status == "failed" else "validated",
            json_valid=status != "failed", schema_valid=status == "validated", evidence_valid=status == "validated",
            numerical_claims_valid=status == "validated", causal_claims_clear=status == "validated",
            errors=["unknown_evidence_fact_ids:unit.*"] if status == "fallback" else []),
        tool_trace=[dict(tool="investigate_degradation", parameters={"question": "unit-test"}, status="success",
                         started_at="2026-10-28T00:00:00Z", elapsed_ms=4.0, evidence_ids=["unit.fact"])],
        errors=["unknown_evidence_fact_ids:unit.*"] if status == "fallback" else [], timing_ms={"total": 1250.0},
        decision_support=[dict(
            problem_id="unit-scope", problem="Problème de fixture UI à investiguer.",
            priority_scope=dict(scope_id="unit-scope", filters={"borough": "BROOKLYN", "agency": "HPD", "complaint_type": "UNIT_TEST"},
                                baseline_period="2026-08", current_period="2026-09"),
            evidence_ids=["unit.fact"], investigation_hypotheses=[dict(
                hypothesis_id="unit_check", statement="Hypothèse de fixture non vérifiée.", status="unverified",
                evidence_ids=["unit.fact"], additional_data=["Journaux d'affectation du segment de test."])],
            investigation_steps=[dict(hypothesis_id="unit_check", dataset="additional_data",
                                      filters={"borough": "BROOKLYN", "baseline_closure_month": "2026-08", "current_closure_month": "2026-09"},
                                      fields=["unique_key", "assignment_timestamp"], comparison="Comparer les étapes dans la même catégorie et agence.")],
            recommended_actions=[dict(hypothesis_id="unit_check", priority=1, action="Extraire les journaux du segment de test.",
                                      priority_reason="Raison de fixture synthétique.", decision_if_supported="Examiner l'étape concernée.",
                                      decision_if_refuted="Écarter cette piste.")],
            expected_verification=[dict(hypothesis_id="unit_check", supports="Écart persistant à dossiers comparables.",
                                        refutes="Écart absent à dossiers comparables.")],
            limitations=["Fixture synthétique UI."],
            origin="python_fallback" if status == "fallback" else "granite_validated")]))
    validation = dict(status="rejected" if status == "fallback" else "validated", errors=report.validation.errors)
    events = [dict(event="llm_request", attempt=1), dict(event="llm_response", attempt=1, raw_response='{"fixture_only":true}', elapsed_ms=10.0),
              dict(event="validation", attempt=1, result=validation)]
    if status == "fallback":
        events.extend([dict(event="llm_response", attempt=2, raw_response='{"injected":true}'),
                       dict(event="validation", attempt=2, result=validation), dict(event="deterministic_fallback")])
    return DemoRun(report=report, events=events, analysis={"fixture_only": True, "periods": ["2026-08", "2026-09"]},
                   mode=mode, origin="FIXTURE UI SYNTHÉTIQUE — aucun score empirique", path=ROOT / "outputs/unit-ui-fixture",
                   verification={"fixture_only": True}, display_metadata=dict(
                       source_mode="controlled_test" if controlled else "live", injected=controlled,
                       llm_called_now=mode == "live", original_elapsed_ms=1250.0))


class DemoUITests(unittest.TestCase):
    def setUp(self):
        self.scenarios = patch("bi_analyst.demo_service.load_scenarios", return_value=deepcopy(SCENARIOS)).start()
        self.recording = patch("bi_analyst.demo_service.load_recording", return_value=fixture_run()).start()
        self.live = patch("bi_analyst.demo_service.run_live", return_value=fixture_run(mode="live")).start()
        self.controlled = patch("bi_analyst.demo_service.run_rejection_demo", return_value=fixture_run(
            mode="controlled_test", status="fallback", controlled=True)).start()
        self.addCleanup(patch.stopall)

    def app(self):
        return AppTest.from_file(str(APP), default_timeout=30).run()

    def text(self, app, element):
        return "\n".join(str(item.value) for item in getattr(app, element))

    def assert_no_exception(self, app):
        self.assertEqual(list(app.exception), [])

    def test_default_replay_is_explicit_and_never_calls_live_or_injection(self):
        app = self.app()
        self.assert_no_exception(app)
        self.assertIn("RESTITUTION ENREGISTRÉE", self.text(app, "warning"))
        self.assertIn("aucune inférence LLM en direct", self.text(app, "warning"))
        self.assertIn("DONNÉES SYNTHÉTIQUES DE TEST", self.text(app, "warning"))
        self.assertTrue(app.text_area(key="question").disabled)
        self.assertEqual(self.recording.call_count, 1)
        self.live.assert_not_called()
        self.controlled.assert_not_called()
        self.assertEqual([item.label for item in app.tabs], ["Synthèse", "Preuves et outils", "Investigation", "Validation et audit", "Rapport JSON"])
        self.assertIn("Durée d'origine (s)", [item.label for item in app.metric])

    def test_replay_scenario_switch_loads_matching_recording_and_updates_question(self):
        app = self.app()
        app.selectbox(key="scenario_id").set_value("drilldown").run()
        self.assert_no_exception(app)
        self.recording.assert_called_with("drilldown", ROOT)
        self.assertEqual(app.text_area(key="question").value, SCENARIOS[1]["question"])
        self.live.assert_not_called()

    def test_live_requires_button_and_preserves_the_entered_question(self):
        app = self.app()
        app.radio(key="mode_choice").set_value("live").run()
        self.assert_no_exception(app)
        self.live.assert_not_called()
        self.assertNotIn("RESTITUTION ENREGISTRÉE", self.text(app, "warning"))
        self.assertEqual(len(app.metric), 0)
        app.text_area(key="question").set_value("Investiguer un segment précisément défini.").run()
        app.button(key="run_live").click().run()
        self.assert_no_exception(app)
        self.live.assert_called_once_with("Investiguer un segment précisément défini.", ROOT)
        self.assertIn("EXÉCUTION EN DIRECT", self.text(app, "info"))
        self.assertNotIn("Durée d'origine (s)", [item.label for item in app.metric])

    def test_editing_live_question_clears_previous_result_until_a_new_run(self):
        app = self.app()
        app.radio(key="mode_choice").set_value("live").run()
        app.button(key="run_live").click().run()
        self.assertGreater(len(app.metric), 0)
        app.text_area(key="question").set_value("Nouvelle question sur les médianes, sans analyse temporelle.").run()
        self.assert_no_exception(app)
        self.assertEqual(len(app.metric), 0)
        self.assertEqual(self.live.call_count, 1)
        run = fixture_run(mode="live")
        run.report.execution_id = "SECOND-RUN-ONLY"
        self.live.return_value = run
        app.button(key="run_live").click().run()
        self.assert_no_exception(app)
        self.assertIn("SECOND-RUN-ONLY", self.text(app, "caption"))
        self.assertEqual(self.live.call_count, 2)

    def test_controlled_mode_discloses_injection_and_python_fallback(self):
        app = self.app()
        app.radio(key="mode_choice").set_value("controlled_test").run()
        self.controlled.assert_not_called()
        app.button(key="run_controlled").click().run()
        self.assert_no_exception(app)
        self.controlled.assert_called_once_with(ROOT)
        self.live.assert_not_called()
        self.assertIn("réponses incorrectes sont injectées", self.text(app, "warning"))
        self.assertIn("FALLBACK PYTHON", self.text(app, "warning"))
        self.assertIn("ne sont pas des réponses Granite authentiques", self.text(app, "warning"))

    def test_archived_injected_result_cannot_be_mistaken_for_authentic_granite_error(self):
        self.recording.return_value = fixture_run(status="fallback", controlled=True)
        app = self.app()
        self.assert_no_exception(app)
        self.assertIn("RESTITUTION ENREGISTRÉE", self.text(app, "warning"))
        self.assertIn("archive restitue un test avec réponses injectées", self.text(app, "warning"))

    def test_insufficient_history_is_distinguished_from_failed_generation(self):
        self.recording.return_value = fixture_run(decision="insufficient_data")
        app = self.app()
        self.assert_no_exception(app)
        self.assertIn("DONNÉES INSUFFISANTES", self.text(app, "info"))
        self.assertNotIn("FALLBACK PYTHON", self.text(app, "warning"))
        self.assertIn("validated", [item.value for item in app.metric])

    def test_failed_preflight_never_claims_that_a_model_call_was_made(self):
        run = fixture_run(mode="live", status="failed", decision="refuse")
        run.display_metadata["llm_called_now"] = False
        self.live.return_value = run
        app = self.app()
        app.radio(key="mode_choice").set_value("live").run()
        app.button(key="run_live").click().run()
        self.assert_no_exception(app)
        self.assertIn("aucun appel LLM n'a pu être lancé", self.text(app, "info"))
        self.assertIn("ÉCHEC CONTRÔLÉ", self.text(app, "error"))
        self.assertIn("REFUS JUSTIFIÉ", self.text(app, "info"))

    def test_read_failure_clears_previous_output_without_inventing_a_replacement(self):
        app = self.app()
        self.recording.side_effect = ValueError("Empreinte incorrecte de fixture")
        app.selectbox(key="scenario_id").set_value("history").run()
        self.assert_no_exception(app)
        self.assertIn("Empreinte incorrecte", self.text(app, "error"))
        self.assertEqual(len(app.metric), 0)
        self.live.assert_not_called()

    def test_concrete_plan_tools_evidence_and_downloads_are_present(self):
        app = self.app()
        self.assert_no_exception(app)
        self.assertGreaterEqual(len(app.dataframe), 3)
        markdown = self.text(app, "markdown")
        self.assertIn("Journaux d'affectation", markdown)
        self.assertIn("Comparer les étapes dans la même catégorie", markdown)
        self.assertIn("Si la piste est soutenue", markdown)
        self.assertIn("Pour l'écarter", markdown)
        downloads = app.get("download_button")
        self.assertEqual(len(downloads), 3)
        labels = [item.proto.label for item in downloads]
        self.assertIn("Télécharger le rapport JSON", labels)
        self.assertIn("Télécharger le journal d'audit JSONL", labels)


class DemoRecordingUITests(unittest.TestCase):
    def test_all_four_saved_scenarios_render_without_model_construction(self):
        from bi_analyst.demo_service import load_scenarios
        scenarios = load_scenarios(ROOT)
        self.assertEqual(len(scenarios), 4)
        with patch("benchmark_llm.runner.OllamaClient.__init__", side_effect=AssertionError("Replay must not construct an Ollama client")), \
             patch("bi_analyst.demo_service.run_live", side_effect=AssertionError("Replay must not execute live analysis")):
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            self.assertEqual(list(app.exception), [])
            for scenario in scenarios:
                with self.subTest(scenario=scenario["id"]):
                    app.selectbox(key="scenario_id").set_value(scenario["id"]).run()
                    self.assertEqual(list(app.exception), [])
                    self.assertGreater(len(app.metric), 0)
                    warning = "\n".join(str(item.value) for item in app.warning)
                    self.assertIn("RESTITUTION ENREGISTRÉE", warning)
                    self.assertEqual(app.session_state["active_run"].mode, "replay")
                    self.assertEqual(app.session_state["active_run"].display_metadata["llm_called_now"], False)
                    if not scenario.get("supports_live", True):
                        self.assertIn("archive restitue un test avec réponses injectées", warning)


if __name__ == "__main__":
    unittest.main()
