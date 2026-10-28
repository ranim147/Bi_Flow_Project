from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from bi_analyst.analytics import FILES
from bi_analyst.demo_service import load_recording, load_scenarios, run_live, run_rejection_demo, recordings_root
from test_benchmark_llm import workspace_trial

ROOT = Path(__file__).resolve().parents[1]


def copy_package(destination, with_data=False):
    shutil.copytree(recordings_root(ROOT), destination / "demo/recordings")
    shutil.copytree(ROOT / "schemas", destination / "schemas")
    shutil.copyfile(ROOT / "demo/questions.json", destination / "demo/questions.json")
    if with_data:
        for relative in FILES.values():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)


class DemoServiceTests(unittest.TestCase):
    def test_all_four_recordings_are_verified_without_ollama(self):
        with patch("benchmark_llm.runner.OllamaClient.call", side_effect=AssertionError("Replay must be offline")):
            scenarios = load_scenarios(ROOT)
            self.assertEqual(len(scenarios), 4)
            for scenario in scenarios:
                with self.subTest(scenario=scenario["id"]):
                    run = load_recording(scenario["id"], ROOT)
                    self.assertEqual(run.mode, "replay")
                    self.assertFalse(run.display_metadata["llm_called_now"])
                    self.assertTrue(run.verification["integrity_verified"])
                    self.assertTrue(run.verification["schema_valid"])
                    self.assertTrue(run.verification["source_verification"]["passed"])

    def test_insufficiency_and_injection_are_not_relabelled_successes(self):
        insufficient = load_recording("insufficient_history", ROOT)
        self.assertEqual((insufficient.report.status, insufficient.report.decision), ("validated", "insufficient_data"))
        injected = load_recording("controlled_rejection", ROOT)
        self.assertEqual(injected.report.status, "fallback")
        self.assertEqual(injected.display_metadata["source_mode"], "controlled_test")
        self.assertTrue(injected.display_metadata["excluded_from_model_metrics"])

    def test_changed_report_is_blocked_before_display(self):
        with workspace_trial() as root:
            copy_package(root)
            target = root / "demo/recordings/compare_boroughs/report.json"
            value = json.loads(target.read_text(encoding="utf-8"))
            value["evidence"][0]["value"] = 99999.0
            target.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Empreinte"):
                load_recording("compare_boroughs", root)

    def test_manifest_cannot_escape_recordings_directory(self):
        with workspace_trial() as root:
            copy_package(root)
            path = root / "demo/recordings/manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["recordings"][0]["sha256"] = {"../outside.json": "a" * 64}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dossier autorisé"):
                load_recording("compare_boroughs", root)

    def test_injection_marker_cannot_be_hidden_by_metadata(self):
        with workspace_trial() as root:
            copy_package(root)
            path = root / "demo/recordings/controlled_rejection/metadata.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value.update(source_mode="live", injected=False)
            path.write_text(json.dumps(value), encoding="utf-8")
            manifest_path = root / "demo/recordings/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = next(row for row in manifest["recordings"] if row["scenario_id"] == "controlled_rejection")
            entry["sha256"]["controlled_rejection/metadata.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contredit"):
                load_recording("controlled_rejection", root)

    def test_unknown_scenario_is_refused(self):
        with self.assertRaises(ValueError):
            load_recording("../../not-a-scenario", ROOT)

    def test_controlled_rejection_hits_both_validations_and_keeps_python_facts(self):
        with workspace_trial() as root:
            copy_package(root, with_data=True)
            with patch("benchmark_llm.runner.OllamaClient.call", side_effect=AssertionError("No real inference during injection")):
                run = run_rejection_demo(root)
            self.assertEqual(run.mode, "controlled_test")
            self.assertEqual(run.report.status, "fallback")
            self.assertEqual(run.report.validation.status, "rejected")
            self.assertEqual(len([e for e in run.events if e["event"] == "validation"]), 2)
            self.assertTrue(any("unverified_numeric_text" in e for e in run.report.errors))
            self.assertTrue(any("unknown_evidence_fact_ids" in e for e in run.report.errors))
            self.assertTrue(run.report.evidence)
            marker = next(e for e in run.events if e["event"] == "controlled_fault_injection")
            self.assertFalse(marker["actual_model_inference"])
            self.assertTrue(marker["excluded_from_model_metrics"])

    def test_live_result_provenance_comes_from_live_function_only(self):
        source = load_recording("compare_boroughs", ROOT)
        with workspace_trial() as root:
            copy_package(root)
            folder = root / "outputs/demo_live" / source.report.execution_id
            folder.mkdir(parents=True)
            shutil.copyfile(source.path / "audit.jsonl", folder / "audit.jsonl")
            with patch("bi_analyst.demo_service.analyze", return_value=source.report) as call:
                # The former fixture returned an unrelated archived question as live.
                # That substitution must now fail before display.
                with self.assertRaisesRegex(ValueError, "ne correspondent pas"):
                    run_live("Question utilisée par le test de routage", root)
                run = run_live(source.report.question, root)
            self.assertEqual(run.mode, "live")
            self.assertEqual(run.display_metadata["source_mode"], "live")
            self.assertEqual(call.call_args.args[0], source.report.question)
            self.assertEqual(call.call_args.kwargs["audit_root"], root / "outputs/demo_live")
