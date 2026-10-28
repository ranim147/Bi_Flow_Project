"""Tests adversariaux hors ligne : aucune réponse testée n'est un score de modèle."""
import copy
import json
from pathlib import Path
from contextlib import contextmanager
import shutil
import time
import uuid
import unittest
from unittest.mock import patch
from urllib import error

import pandas as pd

from benchmark_llm.data import DataValidationError, build_cases, load_bundle
from benchmark_llm.protocol import MODELS, evaluate, payload_for, strict_json
from benchmark_llm.runner import OllamaClient, OllamaFailure, run_benchmark, score_runs, write_report

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def workspace_trial():
    """Dossier jetable avec ACL héritées (mkdtemp privé bloque certains bacs à sable Windows)."""
    base = (ROOT / ".tmp_jupyter").resolve()
    base.mkdir(exist_ok=True)
    folder = base / ("benchmark_test_" + uuid.uuid4().hex)
    folder.mkdir(exist_ok=False)
    try:
        yield folder
    finally:
        resolved = folder.resolve()
        if resolved.parent != base or not resolved.name.startswith("benchmark_test_"):
            raise RuntimeError("Nettoyage refusé hors du dossier temporaire prévu.")
        for attempt in range(20):
            try:
                if resolved.exists():
                    shutil.rmtree(resolved)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 19:
                    raise
                time.sleep(.25)


def reference_response(case):
    return {"case_id": case["case_id"], "data_kind": case["data_kind"],
            "decision": case["reference"]["decision"], "finding": case["reference"]["finding"],
            "claims": copy.deepcopy(case["evidence"]), "insight": "Description prudente des preuves disponibles.",
            "recommendation": "Examiner les dossiers et vérifier les limites avant toute action.",
            "limits": case["reference"]["required_limits"], "causal_explanation": None}


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(ROOT)
        cls.cases = build_cases(cls.bundle)

    def setUp(self):
        self.case = next(c for c in self.cases if c["evidence"] and c["evidence"][0]["value"] is not None)
        self.response = reference_response(self.case)

    def score(self):
        return evaluate(self.case, json.dumps(self.response, ensure_ascii=False))

    def test_reference_contracts_all_cases(self):
        for case in self.cases:
            with self.subTest(case=case["case_id"]):
                self.assertTrue(evaluate(case, json.dumps(reference_response(case)))["objective_pass"])

    def test_at_least_twenty_cases_and_both_origins(self):
        self.assertGreaterEqual(len(self.cases), 20)
        self.assertEqual({c["data_kind"] for c in self.cases},
                         {"real", "synthetic"} if self.bundle["kind"] == "real" else {"synthetic"})

    def test_invalid_json(self):
        self.assertFalse(evaluate(self.case, "{bad")["json_valid"])

    def test_fenced_json_not_silently_repaired(self):
        self.assertFalse(evaluate(self.case, "```json\n{}\n```")["json_valid"])

    def test_duplicate_keys(self):
        with self.assertRaises(ValueError):
            strict_json('{"value":1,"value":2}')

    def test_nonfinite_json(self):
        for value in ["NaN", "Infinity", "-Infinity"]:
            with self.assertRaises(ValueError):
                strict_json('{"value":'+value+'}')

    def test_missing_field(self):
        del self.response["claims"]
        self.assertFalse(self.score()["schema_valid"])

    def test_extra_field(self):
        self.response["fabricated_score"] = 1
        self.assertFalse(self.score()["schema_valid"])

    def test_wrong_numeric_type(self):
        self.response["claims"][0]["value"] = "10000"
        self.assertFalse(self.score()["schema_valid"])

    def test_boolean_not_number(self):
        self.response["claims"][0]["value"] = True
        self.assertFalse(self.score()["schema_valid"])

    def test_invented_value(self):
        self.response["claims"][0]["value"] += 37
        self.assertEqual(self.score()["numeric_mismatches"], 1)
        self.assertFalse(self.score()["objective_pass"])

    def test_wrong_group_even_with_existing_number(self):
        self.response["claims"][0]["scope"] = "Autre arrondissement"
        self.assertEqual(self.score()["numeric_accuracy"], 1)
        self.assertEqual(self.score()["scope_accuracy"], 0)
        self.assertFalse(self.score()["objective_pass"])

    def test_wrong_unit(self):
        self.response["claims"][0]["unit"] = "heures"
        self.assertFalse(self.score()["objective_pass"])

    def test_wrong_source(self):
        self.response["claims"][0]["source"] = "source inventée"
        self.assertEqual(self.score()["source_accuracy"], 0)

    def test_wrong_sample_size(self):
        self.response["claims"][0]["n"] += 1
        self.assertEqual(self.score()["sample_size_accuracy"], 0)

    def test_unknown_fact(self):
        self.response["claims"][0]["fact_id"] = "invented"
        self.assertEqual(self.score()["unsupported_claims"], 1)

    def test_duplicate_fact(self):
        self.response["claims"].append(copy.deepcopy(self.response["claims"][0]))
        self.assertEqual(self.score()["unsupported_claims"], 1)

    def test_empty_claims_cannot_pass_by_silence(self):
        self.response["claims"] = []
        self.assertEqual(self.score()["required_fact_recall"], 0)
        self.assertIsNone(self.score()["numeric_accuracy"])
        self.assertFalse(self.score()["objective_pass"])

    def test_required_limit_omission(self):
        self.response["limits"] = []
        self.assertFalse(self.score()["limits_ok"])

    def test_wrong_decision_and_finding(self):
        self.response["decision"] = "insufficient_data"
        self.response["finding"] = "statistical_anomaly"
        self.assertFalse(self.score()["decision_ok"])
        self.assertFalse(self.score()["finding_ok"])

    def test_real_synthetic_confusion(self):
        self.response["data_kind"] = "synthetic" if self.case["data_kind"] == "real" else "real"
        self.assertFalse(self.score()["identity_ok"])

    def test_numeric_hallucination_in_prose(self):
        self.response["recommendation"] = "Réduire les délais de 75 %."
        self.assertGreater(self.score()["prose_numeric_flags"], 0)
        self.assertFalse(self.score()["objective_pass"])

    def test_explicit_cause_is_flagged(self):
        self.response["causal_explanation"] = "Les délais sont causés par le manque de personnel."
        self.assertGreater(self.score()["causal_flags"], 0)
        self.assertFalse(self.score()["objective_pass"])

    def test_lexical_causal_flag_is_not_a_semantic_verdict(self):
        self.response["insight"] = "On ne sait pas si le délai est causé par un problème d'organisation."
        self.assertGreater(self.score()["causal_flags"], 0)
        self.assertTrue(self.score()["objective_pass"])

    def test_unavailable_response_has_no_quality_scores(self):
        result = evaluate(self.case, "", success=False)
        self.assertIsNone(result["objective_pass"])
        self.assertIsNone(result["numeric_accuracy"])
        self.assertFalse(result["evaluation_applicable"])

    def test_missing_value_cannot_be_zero(self):
        case = next(c for c in self.cases if c["reference"]["finding"] == "missing_data")
        response = reference_response(case)
        response["claims"][0]["value"] = 0
        self.assertFalse(evaluate(case, json.dumps(response))["objective_pass"])

    def test_prompt_is_identical_except_model_and_no_reference_leak(self):
        a, b = [payload_for(m, self.case) for m in MODELS[:2]]
        a.pop("model"); b.pop("model")
        self.assertEqual(a, b)
        self.assertNotIn('"reference"', a["messages"][1]["content"])
        self.assertNotIn('"required_fact_ids"', a["messages"][1]["content"])


class DataTests(unittest.TestCase):
    def test_fallback_is_explicit_and_covers_twenty_cases(self):
        with workspace_trial() as folder:
            bundle = load_bundle(Path(folder))
            self.assertEqual(bundle["kind"], "synthetic")
            self.assertTrue(bundle["warnings"])
            self.assertGreaterEqual(len(build_cases(bundle)), 20)
            self.assertTrue(all(c["data_kind"] == "synthetic" for c in build_cases(bundle)))

    def test_corrupt_present_data_does_not_silently_fallback(self):
        from benchmark_llm.data import FILES
        import shutil
        if not all((ROOT / rel).is_file() for rel in FILES.values()):
            self.skipTest("Jeu réel incomplet : scénario de corruption non applicable.")
        with workspace_trial() as folder:
            root = Path(folder)
            for rel in FILES.values():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, root / rel)
            summary = pd.read_csv(root / FILES["summary"])
            summary.loc[summary.kpi_name.eq("request_volume"), "value"] = 9999
            summary.to_csv(root / FILES["summary"], index=False)
            with self.assertRaises(DataValidationError):
                load_bundle(root)


class ClientTests(unittest.TestCase):
    def mock_campaign(self, failure=None, truncate=False, wrong_model=False):
        bundle = load_bundle(ROOT)
        cases = build_cases(bundle)[:2]
        lookup = {c["case_id"]: c for c in cases}

        class FakeClient:
            def installed(self):
                return {m: {"name": m, "digest": "MOCK-NOT-A-REAL-MODEL"} for m in MODELS}

            def call(self, endpoint, payload=None, **kwargs):
                if endpoint != "chat":
                    return {"version": "TEST-ONLY"}
                if payload["messages"][0]["role"] == "user":
                    return {"done": True, "message": {"content": '{"ready":true}'}}
                if failure:
                    raise OllamaFailure(failure, "SIMULATED FAILURE FOR UNIT TEST")
                public = json.loads(payload["messages"][1]["content"])
                content = json.dumps(reference_response(lookup[public["case_id"]]))
                return {"model": "wrong-model" if wrong_model else payload["model"], "done": True,
                        "done_reason": "length" if truncate else "stop", "message": {"content": content}}

        with workspace_trial() as folder:
            with patch("benchmark_llm.runner.MIN_FREE_GIB", {m: 0 for m in MODELS}):
                runs, manifest = run_benchmark(ROOT, bundle, cases, FakeClient(), folder)
            scores, comparison = score_runs(cases, runs, folder)
        return runs, scores, comparison

    def test_success_path_with_mock_responses_only(self):
        runs, scores, comparison = self.mock_campaign()
        self.assertEqual(len(runs), 6)
        self.assertTrue(scores.objective_pass.all())
        self.assertTrue(comparison.objective_pass_mean.eq(1).all())
        self.assertTrue(comparison.availability_rate.eq(1).all())
        hashes = pd.DataFrame(runs).groupby("case_id").request_hash.nunique()
        self.assertTrue(hashes.eq(1).all())

    def test_timeout_stops_remaining_cases_of_each_model(self):
        runs, scores, comparison = self.mock_campaign(failure="timeout")
        self.assertEqual(sum(r["status"] == "timeout" for r in runs), 3)
        self.assertEqual(sum(r["status"] == "remaining_cases_skipped_after_timeout" for r in runs), 3)
        self.assertTrue(comparison.objective_pass_mean.isna().all())

    def test_truncated_json_is_not_a_successful_generation(self):
        runs, scores, comparison = self.mock_campaign(truncate=True)
        self.assertTrue(all(r["status"] == "truncated_response" for r in runs))
        self.assertTrue(scores.objective_pass.eq(False).all())
        self.assertTrue(comparison.objective_pass_mean.eq(0).all())
        self.assertTrue(comparison.availability_rate.eq(0).all())

    def test_unexpected_model_identity_rejected(self):
        runs, scores, comparison = self.mock_campaign(wrong_model=True)
        self.assertTrue(all(r["status"] == "unexpected_model_identity" for r in runs))
        self.assertTrue(comparison.objective_pass_mean.isna().all())

    def test_remote_server_rejected(self):
        with self.assertRaises(ValueError):
            OllamaClient("https://cloud.example.com")

    def test_connection_refusal_classification(self):
        client = OllamaClient()
        with patch.object(client.opener, "open", side_effect=error.URLError(ConnectionRefusedError())):
            with self.assertRaises(OllamaFailure) as caught:
                client.call("tags")
            self.assertEqual(caught.exception.status, "server_unavailable")

    def test_timeout_classification(self):
        client = OllamaClient()
        with patch.object(client.opener, "open", side_effect=TimeoutError("timed out")):
            with self.assertRaises(OllamaFailure) as caught:
                client.call("chat", {})
            self.assertEqual(caught.exception.status, "timeout")

    def test_unavailable_models_have_blank_scores_and_no_winner(self):
        class MissingClient:
            def call(self, *args, **kwargs):
                return {"version": "test"}

            def installed(self):
                return {}

        with workspace_trial() as folder:
            bundle = load_bundle(ROOT)
            cases = build_cases(bundle)[:2]
            runs, manifest = run_benchmark(ROOT, bundle, cases, MissingClient(), Path(folder))
            self.assertEqual(len(runs), 6)
            self.assertTrue(all(r["status"] == "model_absent" for r in runs))
            scores, comparison = score_runs(cases, runs, Path(folder))
            self.assertTrue(comparison.objective_pass_mean.isna().all())
            report = write_report(comparison, scores, manifest, Path(folder))
            self.assertIn("Aucun modèle sélectionné", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
