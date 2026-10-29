"""Dashboard BIFlow (Streamlit + Plotly) : affiche le dernier run PUBLIÉ.

    streamlit run src/biflow/dashboard/app.py
    streamlit run src/biflow/dashboard/app.py -- --dir <dossier des runs>
    (ou variable d'environnement BIFLOW_RUNS_DIR)

L'application n'affiche que la spec produite par l'agent Dashboard, après avoir vérifié
son empreinte ; chaque graphique montre la requête SQL dont il provient (traçabilité).
L'onglet « Poser une question » interroge l'instantané audité du run (empreinte vérifiée),
avec les garde-fous de l'agent KPI : périmètre, contrôle de la question, bac à sable, seuil.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from biflow.agents.kpi_semantic import ask
from biflow.common.config import PROCESSED_DIR
from biflow.common.reference import load_reference
from biflow.common.snapshot import file_hash
from biflow.llm.client import get_llm, llm_label
from biflow.orchestrator.state import DatasetRef

UNIT_LABELS = {"count": "", "hours": " h", "percent": " %", "ratio": "", "per_10k": " / 10 000 hab."}
SERIES = "#2a78d6"      # une seule série par graphique : une seule couleur
ANOMALY = "#d03b3b"     # statut « critique », toujours avec la légende « anomalie »
CROSS_CHECK = {
    "concordant": "contrôle croisé : la baseline à règles trouve le même résultat",
    "divergent": "contrôle croisé : la baseline à règles trouve un autre résultat — réponse à vérifier",
    "non vérifiable": "contrôle croisé impossible : question hors des règles de la baseline",
}
EXAMPLES = [
    "Combien de demandes de bruit dans le Bronx ?",
    "Quel borough a le délai médian de résolution le plus long ?",
    "Quel est le taux de respect du SLA à Brooklyn ?",
    "Quelle agence traite le plus de demandes ?",
    "Pourquoi les délais sont-ils plus longs à Staten Island ?",
]


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=os.getenv("BIFLOW_RUNS_DIR") or str(PROCESSED_DIR))
    return p.parse_known_args(sys.argv[1:])[0]


def _fmt(value: float | None, unit: str | None) -> str:
    if value is None:
        return "masqué"
    if unit == "ratio":
        return f"{100 * value:,.1f} %".replace(",", " ")
    decimals = 0 if unit == "count" else 1
    return f"{value:,.{decimals}f}".replace(",", " ") + UNIT_LABELS.get(unit or "", "")


def _trace(chart: dict, queries: dict[str, dict]) -> None:
    with st.expander("Origine des chiffres"):
        for qid in chart["query_ids"]:
            q = queries.get(qid)
            if q:
                st.caption(f"{qid} · instantané {q['snapshot_hash'][:12]} · {q['executed_at'][:19]}")
                st.code(q["sql"], language="sql")


def _figure(c: dict, df: pd.DataFrame):
    if c["chart_type"] == "line":
        fig = px.line(df, x="key", y="value", labels={"key": "jour", "value": "demandes"},
                      color_discrete_sequence=[SERIES])
        fig.update_traces(line={"width": 2}, name="demandes", showlegend=True)
        anomalies = df[df["anomaly"]]
        fig.add_scatter(x=anomalies["key"], y=anomalies["value"], mode="markers", name="anomalie",
                        marker={"color": ANOMALY, "size": 10, "line": {"width": 2, "color": "white"}})
    elif c["chart_type"] == "bar":
        fig = px.bar(df, x="key", y="value", labels={"key": "", "value": c["unit"]},
                     color_discrete_sequence=[SERIES])
    else:
        fig = px.bar(df.iloc[::-1], x="value", y="key", orientation="h",
                     labels={"key": "", "value": c["unit"]}, color_discrete_sequence=[SERIES])
    fig.update_layout(margin={"l": 8, "r": 8, "t": 8, "b": 8}, hovermode="x unified"
                      if c["chart_type"] == "line" else "closest", bargap=0.25)
    return fig


def dashboard_tab(spec: dict, run: dict, queries: dict[str, dict]) -> None:
    charts = spec["charts"]
    cards = [c for c in charts if c["chart_type"] == "kpi_card"]
    for col, c in zip(st.columns(len(cards)), cards, strict=True):
        col.metric(c["title"], _fmt(c["value"], c["unit"]))
        with col:
            _trace(c, queries)

    insights = next((c for c in charts if c["chart_type"] == "text"), None)
    if insights:
        st.subheader("Insights")
        for i in insights["data"]:
            st.markdown(f"**{i['title']}** — {i['text']}  \n<small>rédaction : {i['written_by']}</small>",
                        unsafe_allow_html=True)

    for c in charts:
        if c["chart_type"] not in ("line", "bar", "bar_h"):
            continue
        df = pd.DataFrame(c["data"])
        st.subheader(c["title"])
        if df.empty:
            st.info("Aucune donnée.")
            continue
        if df["masked"].any():
            st.caption(f"{int(df['masked'].sum())} valeur(s) masquée(s) : moins de "
                       f"{run['outputs']['kpi_semantic'].get('min_group_size', 10)} demandes.")
        st.plotly_chart(_figure(c, df), width="stretch")
        _trace(c, queries)

    table = next((c for c in charts if c["chart_type"] == "table"), None)
    if table:
        st.subheader(table["title"])
        for a in table["data"]:
            st.markdown(f"**{a['question']}** · traduction : {a['mode']}")
            if a.get("cross_check"):
                (st.warning if a["cross_check"] == "divergent" else st.caption)(CROSS_CHECK[a["cross_check"]])
            if a.get("refusal"):
                st.info(a["refusal"])
            elif a["error"]:
                st.warning(a["error"])
            else:
                st.dataframe(pd.DataFrame(a["rows"]), hide_index=True)
                st.code(a["sql"], language="sql")


def question_tab(run: dict) -> None:
    etl = run["outputs"]["quality_etl"]
    llm = get_llm()
    st.caption(f"Traduction question -> SQL : {llm_label(llm) or 'baseline à règles (aucun LLM configuré)'}. "
               "La requête s'exécute en lecture seule sur l'instantané audité du run ; "
               f"tout résultat sur moins de {run['outputs']['kpi_semantic'].get('min_group_size', 10)} "
               "demandes est masqué. Réponse exploratoire : elle n'est pas publiée dans le dashboard.")
    example = st.selectbox("Exemples", [""] + EXAMPLES, index=0)
    question = st.text_input("Question", value=example, placeholder="Combien de demandes de bruit à Brooklyn ?")
    if not st.button("Répondre", type="primary") or not question.strip():
        return
    with st.spinner("Traduction et exécution..."):
        try:
            answer, _ = ask(question, etl["snapshot_path"], etl["snapshot_hash"],
                            load_reference(DatasetRef(**run["dataset"])), llm=llm)
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
            return
    st.caption(f"mode : {answer.mode} · {answer.duration_s} s")
    if answer.cross_check:
        (st.warning if answer.cross_check == "divergent" else st.caption)(CROSS_CHECK[answer.cross_check])
    if answer.personal_data_found:
        st.warning(f"Données personnelles masquées avant envoi au LLM : {', '.join(answer.personal_data_found)} "
                   f"— question envoyée : « {answer.question_sent} »")
    if answer.refusal:
        st.info(answer.refusal)
        return
    if answer.error:
        st.warning(answer.error)
        return
    rows = pd.DataFrame(answer.rows)
    if len(rows) == 1 and "value" in rows:
        value = rows.iloc[0]["value"]
        label = next((str(rows.iloc[0][c]) for c in rows.columns if c not in ("value", "n", "masked")), "Résultat")
        st.metric(label, "masqué" if pd.isna(value) else f"{value:,.2f}".replace(",", " ").rstrip("0").rstrip("."))
    st.dataframe(rows, hide_index=True)
    st.code(answer.sql, language="sql")


def audit_tab(run: dict, audit: dict) -> None:
    profile = run["outputs"]["data_profiler"]
    etl = run["outputs"]["quality_etl"]
    source = profile.get("source") or {}
    st.subheader("Provenance des données")
    st.markdown(f"Fichier : `{run['dataset'].get('file_path') or run['dataset']['table']}` · "
                f"{profile['row_count']:,} lignes · du {str(profile.get('date_min'))[:10]} "
                f"au {str(profile.get('date_max'))[:10]}".replace(",", " "))
    if source.get("source"):
        st.markdown(f"Source : {source['source']}")
    for note in source.get("notes", []):
        st.warning(note)
    if profile.get("absent_columns"):
        st.caption(f"Colonnes absentes de l'extrait (laissées vides) : {', '.join(profile['absent_columns'])}")

    st.subheader("Défauts détectés (Data Profiler)")
    st.dataframe(pd.DataFrame(profile["issues"]).drop(columns=["sample_keys"], errors="ignore"), hide_index=True)
    st.subheader("Règles de nettoyage (Qualité / ETL)")
    st.dataframe(pd.DataFrame(etl["rule_counts"].items(), columns=["règle", "lignes"]), hide_index=True)
    st.caption(f"{etl.get('quarantine_rows', 0)} ligne(s) supprimée(s) gardée(s) en quarantaine avec leur motif "
               f"({etl.get('quarantine_path') or 'aucune'}).")

    anomalies = run["outputs"]["analyste_bi"].get("anomalies", [])
    if anomalies:
        st.subheader(f"Anomalies détectées ({run['outputs']['analyste_bi'].get('detection_method')})")
        st.dataframe(pd.DataFrame(anomalies), hide_index=True)

    st.subheader("Audit")
    st.text(audit["explanation"])
    with st.expander(f"Contrôles ({sum(c['passed'] for c in audit['checks'])}/{len(audit['checks'])})"):
        st.dataframe(pd.DataFrame(audit["checks"]), hide_index=True)


def main() -> None:
    st.set_page_config(page_title="BIFlow — NYC 311", layout="wide")
    pointer_path = Path(_args().dir) / "published.json"
    if not pointer_path.exists():
        st.warning(f"Aucun run publié dans {pointer_path.parent}. Lancer `python -m biflow.run`.")
        return
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if file_hash(pointer["spec_path"]) != pointer["spec_hash"]:
        st.error("La spec du dashboard a été modifiée après l'audit : affichage refusé.")
        return
    spec = json.loads(Path(pointer["spec_path"]).read_text(encoding="utf-8"))
    run = json.loads(Path(pointer["run_file"]).read_text(encoding="utf-8"))
    queries = {q["id"]: q for q in run["outputs"]["kpi_semantic"].get("queries", [])}
    audit = run["outputs"]["auditor_xai"]

    st.title(spec["title"])
    st.caption(f"Run {spec['run_id']} · instantané {spec['snapshot_hash'][:12]} · publié le "
               f"{pointer['published_at'][:16].replace('T', ' ')} UTC · statut {run['status']}")
    if (run["outputs"]["data_profiler"].get("source") or {}).get("notes"):
        st.info("Données échantillonnées : voir l'onglet « Audit et provenance » avant d'interpréter les volumes.")

    tab_dash, tab_ask, tab_audit = st.tabs(["Tableau de bord", "Poser une question", "Audit et provenance"])
    with tab_dash:
        dashboard_tab(spec, run, queries)
    with tab_ask:
        question_tab(run)
    with tab_audit:
        audit_tab(run, audit)


main()
