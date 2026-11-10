"""API du front React : les scénarios lancent le vrai pipeline et finissent avec le bon statut."""

import dataclasses
import shutil
import time

import pytest
from fastapi.testclient import TestClient

import biflow.api.app as api_app
import biflow.api.live as live_mod
from biflow.api.live import RunManager
from biflow.common.config import get_settings


@pytest.fixture
def client(synthetic_dataset, tmp_path, monkeypatch):
    """API isolée : extrait synthétique dans un faux projet, runs dans un dossier temporaire."""
    raw, runs = tmp_path / "data" / "raw", tmp_path / "data" / "processed"
    raw.mkdir(parents=True)
    runs.mkdir(parents=True)
    shutil.copy(synthetic_dataset.file_path, raw / "synthetique.csv")
    monkeypatch.setattr(api_app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(api_app, "RAW_DIR", raw)
    monkeypatch.setattr(api_app, "PROCESSED_DIR", runs)
    monkeypatch.setattr(api_app, "manager", RunManager(runs))
    monkeypatch.setattr(api_app, "get_llm", lambda: None)
    fast = dataclasses.replace(get_settings(), retry_delay_seconds=0)
    monkeypatch.setattr(live_mod, "get_settings", lambda: fast)
    return TestClient(api_app.app)


def _run(client, **body) -> dict:
    r = client.post("/api/runs", json={"pace_s": 0, **body})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    for _ in range(600):
        snap = client.get(f"/api/runs/{run_id}/live").json()
        if snap["ended_at"]:
            return snap
        time.sleep(0.1)
    raise AssertionError("le run ne se termine pas")


def kinds(snap: dict) -> list[tuple]:
    return [(e["kind"], e.get("step")) for e in snap["events"]]


def test_meta_lists_scenarios_and_dataset(client):
    meta = client.get("/api/meta").json()
    assert {s["id"] for s in meta["scenarios"]} == {"nominal", "panne", "correction", "non_fiable"}
    assert [d["name"] for d in meta["datasets"]] == ["synthetique.csv"]


def test_nominal_run_is_published_and_served(client):
    snap = _run(client, scenario="nominal", questions=["Combien de demandes à Brooklyn ?"])
    assert snap["status"] == "success" and snap["published"]
    assert [k for k in kinds(snap) if k[0] == "success"] == [("success", s) for s in live_mod.STEP_ORDER]
    pub = client.get("/api/published").json()
    assert pub["pointer"]["run_id"] == snap["run_id"]
    assert any(c["chart_type"] == "kpi_card" for c in pub["spec"]["charts"])
    runs = client.get("/api/runs").json()
    assert runs[0]["run_id"] == snap["run_id"] and runs[0]["is_current"]


def test_failure_scenario_retries_then_stops(client):
    snap = _run(client, scenario="panne", step="quality_etl")
    assert snap["status"] == "failed" and not snap["published"]
    assert kinds(snap).count(("started", "quality_etl")) == 3
    assert ("started", "kpi_semantic") not in kinds(snap)
    assert client.get("/api/published").status_code == 404


def test_audit_feedback_loop(client):
    fixed = _run(client, scenario="correction", step="kpi_semantic")
    audits = [e for e in fixed["events"] if e["kind"] == "audit"]
    assert [a["approved"] for a in audits] == [False, True]
    assert fixed["status"] == "success"

    stuck = _run(client, scenario="non_fiable", step="kpi_semantic")
    assert stuck["status"] == "unreliable" and not stuck["published"]
    # le tableau de bord reste sur le dernier run validé
    assert client.get("/api/published").json()["pointer"]["run_id"] == fixed["run_id"]


def test_ask_refuses_out_of_scope_and_answers(client):
    _run(client, scenario="nominal")
    refused = client.post("/api/ask", json={"question": "Combien d'agents faut-il recruter ?"}).json()
    assert refused["answer"]["mode"] == "refused"
    ok = client.post("/api/ask", json={"question": "Combien de demandes à Brooklyn ?"}).json()
    assert ok["answer"]["mode"] == "baseline" and ok["answer"]["rows"]


def test_invalid_requests(client):
    assert client.post("/api/runs", json={"scenario": "inconnu"}).status_code == 400
    assert client.post("/api/runs", json={"scenario": "panne", "step": "finalize"}).status_code == 400
    assert client.post("/api/runs", json={"dataset": "../../secret.parquet"}).status_code == 400
    assert client.get("/api/runs/pas-un-run").status_code == 400
    assert client.get("/api/runs/pas-un-run/quarantine").status_code == 400
    assert client.get("/api/inconnue").status_code == 404
