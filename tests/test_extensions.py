"""Tests des ajouts pour les données réelles : périmètre des questions, colonnes facultatives,
quarantaine, tendance longue, explication d'anomalie, import de pages, LLM local, `ask()`."""

import json

import numpy as np
import pandas as pd
import pytest

from biflow.agents.kpi_semantic import KPISemanticAgent, ask
from biflow.analysis.anomalies import top_contributor, trend_pct, trend_window
from biflow.common.config import get_settings
from biflow.common.data import metadata_path
from biflow.common.reference import BOROUGH_SEED, SLA_SEED
from biflow.common.schema import OPTIONAL_COLUMNS
from biflow.evaluation.__main__ import PARAPHRASES, QUESTIONS
from biflow.ingestion.import_pages import import_pages
from biflow.llm import client as llm_client
from biflow.orchestrator.state import DatasetRef, RunStatus
from biflow.semantic.catalog import load_catalog
from biflow.semantic.sandbox import SQLSandbox, UnsafeQueryError
from biflow.semantic.scope import out_of_scope
from biflow.semantic.text_to_sql import build_prompt, dimension_values


class FakeLLM:
    model = "fake"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self.answers.pop(0)


# --------------------------------------------------------------------------- périmètre
@pytest.mark.parametrize("question", [
    "Pourquoi les délais augmentent-ils à Brooklyn ?",
    "Quelle est la cause de la hausse du bruit ?",
    "Combien d'agents faut-il recruter ?",
    "Faut-il embaucher du personnel au DOT ?",
    "Combien de demandes recevra-t-on le mois prochain ?",
])
def test_out_of_scope_questions_are_refused(question):
    assert out_of_scope(question) is not None


def test_evaluation_questions_are_in_scope():
    assert [q for q, _ in QUESTIONS + PARAPHRASES if out_of_scope(q)] == []


def test_refused_question_never_reaches_the_llm(pipeline):
    llm = FakeLLM()
    agents = __import__("biflow.agents", fromlist=["default_agents"]).default_agents()
    agents["kpi_semantic"] = KPISemanticAgent(llm=llm)
    state = pipeline(agents=agents, questions=["Pourquoi le Bronx est-il plus lent ?"])
    answer = state.outputs["kpi_semantic"]["answers"][0]
    assert answer["mode"] == "refused" and "cause" in answer["refusal"]
    assert llm.prompts == [] and answer["query_id"] is None
    assert state.status == RunStatus.SUCCESS


# --------------------------------------------------------------------------- données réelles
def test_extract_without_optional_columns(synthetic, tmp_path, pipeline):
    path = tmp_path / "projection.csv"
    synthetic.raw.drop(columns=["incident_zip", "latitude", "longitude", "resolution_description"]).to_csv(
        path, index=False)
    state = pipeline(dataset=DatasetRef(file_path=str(path)))
    assert state.status == RunStatus.SUCCESS, state.findings
    absent = state.outputs["data_profiler"]["absent_columns"]
    assert set(absent) == {"incident_zip", "latitude", "longitude", "resolution_description"}
    assert set(absent) <= set(OPTIONAL_COLUMNS)


def test_extract_without_required_column_fails(synthetic, tmp_path, pipeline):
    path = tmp_path / "sans_borough.csv"
    synthetic.raw.drop(columns=["borough"]).to_csv(path, index=False)
    state = pipeline(dataset=DatasetRef(file_path=str(path)))
    assert state.status == RunStatus.FAILED


def test_dropped_rows_are_quarantined_with_reason(pipeline):
    etl = pipeline().outputs["quality_etl"]
    quarantine = pd.read_parquet(etl["quarantine_path"])
    assert len(quarantine) == etl["rows_dropped"] == etl["quarantine_rows"] > 0
    assert quarantine["motif"].str.startswith("suppression").all()
    assert "resolution_description" not in quarantine  # texte libre : jamais conservé


def test_import_pages_writes_extract_and_provenance(synthetic, tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    half = len(synthetic.raw) // 2
    synthetic.raw.iloc[:half].to_csv(pages / "p1.csv", index=False)
    synthetic.raw.iloc[half:].drop(columns=["latitude"]).to_parquet(pages / "p2.parquet", index=False)
    path = import_pages([str(pages)], "extrait", tmp_path / "raw", source="test", notes=["échantillon"])
    assert len(pd.read_parquet(path)) == len(synthetic.raw)
    meta = json.loads(metadata_path(path).read_text(encoding="utf-8"))
    assert meta["rows"] == len(synthetic.raw) and meta["notes"] == ["échantillon"] and meta["pages"] == 2


def test_source_notes_add_reserves_to_insights(synthetic, tmp_path, pipeline):
    path = tmp_path / "extrait.csv"
    synthetic.raw.to_csv(path, index=False)
    metadata_path(path).write_text(json.dumps({"notes": ["échantillon de 2 %"]}), encoding="utf-8")
    state = pipeline(dataset=DatasetRef(file_path=str(path)))
    assert state.outputs["data_profiler"]["source"]["notes"] == ["échantillon de 2 %"]
    assert "Réserve" in state.outputs["analyste_bi"]["insights"][0]["text"]


# --------------------------------------------------------------------------- analyse
def test_trend_window_adapts_to_length():
    assert trend_window(10) is None
    assert trend_window(14) == 7
    assert trend_window(60) == 28
    assert trend_window(800) == 365


def test_yearly_trend_compares_last_two_years():
    days = pd.date_range("2023-01-01", periods=1000, freq="D")
    series = pd.Series(np.where(np.arange(1000) >= 1000 - 365, 110.0, 100.0), index=days)
    assert trend_pct(series) == (10.0, 365)


def test_top_contributor_is_the_largest_excess():
    day = pd.Series({"Snow or Ice": 231, "Noise - Residential": 40})
    usual = pd.Series({"Snow or Ice": 20.5, "Noise - Residential": 38.0})
    assert top_contributor(day, usual) == ("Snow or Ice", 231, 20.5)
    assert top_contributor(pd.Series({"A": 1}), pd.Series({"A": 3.0})) is None


def test_anomaly_insight_names_the_main_type(pipeline, synthetic):
    insights = pipeline().outputs["analyste_bi"]["insights"]
    spike = next(i for i in insights if i["category"] == "anomalie")
    assert spike["evidence"]["type_en_hausse"] == "Noise - Residential"
    assert "pas une cause" in spike["text"]


# --------------------------------------------------------------------------- SQL et LLM
def test_catalog_queries_may_exceed_generated_sql_row_limit():
    sandbox = SQLSandbox({"requests": pd.DataFrame({"x": range(5)})}, max_rows=3)
    with pytest.raises(UnsafeQueryError):
        sandbox.query("SELECT * FROM requests")
    assert len(sandbox.query("SELECT * FROM requests", max_rows=10)) == 5


def test_llm_prompt_lists_dimension_values_above_threshold():
    df = pd.DataFrame({"borough": ["BRONX"] * 12 + ["QUEENS"] * 3, "agency": "NYPD",
                       "complaint_type": "Noise - Residential", "status": "Closed"})
    sandbox = SQLSandbox({"requests": df})
    values = dimension_values(sandbox.query, min_n=10)
    assert values["borough"] == ["BRONX"]  # QUEENS : moins de 10 demandes
    prompt = build_prompt("Combien ?", load_catalog(), values)
    assert "borough : BRONX" in prompt and "QUEENS" not in prompt.split("Modalités")[1]


def test_get_llm_providers(monkeypatch):
    def settings(**kw):
        base = get_settings()
        return type(base)(**{**base.__dict__, **kw})

    monkeypatch.setattr(llm_client, "get_settings", lambda: settings(llm_provider="none"))
    assert llm_client.get_llm() is None
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings(llm_provider="auto", mistral_api_key=None))
    assert llm_client.get_llm() is None
    monkeypatch.setattr(llm_client, "get_settings",
                        lambda: settings(llm_provider="ollama", ollama_model="qwen2.5:7b"))
    llm = llm_client.get_llm()
    assert llm.label == "ollama:qwen2.5:7b" and "Authorization" not in llm.client.headers
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings(llm_provider="auto", mistral_api_key="k"))
    assert llm_client.get_llm().label.startswith("mistral:")


# --------------------------------------------------------------------------- ask() (dashboard)
def test_ask_uses_audited_snapshot(pipeline):
    etl = pipeline().outputs["quality_etl"]
    reference = {"boroughs": pd.DataFrame(BOROUGH_SEED), "sla_targets": pd.DataFrame(SLA_SEED)}
    answer, queries = ask("Combien de demandes à Brooklyn ?", etl["snapshot_path"], etl["snapshot_hash"],
                          reference, llm=None)
    assert answer.mode == "baseline" and answer.rows[0]["value"] > 0 and len(queries) == 1
    refused, _ = ask("Pourquoi Brooklyn ?", etl["snapshot_path"], etl["snapshot_hash"], reference, llm=None)
    assert refused.mode == "refused"


def test_ask_refuses_modified_snapshot(pipeline):
    etl = pipeline().outputs["quality_etl"]
    df = pd.read_parquet(etl["snapshot_path"])
    df.iloc[1:].to_parquet(etl["snapshot_path"], index=False)
    with pytest.raises(ValueError, match="modifié"):
        ask("Combien de demandes ?", etl["snapshot_path"], etl["snapshot_hash"], {}, llm=None)


# --------------------------------------------------------------------------- contrôle croisé
def test_same_answer_ignores_sql_and_column_names():
    from biflow.agents.kpi_semantic import same_answer

    assert same_answer([{"value": 12, "n": 12}], [{"total": 12.0, "n": 12}]) is False  # pas de clé « value »
    assert same_answer([{"borough": "BRONX", "value": 3.0, "n": 40}], [{"b": "BRONX", "value": 3.0, "n": 40}])
    assert same_answer([{"value": "BRONX", "n": 40}], [{"borough": "BRONX", "value": 40, "n": 40}]) is False
    assert not same_answer([{"value": 1.0}], [{"value": 1.1}])


def test_llm_answer_is_cross_checked_with_baseline(pipeline):
    from biflow.agents import default_agents

    right = "SELECT COUNT(*) AS value, COUNT(*) AS n FROM requests WHERE borough = 'BROOKLYN'"
    wrong = "SELECT COUNT(*) AS value, COUNT(*) AS n FROM requests"
    llm = FakeLLM(right, wrong, right)
    agents = default_agents()
    agents["kpi_semantic"] = KPISemanticAgent(llm=llm)
    state = pipeline(agents=agents, questions=["Combien de demandes à Brooklyn ?",
                                               "Combien de demandes à Brooklyn ?",
                                               "Quelle agence traite le plus de demandes ?"])
    checks = [a["cross_check"] for a in state.outputs["kpi_semantic"]["answers"]]
    assert checks == ["concordant", "divergent", "non vérifiable"]
    assert state.status == RunStatus.SUCCESS  # requêtes de contrôle rejouées par l'Auditeur
