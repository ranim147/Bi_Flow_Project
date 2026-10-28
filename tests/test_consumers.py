"""Simulated consumers only: no other agent implementation or model inference."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from bi_analyst.consumers import check_consumers
from bi_analyst.models import BIAnalysisReport

ROOT = Path(__file__).resolve().parents[1]


def report_fixture(*, version="v1", data_kind="real", status="validated", decision="answer"):
    source = ("synthetic://consumer-test/requests.csv" if data_kind == "synthetic"
              else "data/processed/nyc311_clean.csv") + "#borough=BROOKLYN"
    fact_id = "delay.borough.BROOKLYN"
    fact = dict(fact_id=fact_id, metric="mean_resolution_days", scope="borough=BROOKLYN",
                value=2.5, unit="days", n=40, source=source, source_sha256="a" * 64, data_kind=data_kind)
    validation = dict(status="validated", json_valid=True, schema_valid=True, evidence_valid=True,
                      numerical_claims_valid=True, causal_claims_clear=True, errors=[])
    finding = "insufficient_history" if decision == "insufficient_data" else "descriptive"
    payload = dict(contract_version="biflow-bi-analyst-" + version, execution_id="consumer-test",
                   generated_at="2026-10-28T12:00:00+00:00", question="Compare les délais à Brooklyn.",
                   model="granite3.3:2b", data_kind=data_kind, status=status, decision=decision, finding=finding,
                   insights=[dict(insight_id="consumer-test-scope", category="comparison", finding=finding,
                                  insight="Comparaison descriptive des durées observées.", recommendation="Vérifier les dossiers du périmètre.",
                                  evidence_fact_ids=[fact_id], limitation_codes=["no_cause"])],
                   evidence=[fact], limitations=["Aucune cause n'est établie."], source_refs=[source], validation=validation,
                   tool_trace=[dict(tool="compare_groups", parameters={"question": "Compare les délais à Brooklyn."},
                                    status="success", started_at="2026-10-28T12:00:00+00:00", elapsed_ms=1.0,
                                    evidence_ids=[fact_id, "quality.computed_but_not_retained"])], errors=[], timing_ms={"total": 2.0})
    if version == "v2":
        payload["decision_support"] = [dict(
            problem_id="scope", problem="Un écart descriptif mérite une vérification.",
            priority_scope=dict(scope_id="scope", filters={"borough": "BROOKLYN"}, baseline_period="2026-08", current_period="2026-09"),
            evidence_ids=[fact_id],
            investigation_hypotheses=[dict(hypothesis_id="composition", statement="Le mix doit être vérifié.", status="unverified", evidence_ids=[fact_id], additional_data=["Complexité des dossiers"])],
            investigation_steps=[dict(hypothesis_id="composition", dataset="prepared_extract", filters={"borough": "BROOKLYN"}, fields=["complaint_type", "agency"], comparison="Comparer les mêmes catégories et périodes.")],
            recommended_actions=[dict(hypothesis_id="composition", priority=1, action="Standardiser les catégories.", priority_reason="Le mix est à vérifier.", decision_if_supported="Présenter un indicateur standardisé.", decision_if_refuted="Examiner les étapes de traitement.")],
            expected_verification=[dict(hypothesis_id="composition", supports="L'écart disparaît à mix fixe.", refutes="L'écart persiste à mix fixe.")],
            limitations=["La comparaison reste descriptive."], origin="granite_validated")]
    if status == "fallback":
        validation.update(status="rejected", schema_valid=False, errors=["test_rejection"])
        payload["errors"] = ["test_rejection"]
        if version == "v2":
            payload["decision_support"][0]["origin"] = "python_fallback"
    if status == "failed":
        payload.update(decision="refuse", finding="insufficient_data", model=None, insights=[], evidence=[], source_refs=[], tool_trace=[], errors=["required_model_not_installed:granite3.3:2b"])
        payload.pop("decision_support", None)
        validation.update(status="not_run", json_valid=False, schema_valid=False, evidence_valid=False, numerical_claims_valid=False, causal_claims_clear=False)
    return payload


class ConsumerContractTests(unittest.TestCase):
    def test_both_versions_and_three_consumer_views(self):
        for version in ("v1", "v2"):
            with self.subTest(version=version):
                result = check_consumers(report_fixture(version=version))
                self.assertTrue(result["validated"])
                self.assertTrue(result["orchestrator"]["llm_validated"])
                self.assertTrue(result["dashboard"]["renderable"])
                self.assertTrue(result["auditor"]["canonical_recalculation_required"])
                self.assertEqual(result["checks_scope"], "schema_and_reference_consistency")

    def test_insufficiency_fallback_failed_refusal_and_python_baseline_stay_distinct(self):
        insufficient = check_consumers(report_fixture(decision="insufficient_data"))
        self.assertEqual(insufficient["orchestrator"]["route"], "insufficient_data")
        self.assertTrue(insufficient["orchestrator"]["llm_validated"])
        fallback = check_consumers(report_fixture(version="v2", status="fallback"))
        self.assertFalse(fallback["orchestrator"]["llm_validated"])
        self.assertTrue(fallback["orchestrator"]["python_fallback"])
        failed = check_consumers(report_fixture(status="failed"))
        self.assertEqual(failed["orchestrator"]["route"], "refused")
        self.assertFalse(failed["dashboard"]["renderable"])
        baseline = report_fixture()
        baseline.update(status="fallback", model=None)
        self.assertTrue(check_consumers(baseline)["orchestrator"]["python_fallback"])

    def test_unknown_wildcard_duplicate_and_untraced_references_rejected(self):
        original = report_fixture(version="v2")
        mutations = []
        value = copy.deepcopy(original); value["insights"][0]["evidence_fact_ids"] = ["invented.*"]; mutations.append(value)
        value = copy.deepcopy(original); value["evidence"].append(copy.deepcopy(value["evidence"][0])); mutations.append(value)
        value = copy.deepcopy(original); value["tool_trace"][0]["evidence_ids"] = []; mutations.append(value)
        value = copy.deepcopy(original); value["tool_trace"][0]["evidence_ids"] = ["delay.*"]; mutations.append(value)
        value = copy.deepcopy(original); value["source_refs"] = ["invented://source"]; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["evidence_ids"] = ["unknown"]; mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ValueError):
                check_consumers(value)

    def test_validation_status_and_provenance_corruption_rejected(self):
        original = report_fixture()
        mutations = []
        value = copy.deepcopy(original); value["validation"]["status"] = "rejected"; mutations.append(value)
        value = copy.deepcopy(original); value["validation"]["evidence_valid"] = False; mutations.append(value)
        value = copy.deepcopy(original); value["errors"] = ["blocked"]; mutations.append(value)
        value = copy.deepcopy(original); value["evidence"][0]["source_sha256"] = None; mutations.append(value)
        value = copy.deepcopy(original); value["timing_ms"]["total"] = float("inf"); mutations.append(value)
        value = copy.deepcopy(original); value["generated_at"] = "2026-10-28"; mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ValueError):
                check_consumers(value)

    def test_plan_scope_hypothesis_operations_and_outcomes_are_bound(self):
        original = report_fixture(version="v2")
        mutations = []
        value = copy.deepcopy(original); value["decision_support"][0]["priority_scope"]["scope_id"] = "other"; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["investigation_steps"][0]["filters"]["borough"] = "QUEENS"; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["expected_verification"] = []; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["recommended_actions"][0]["hypothesis_id"] = "unknown"; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["recommended_actions"][0]["priority"] = 3; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["expected_verification"][0]["refutes"] = value["decision_support"][0]["expected_verification"][0]["supports"]; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["origin"] = "python_fallback"; mutations.append(value)
        value = copy.deepcopy(original); value["decision_support"][0]["priority_scope"]["current_period"] = "2026-07"; mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ValueError):
                check_consumers(value)

    def test_real_and_synthetic_are_segregated_and_v1_projection_checked(self):
        synthetic = report_fixture(version="v2", data_kind="synthetic")
        self.assertEqual(check_consumers(synthetic)["dashboard"]["data_kind"], "synthetic")
        value = copy.deepcopy(synthetic); value["data_kind"] = "real"
        with self.assertRaises(ValueError):
            check_consumers(value)
        with self.assertRaises(ValueError):
            BIAnalysisReport.model_validate(synthetic).to_legacy_dict()
        projection = BIAnalysisReport.model_validate(report_fixture(version="v2")).to_legacy_dict()
        self.assertNotIn("decision_support", projection)
        self.assertEqual(check_consumers(projection)["contract_version"], "biflow-bi-analyst-v1")

    def test_mutated_model_instance_cannot_bypass_revalidation(self):
        report = BIAnalysisReport.model_validate(report_fixture())
        report.evidence[0].data_kind = "synthetic"
        with self.assertRaises(ValueError):
            check_consumers(report)

    def test_consumer_views_are_independent_copies(self):
        report = BIAnalysisReport.model_validate(report_fixture(version="v2"))
        result = check_consumers(report)
        result["dashboard"]["facts"][0]["value"] = 999.0
        result["dashboard"]["decision_support"][0]["priority_scope"]["filters"]["borough"] = "QUEENS"
        self.assertEqual(report.evidence[0].value, 2.5)
        self.assertEqual(report.decision_support[0].priority_scope.filters["borough"], "BROOKLYN")


class ArchivedConsumerIntegrationTests(unittest.TestCase):
    def test_existing_real_v1_and_v2_campaign_reports_are_consumable(self):
        roots = [(ROOT / "outputs/bi_analyst_finalization/granite_v2_release_20261028", "agent"),
                 (ROOT / "outputs/decision_support/granite_investigation_20261028", "report")]
        tested = 0
        for root, field in roots:
            if not root.exists():
                root = ROOT / "archive" / root.relative_to(ROOT)
            for path in root.glob("*.json"):
                content = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(content, dict) or field not in content:
                    continue
                with self.subTest(path=path.name):
                    self.assertTrue(check_consumers(content[field])["validated"])
                    tested += 1
        if not tested:
            self.skipTest("Optional archived campaigns are not distributed in this checkout.")
        self.assertGreaterEqual(tested, 2)


if __name__ == "__main__":
    unittest.main()
