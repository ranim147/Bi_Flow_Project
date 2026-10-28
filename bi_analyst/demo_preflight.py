"""Reproducible local readiness check, with optional Ollama/data inspection."""
from __future__ import annotations
import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import sys

from benchmark_llm.runner import OllamaClient, OllamaFailure
from .demo_service import load_recording, load_scenarios, recordings_root, active_dataset


def preflight(root: Path, *, check_ollama=False, check_data=False):
    root = root.resolve()
    errors, recordings = [], []
    try:
        streamlit_version = version("streamlit")
    except PackageNotFoundError:
        streamlit_version = None
        errors.append("Streamlit absent : installer requirements.txt")
    try:
        for scenario in load_scenarios(root):
            run = load_recording(scenario["id"], root)
            recordings.append(dict(id=scenario["id"], execution_id=run.report.execution_id,
                                   source_mode=run.display_metadata["source_mode"], status=run.report.status,
                                   contract_verified=True))
    except (ValueError, OSError) as exc:
        errors.append("Restitution : " + str(exc))
    data_checked = None
    if check_data:
        data_checked = True
        try:
            verification = json.loads((recordings_root(root) / "compare_boroughs/verification.json").read_text(encoding="utf-8"))
            for relative, expected in verification["real_input_sha256"].items():
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError("Source modifiée : " + relative)
            selected = active_dataset(root)
            if selected:
                from .analytics import load_real_bundle
                load_real_bundle(root, selected)
        except (ValueError, OSError, KeyError) as exc:
            data_checked = False
            errors.append("Données : " + str(exc))
    ollama = None
    if check_ollama:
        try:
            client = OllamaClient(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
            models = client.installed()
            metadata = client.call("version", timeout=5)
            if "granite3.3:2b" not in models:
                raise ValueError("granite3.3:2b absent")
            ollama = dict(available=True, version=metadata.get("version"), model="granite3.3:2b",
                          digest=models["granite3.3:2b"].get("digest"), inference_performed=False)
        except (OllamaFailure, ValueError) as exc:
            ollama = dict(available=False, error=str(exc), inference_performed=False)
            errors.append("Ollama : " + str(exc))
    return dict(python=sys.version.split()[0], streamlit_version=streamlit_version,
                replay_ready=len(recordings) == 4 and streamlit_version is not None,
                recordings=recordings, data_hashes_verified=data_checked, ollama=ollama,
                live_readiness_checked=check_ollama and check_data,
                no_model_inference_performed=True, errors=errors, ready=not errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check-ollama", action="store_true")
    parser.add_argument("--check-data", action="store_true")
    args = parser.parse_args()
    result = preflight(args.root, check_ollama=args.check_ollama, check_data=args.check_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
