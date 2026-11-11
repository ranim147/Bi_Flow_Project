"""Tests des agents réels sur le jeu synthétique (défauts et anomalie connus)."""

import json
from pathlib import Path

import duckdb
import httpx
import numpy as np
import pandas as pd
import pytest

from biflow.agents import default_agents
from biflow.agents.analyste_bi import AnalysteBIAgent
from biflow.agents.kpi_semantic import KPISemanticAgent, mask_rows
from biflow.analysis.anomalies import detect_anomalies
from biflow.analysis.numbers import extract_numbers, unsupported_numbers
from biflow.evaluation.synthetic import expected_kpis
from biflow.llm.client import ChatClient
from biflow.llm.privacy import MASK, redact
from biflow.orchestrator.state import RunStatus
from biflow.semantic.sandbox import SQLSandbox, UnsafeQueryError
from biflow.semantic.text_to_sql import baseline_to_sql, extract_sql


class FakeLLM:
    model = "fake"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self.answers.pop(0)


@pytest.fixture(scope="module")
def state(synthetic_dataset, tmp_path_factory):
    from biflow.agents.base import RunContext
    from biflow.orchestrator.graph import run_pipeline

    ctx = RunContext(max_retries=0, retry_delay_seconds=0, sleep=lambda _: None)
    agents = default_agents()
    agents["kpi_semantic"] = KPISemanticAgent(llm=None)
    agents["analyste_bi"] = AnalysteBIAgent(llm=None)
    return run_pipeline(synthetic_dataset, agents=agents, ctx=ctx,
                        questions=["Combien de demandes de bruit à Brooklyn ?", "Question sans rapport"],
                        output_dir=tmp_path_factory.mktemp("runs"))


# --------------------------------------------------------------------------- 1. profilage
def test_profiler_finds_every_injected_defect(state, synthetic):
    found = {(i["column"], i["issue_type"]): i["count"] for i in state.outputs["data_profiler"]["issues"]}
    for d in synthetic.defects:
        assert found.get((d.column, d.issue_type)) == d.count, d


# --------------------------------------------------------------------------- 2. qualité
def test_quality_cleans_and_minimises(state, synthetic):
    etl = state.outputs["quality_etl"]
    assert etl["rows_out"] == len(synthetic.truth)
    assert etl["duplicates"] == 15 and etl["invalid_dates"] == 12
    clean = pd.read_parquet(etl["snapshot_path"])
    assert "resolution_description" not in clean
    assert clean["unique_key"].is_unique
    assert (clean["latitude"].dropna().round(2) == clean["latitude"].dropna()).all()
    assert set(clean["borough"]) <= {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "UNSPECIFIED"}


# --------------------------------------------------------------------------- 3. KPI
def test_kpis_equal_manual_calculation(state, synthetic):
    kpis = {k["name"]: k["value"] for k in state.outputs["kpi_semantic"]["kpis"]}
    for name, value in expected_kpis(synthetic.truth).items():
        assert kpis[name] == pytest.approx(value, rel=1e-9), name


def test_every_kpi_has_a_recorded_query(state):
    out = state.outputs["kpi_semantic"]
    queries = {q["id"]: q for q in out["queries"]}
    for k in out["kpis"]:
        assert queries[k["query_id"]]["rows"][0]["value"] == pytest.approx(k["value"])
        assert queries[k["query_id"]]["snapshot_hash"] == state.outputs["quality_etl"]["snapshot_hash"]


def test_small_groups_are_masked():
    rows = mask_rows([{"borough": "A", "value": 50, "n": 50}, {"borough": "B", "value": 3.5, "n": 4}], 10)
    assert rows[0]["value"] == 50
    assert rows[1] == {"borough": "B", "value": None, "n": None, "masked": True}


def test_questions_answered_or_reported(state):
    ok, unknown = state.outputs["kpi_semantic"]["answers"]
    assert ok["mode"] == "baseline" and ok["rows"][0]["value"] > 0
    assert unknown["mode"] == "none" and unknown["error"]


# --------------------------------------------------------------------------- 4. analyse
def test_injected_anomaly_is_detected(state, synthetic):
    detected = {(a["zone"], a["day"]) for a in state.outputs["analyste_bi"]["anomalies"]}
    for a in synthetic.anomalies:
        assert (a["zone"], a["day"]) in detected


def test_stl_flags_spike_and_nothing_else():
    rng = np.random.default_rng(0)
    days = pd.date_range("2026-01-01", periods=56, freq="D")
    series = pd.Series(rng.poisson(100, size=56).astype(float), index=days)
    series.iloc[30] += 150
    anomalies, method = detect_anomalies(series)
    assert method == "stl"
    assert [a.day for a in anomalies] == [days[30].date().isoformat()]


def test_short_series_skips_detection():
    series = pd.Series([10.0, 12, 11], index=pd.date_range("2026-01-01", periods=3, freq="D"))
    assert detect_anomalies(series) == ([], "insuffisant")


def test_insight_numbers_are_backed_by_evidence(state):
    for insight in state.outputs["analyste_bi"]["insights"]:
        assert unsupported_numbers(insight["text"], insight["evidence"]) == [], insight["text"]


def test_number_extraction_handles_french_format():
    assert extract_numbers("Le 2026-02-14 : 1 234 demandes, 12,5 % et -3.2") == [1234, 12.5, -3.2]
    assert unsupported_numbers("85,7 % et 4,4 h", {"a": 85.747, "b": 4.39}) == []
    assert unsupported_numbers("86 % et 4,4 h", {"a": 85.2, "b": 4.39}) == [86]
    assert unsupported_numbers("92 % clôturées", {"closure_rate": 0.921}) == []


# --------------------------------------------------------------------------- 5. dashboard / audit
def test_dashboard_spec_is_traced(state):
    dash = state.outputs["dashboard_generator"]
    spec = json.loads(Path(dash["spec_path"]).read_text(encoding="utf-8"))
    assert spec["snapshot_hash"] == state.outputs["quality_etl"]["snapshot_hash"]
    numeric = [c for c in dash["charts"] if c["chart_type"] in ("kpi_card", "bar", "bar_h", "line")]
    assert numeric and all(c["query_ids"] for c in numeric)


def test_auditor_approves_clean_run(state):
    audit = state.outputs["auditor_xai"]
    assert audit["approved"] and all(c["passed"] for c in audit["checks"])
    assert "Aucun écart" in audit["explanation"]


class _ShiftedKPI(KPISemanticAgent):
    def run(self, state):
        out = super().run(state)
        out.kpis[1].value = min(out.kpis[1].value + 0.01, 1)
        return out


def test_auditor_catches_kpi_not_matching_its_query(pipeline):
    agents = default_agents()
    agents["kpi_semantic"] = _ShiftedKPI(llm=None)
    state = pipeline(agents=agents)
    assert state.status == RunStatus.UNRELIABLE and not state.published
    assert {f.target_step for f in state.findings if f.severity == "error"} == {"kpi_semantic"}


def test_replay_ignores_row_order_but_not_values():
    from biflow.agents.auditor_xai import same_rows

    a = [{"k": "A", "value": 3}, {"k": "B", "value": 3}]
    assert same_rows(a, a[::-1])
    assert not same_rows(a, [{"k": "A", "value": 3}, {"k": "B", "value": 4}])


def test_catalog_orderings_are_deterministic():
    from biflow.semantic.catalog import load_catalog

    for b in load_catalog().breakdowns:
        order = b.sql.rsplit("ORDER BY", 1)[1]
        assert b.dimension in order, f"{b.name} : ORDER BY sans départage par {b.dimension}"


# --------------------------------------------------------------------------- SQL sûr
@pytest.fixture
def sandbox():
    box = SQLSandbox({"requests": pd.DataFrame({"borough": ["BRONX", "QUEENS"], "n": [1, 2]})},
                     max_rows=5)
    yield box
    box.close()


@pytest.mark.parametrize("sql", [
    "DROP TABLE requests",
    "SELECT 1; SELECT 2",
    "SELECT * FROM secrets",
    "SELECT * FROM read_csv('C:/Windows/win.ini')",
    "COPY requests TO 'x.csv'",
    "INSERT INTO requests VALUES ('X', 3)",
])
def test_sandbox_refuses_unsafe_sql(sandbox, sql):
    with pytest.raises(UnsafeQueryError):
        sandbox.query(sql)
    assert sandbox.scalar("SELECT COUNT(*) FROM requests") == 2


def test_sandbox_limits_rows(sandbox):
    with pytest.raises(UnsafeQueryError, match="lignes"):
        sandbox.query("SELECT * FROM range(100)")


def test_sandbox_cannot_reenable_file_access(sandbox):
    with pytest.raises(duckdb.Error):
        sandbox.conn.execute("SET enable_external_access = true")


def test_baseline_sql_rules():
    assert baseline_to_sql("Quel est le taux de clôture à Staten Island ?") == (
        "SELECT AVG(CASE WHEN is_closed THEN 1.0 ELSE 0.0 END) AS value, COUNT(*) AS n FROM requests "
        "WHERE borough IN ('STATEN ISLAND')")
    assert "LIMIT 1" in baseline_to_sql("Quel borough a le plus de demandes ?")
    assert baseline_to_sql("Bonjour") is None


# --------------------------------------------------------------------------- LLM
def test_llm_sql_is_used_and_question_redacted(state, synthetic_dataset):
    llm = FakeLLM("```sql\nSELECT COUNT(*) AS value, COUNT(*) AS n FROM requests WHERE borough = 'BRONX'\n```")
    agent = KPISemanticAgent(llm=llm)
    s = state.model_copy(update={"questions": ["Demandes au 12 Main Street dans le Bronx ?"]})
    answer = agent.run(s).answers[0]
    assert answer.mode == "llm" and answer.rows[0]["value"] > 0
    assert answer.personal_data_found == ["adresse"]
    assert "Main Street" not in llm.prompts[0] and MASK in llm.prompts[0]


def test_unsafe_llm_sql_falls_back_to_baseline(state):
    llm = FakeLLM("DROP TABLE requests", "DELETE FROM requests")
    s = state.model_copy(update={"questions": ["Combien de demandes à Brooklyn ?"]})
    answer = KPISemanticAgent(llm=llm).run(s).answers[0]
    assert answer.mode == "baseline" and answer.rows[0]["value"] > 0


def test_llm_sql_without_support_count_is_sent_back(state):
    llm = FakeLLM("SELECT COUNT(*) AS value FROM requests WHERE borough = 'BRONX'",
                  "SELECT COUNT(*) AS value, COUNT(*) AS n FROM requests WHERE borough = 'BRONX'")
    s = state.model_copy(update={"questions": ["Combien de demandes dans le Bronx ?"]})
    out = KPISemanticAgent(llm=llm).run(s)
    assert out.answers[0].mode == "llm" and "n" in out.answers[0].rows[0]
    assert "colonne n" in llm.prompts[1]
    assert len([q for q in out.queries if q.id == "question:1"]) == 1  # requête refusée non journalisée


def test_llm_insight_with_invented_number_is_rejected(state):
    n = len(state.outputs["analyste_bi"]["insights"])
    texts = ["Texte reformulé sans chiffre."] + ["Il y a eu 999 999 demandes."] * (n - 1)
    out = AnalysteBIAgent(llm=FakeLLM(json.dumps(texts))).run(state)
    assert out.insights[0].written_by == "llm"
    assert all(i.written_by == "template" for i in out.insights[1:])


def test_prompts_never_contain_raw_rows(state):
    llm = FakeLLM(json.dumps(["x"] * len(state.outputs["analyste_bi"]["insights"])))
    AnalysteBIAgent(llm=llm).run(state)
    keys = pd.read_parquet(state.outputs["quality_etl"]["snapshot_path"])["unique_key"].astype(str)
    assert not any(k in llm.prompts[0] for k in keys.head(200))


def test_redact_personal_data():
    text, found = redact("Plainte de jean@exemple.fr au 221 Baker Street, tel 06 12 34 56 78")
    assert set(found) == {"email", "adresse", "téléphone"}
    assert "Baker" not in text and "jean@" not in text


def test_chat_client_retries_then_parses(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "```sql\nSELECT 1\n```"}}]})

    client = ChatClient("mistral-small-latest", "https://x", "k",
                        client=httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr("biflow.llm.client.time.sleep", lambda _: None)
    assert extract_sql(client.complete("sys", "user")) == "SELECT 1"
    assert len(calls) == 2 and calls[0]["temperature"] == 0
