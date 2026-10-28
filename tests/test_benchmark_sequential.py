"""Déchargement, ordre et persistance ; aucune performance simulée publiée."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from benchmark_llm.data import build_cases, load_bundle
from benchmark_llm.protocol import MODELS
from benchmark_llm.runner import OllamaFailure, _json_write, release_ollama_memory, run_benchmark
from test_benchmark_llm import ROOT, reference_response, workspace_trial


class StatefulClient:
    def __init__(self, cases, folder, *, interrupt=None):
        self.lookup = {c["case_id"]: c for c in cases}
        self.folder = folder
        self.resident = ["llama3.2:3b"]
        self.events = []
        self.interrupt = interrupt
        self.measured = 0

    def installed(self):
        return {m: {"name": m, "digest": "TEST-ONLY"} for m in MODELS}

    def call(self, endpoint, payload=None, **kwargs):
        if endpoint == "ps":
            return {"models": [{"name": m} for m in self.resident]}
        if endpoint == "generate":
            assert payload["keep_alive"] == 0
            self.events.append(("unload", payload["model"]))
            if payload["model"] in self.resident:
                self.resident.remove(payload["model"])
            return {"done": True}
        if endpoint != "chat":
            return {"version": "TEST-ONLY"}
        model = payload["model"]
        if payload["messages"][0]["role"] == "user":
            assert not self.resident, "A second model would be loaded concurrently"
            index = MODELS.index(model)
            if index:
                previous = self.folder / "models" / MODELS[index-1].replace(":", "_")
                assert (previous / "scores_detailed.csv").is_file(), "Previous scores not saved"
                assert (previous / "responses.jsonl").read_text(encoding="utf-8").strip(), "Previous responses not saved"
            self.resident.append(model)
            self.events.append(("load", model))
            if self.interrupt == "warmup":
                raise KeyboardInterrupt()
            return {"done": True, "message": {"content": '{"ready":true}'}}
        assert self.resident == [model]
        self.measured += 1
        if self.interrupt == self.measured:
            raise KeyboardInterrupt()
        public = json.loads(payload["messages"][1]["content"])
        self.events.append(("case", model))
        return {"model": model, "done": True, "message": {
            "content": json.dumps(reference_response(self.lookup[public["case_id"]]))}}


class SequentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(ROOT)
        cls.cases = build_cases(cls.bundle)[:2]

    def test_low_estimated_ram_does_not_skip_models_in_attempt_mode(self):
        with workspace_trial() as folder:
            client = StatefulClient(self.cases, folder)
            with patch("benchmark_llm.runner.MIN_FREE_GIB", {m: 1000 for m in MODELS}):
                runs, manifest = run_benchmark(ROOT, self.bundle, self.cases, client, folder)
            self.assertTrue(all(r["status"] == "success" for r in runs))
            self.assertEqual([m for action, m in client.events if action == "load"], list(MODELS))
            self.assertEqual(client.events[0], ("unload", "llama3.2:3b"))
            self.assertFalse(client.resident)
            self.assertEqual(len(manifest["memory_events"]), 6)
            for model in MODELS:
                saved = folder / "models" / model.replace(":", "_")
                self.assertEqual(len((saved / "responses.jsonl").read_text(encoding="utf-8").splitlines()), 2)
                self.assertTrue((saved / "model_comparison.csv").is_file())

    def test_strict_memory_policy_remains_optional(self):
        with workspace_trial() as folder:
            client = StatefulClient(self.cases, folder)
            with patch("benchmark_llm.runner.MIN_FREE_GIB", {m: 1000 for m in MODELS}):
                runs, manifest = run_benchmark(ROOT, self.bundle, self.cases, client, folder, memory_policy="strict")
            self.assertTrue(all(r["status"] == "warmup_insufficient_memory_preflight" for r in runs))
            self.assertFalse(any(action == "load" for action, _ in client.events))

    def test_interruption_keeps_previous_and_partial_model_results(self):
        with workspace_trial() as folder:
            client = StatefulClient(self.cases, folder, interrupt=4)
            with self.assertRaises(KeyboardInterrupt):
                run_benchmark(ROOT, self.bundle, self.cases, client, folder)
            self.assertFalse(client.resident)
            self.assertEqual(len((folder / "responses.jsonl").read_text(encoding="utf-8").splitlines()), 3)
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["campaign_status"], "interrupted")
            self.assertEqual(manifest["completed_models"], [MODELS[0]])
            self.assertTrue((folder / "models/gemma3_4b/scores_detailed.csv").is_file())

    def test_interrupt_during_warmup_also_unloads(self):
        with workspace_trial() as folder:
            client = StatefulClient(self.cases, folder, interrupt="warmup")
            with self.assertRaises(KeyboardInterrupt):
                run_benchmark(ROOT, self.bundle, self.cases, client, folder)
            self.assertFalse(client.resident)
            self.assertEqual(json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["campaign_status"], "interrupted")

    def test_existing_journal_is_never_overwritten(self):
        with workspace_trial() as folder:
            journal = folder / "responses.jsonl"
            journal.write_text("existing-results\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                run_benchmark(ROOT, self.bundle, self.cases, StatefulClient(self.cases, folder), folder)
            self.assertEqual(journal.read_text(encoding="utf-8"), "existing-results\n")

    def test_manifest_write_retries_transient_windows_file_lock(self):
        original = Path.replace
        attempts = []

        def locked_once(path, target):
            attempts.append(target)
            if len(attempts) == 1:
                error = PermissionError("Transient sharing violation")
                error.winerror = 32
                raise error
            return original(path, target)

        with workspace_trial() as folder:
            with patch.object(Path, "replace", locked_once), patch("benchmark_llm.runner.time.sleep"):
                _json_write(folder / "manifest.json", {"saved": True})
            self.assertEqual(json.loads((folder / "manifest.json").read_text(encoding="utf-8")), {"saved": True})
            self.assertEqual(len(attempts), 2)

    def test_stuck_resident_model_prevents_next_load(self):
        class StuckClient:
            def call(self, endpoint, *args, **kwargs):
                return {"models": [{"name": "stuck"}]} if endpoint == "ps" else {}

        with self.assertRaises(OllamaFailure) as error:
            release_ollama_memory(StuckClient(), timeout=0)
        self.assertEqual(error.exception.status, "unload_failed")

    def test_unload_also_targets_model_missing_from_running_list(self):
        calls = []

        class LoadingClient:
            def call(self, endpoint, payload=None, **kwargs):
                calls.append((endpoint, payload))
                return {"models": []} if endpoint == "ps" else {"done": True}

        release_ollama_memory(LoadingClient(), target_model="gemma3:4b")
        unloads = [p for endpoint, p in calls if endpoint == "generate"]
        self.assertEqual(unloads[0]["model"], "gemma3:4b")
        self.assertEqual(unloads[0]["keep_alive"], 0)


if __name__ == "__main__":
    unittest.main()
