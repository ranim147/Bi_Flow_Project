"""Local academic presentation for BIFlow; no hosting or dashboard generation."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from bi_analyst import demo_service


MODE_LABELS = {
    "replay": "Restitution enregistrée",
    "latest": "Derniers résultats sur les nouvelles données",
    "live": "Exécution Granite en direct",
    "controlled_test": "Test contrôlé du validateur",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _origin_banner(run) -> None:
    metadata = run.display_metadata or {}
    controlled_source = (run.mode == "controlled_test" or metadata.get("source_mode") == "controlled_test"
                         or metadata.get("injected") is True)
    if run.mode == "replay":
        st.warning("RESTITUTION ENREGISTRÉE — aucune inférence LLM en direct. Les résultats et la durée affichés proviennent de l'exécution archivée.")
    elif run.mode == "live":
        if metadata.get("llm_called_now") is False:
            st.info("EXÉCUTION EN DIRECT — demande locale effectuée, mais aucun appel LLM n'a pu être lancé. Consulter le précontrôle et l'audit.")
        else:
            st.info("EXÉCUTION EN DIRECT — cette analyse vient d'appeler Granite3.3 2B via Ollama local.")
    elif run.mode == "controlled_test":
        st.warning("TEST CONTRÔLÉ — des réponses incorrectes sont injectées pour éprouver le validateur. Elles ne sont pas des réponses Granite authentiques.")
    else:
        st.error("Origine non reconnue : ne pas présenter ce résultat comme une exécution en direct.")
    if controlled_source and run.mode == "replay":
        st.warning("Cette archive restitue un test avec réponses injectées, pas une génération incorrecte réellement observée de Granite.")
    if run.report.data_kind == "synthetic":
        st.warning("DONNÉES SYNTHÉTIQUES DE TEST — ce résultat ne décrit pas les données NYC 311 réelles.")
    else:
        st.caption("Données : extrait NYC 311 réel préparé et validé par Python.")
    st.caption(f"Origine : {run.origin} · exécution {run.report.execution_id} · {run.report.generated_at}")
    if metadata.get("dataset_version"):
        st.caption("Version des données : " + metadata["dataset_version"])


def _render_summary(report) -> None:
    if not report.insights:
        st.info("Aucun insight n'est diffusé pour ce rapport.")
    for insight in report.insights:
        with st.container(border=True):
            st.markdown(f"**Constat — {insight.category}**")
            st.write(insight.insight)
            st.markdown("**Recommandation**")
            st.write(insight.recommendation)
            st.caption("Preuves : " + ", ".join(insight.evidence_fact_ids))
    st.subheader("Limites de l'analyse")
    if report.limitations:
        for limitation in report.limitations:
            st.write("• " + limitation)
    else:
        st.info("Aucune limite fournie : vérifier la couverture du rapport avant de l'utiliser.")


def _render_evidence(report, analysis) -> None:
    st.subheader("Outils analytiques sélectionnés")
    tools = [dict(outil=trace.tool, statut=trace.status, durée_ms=round(trace.elapsed_ms, 3),
                  preuves=len(trace.evidence_ids), détail=trace.detail) for trace in report.tool_trace]
    if tools:
        st.dataframe(tools, hide_index=True, width="stretch")
    else:
        st.info("Aucun outil exécuté; consulter les erreurs de précontrôle.")
    st.subheader("KPI et preuves utilisés")
    facts = [fact.model_dump(mode="json") for fact in report.evidence]
    if facts:
        st.dataframe(facts, hide_index=True, width="stretch")
    else:
        st.info("Aucune preuve disponible.")
    st.caption("Valeurs, unités, effectifs, sources et empreintes sont reconstruits par Python. Une différence descriptive ne prouve pas une cause.")
    if analysis is not None:
        with st.expander("Analyses détaillées : périodes, contributions, catégories, agences et backlog"):
            st.json(analysis, expanded=False)


def _render_investigation(report) -> None:
    if not report.decision_support:
        if report.analytical_plan is not None:
            st.json(report.analytical_plan.model_dump(mode="json"), expanded=True)
            for insight in report.insights:
                st.write(insight.recommendation)
        else:
            st.info("Ce scénario utilise la voie descriptive. Choisir le scénario drill-down pour afficher un plan d'investigation.")
        return
    for plan in report.decision_support:
        with st.container(border=True):
            st.subheader(plan.problem)
            st.caption("Origine du plan : " + ("Granite accepté par les contrôles Python" if plan.origin == "granite_validated" else "Fallback déterministe Python"))
            scope = plan.priority_scope
            st.markdown("**Périmètre prioritaire**")
            st.json(dict(filtres=scope.filters, période_avant=scope.baseline_period,
                         période_après=scope.current_period, base_temporelle=scope.period_basis), expanded=True)
            st.caption("Preuves exactes : " + ", ".join(plan.evidence_ids))
            for hypothesis in plan.investigation_hypotheses:
                with st.expander("Hypothèse à vérifier — " + hypothesis.hypothesis_id, expanded=True):
                    st.write(hypothesis.statement)
                    st.caption("Statut : hypothèse non vérifiée. Aucune causalité n'est établie.")
                    st.markdown("**Données supplémentaires nécessaires**")
                    for request in hypothesis.additional_data:
                        st.write("• " + request)
                    for step in plan.investigation_steps:
                        if step.hypothesis_id == hypothesis.hypothesis_id:
                            st.markdown("**Contrôle à effectuer**")
                            st.write(step.comparison)
                            st.json(dict(données=step.dataset, filtres=step.filters, champs=step.fields))
                    for action in plan.recommended_actions:
                        if action.hypothesis_id == hypothesis.hypothesis_id:
                            st.markdown(f"**Action d'investigation — priorité {action.priority}**")
                            st.write(action.action)
                            st.write("Justification : " + action.priority_reason)
                            st.write("Si la piste est soutenue : " + action.decision_if_supported)
                            st.write("Si elle est écartée : " + action.decision_if_refuted)
                    for verification in plan.expected_verification:
                        if verification.hypothesis_id == hypothesis.hypothesis_id:
                            st.markdown("**Observations attendues**")
                            st.write("Pour soutenir la piste : " + verification.supports)
                            st.write("Pour l'écarter : " + verification.refutes)
            with st.expander("Limites propres à ce plan"):
                for limitation in plan.limitations:
                    st.write("• " + limitation)


def _render_audit(run) -> None:
    report = run.report
    st.subheader("Validation automatique Python")
    st.json(report.validation.model_dump(mode="json"))
    if report.errors:
        st.markdown("**Erreurs et rejets conservés**")
        for error in report.errors:
            st.code(error, language=None)
    st.subheader("Tentatives, corrections et fallback")
    rows = []
    for event in run.events:
        if event.get("event") in {"llm_request", "llm_response", "validation", "deterministic_fallback", "inference_failed", "report_completed", "report_validated"}:
            result = event.get("result") or {}
            rows.append(dict(événement=event["event"], problème=event.get("problem_id"),
                             tentative=event.get("attempt"), statut=result.get("status", event.get("status")),
                             durée_ms=event.get("elapsed_ms"), erreurs=", ".join(result.get("errors", []))))
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Aucune tentative LLM présente dans le journal fourni.")
    with st.expander("Réponses brutes : entrées d'audit, pas conclusions validées"):
        st.warning("Les textes bruts peuvent contenir des affirmations rejetées. Seuls les éléments du rapport final ont été diffusés par l'agent.")
        for event in run.events:
            if event.get("event") == "llm_response":
                st.caption(f"Problème {event.get('problem_id', 'principal')} · tentative {event.get('attempt', '?')}")
                st.code(event.get("raw_response", ""), language="json")
    with st.expander("Détail des temps mesurés"):
        st.json(report.timing_ms)
    with st.expander("Vérification et provenance de l'enregistrement"):
        st.json(run.verification)
        st.json(run.display_metadata)
    with st.expander("Journal d'audit complet"):
        st.json(run.events, expanded=False)


def _render_run(run) -> None:
    report = run.report
    _origin_banner(run)
    st.markdown("**Question effectivement analysée**")
    st.write(report.question)
    status, decision, duration, model = st.columns(4)
    status.metric("Statut de sortie", report.status)
    decision.metric("Décision analytique", report.decision)
    duration.metric("Durée d'origine (s)" if run.mode == "replay" else "Durée de cette exécution (s)",
                    f"{report.timing_ms.get('total', 0) / 1000:.2f}")
    model.metric("Modèle déclaré", report.model or "Python uniquement")
    if report.status == "fallback":
        st.warning("FALLBACK PYTHON — la réponse LLM n'a pas été acceptée. Le rapport conserve le constat et le plan déterministes ainsi que les raisons du rejet.")
    elif report.status == "failed":
        st.error("ÉCHEC CONTRÔLÉ — aucun résultat analytique validé n'est revendiqué. Consulter le précontrôle et l'audit.")
    elif report.status == "validated":
        st.success("La réponse de l'agent a passé les contrôles automatiques Python. Ce statut n'est pas une validation humaine.")
    if report.decision == "insufficient_data":
        st.info("DONNÉES INSUFFISANTES — cette conclusion prudente ne démontre ni la présence ni l'absence du phénomène demandé.")
    elif report.decision == "refuse":
        st.info("REFUS JUSTIFIÉ — les conditions nécessaires à l'analyse ne sont pas réunies.")
    summary, evidence, investigation, audit, output = st.tabs(
        ["Synthèse", "Preuves et outils", "Investigation", "Validation et audit", "Rapport JSON"])
    with summary:
        _render_summary(report)
    with evidence:
        _render_evidence(report, run.analysis)
    with investigation:
        _render_investigation(report)
    with audit:
        _render_audit(run)
    with output:
        st.caption(f"Contrat {report.contract_version} · dossier local {run.path}")
        report_bytes = report.model_dump_json(indent=2).encode("utf-8")
        audit_bytes = ("\n".join(json.dumps(event, ensure_ascii=False, allow_nan=False) for event in run.events) + "\n").encode("utf-8")
        st.download_button("Télécharger le rapport JSON", data=report_bytes, file_name=f"{report.execution_id}_report.json",
                           mime="application/json", key="download_report")
        st.download_button("Télécharger le journal d'audit JSONL", data=audit_bytes, file_name=f"{report.execution_id}_audit.jsonl",
                           mime="application/x-ndjson", key="download_audit")
        if run.analysis is not None:
            st.download_button("Télécharger les analyses détaillées", data=_json(run.analysis).encode("utf-8"),
                               file_name=f"{report.execution_id}_analysis.json", mime="application/json", key="download_analysis")
        st.json(report.model_dump(mode="json"), expanded=False)


def main() -> None:
    st.set_page_config(page_title="BIFlow — BI Analyst", page_icon="📊", layout="wide")
    st.title("BIFlow · BI Analyst")
    st.caption("Démonstration académique locale · Python + Granite3.3 2B + Ollama · Interface indépendante du Dashboard Generator")
    try:
        scenarios = demo_service.load_scenarios(ROOT)
    except Exception as exc:
        st.error(f"Impossible de charger les scénarios de démonstration : {str(exc)[:500]}")
        st.stop()
    if not scenarios:
        st.error("Aucun scénario n'est configuré. Préparer le dossier de démonstration avant la soutenance.")
        st.stop()
    by_id = {scenario["id"]: scenario for scenario in scenarios}
    with st.sidebar:
        st.header("Préparer la démonstration")
        mode = st.radio("Mode de présentation", list(MODE_LABELS), format_func=MODE_LABELS.get,
                        key="mode_choice")
        scenario_id = st.selectbox("Scénario prédéfini", list(by_id),
                                   format_func=lambda key: by_id[key]["title"], key="scenario_id",
                                   disabled=mode in {"controlled_test", "latest"})
        st.caption(by_id[scenario_id].get("description", ""))
        recent = demo_service.latest_cases(ROOT) if mode == "latest" else []
        if mode == "latest":
            if not recent:
                st.info("Aucun manifeste de résultats récents n'est disponible.")
                st.stop()
            selected_recent = st.selectbox("Résultat récent", [case["id"] for case in recent], key="latest_case")
            recent_case = next(case for case in recent if case["id"] == selected_recent)
        st.divider()
        st.markdown("**Règle de présentation**")
        st.write("Une archive est une restitution. Un test injecté éprouve le validateur. Seul le mode direct appelle Granite pour la question posée.")
    mode_changed = st.session_state.get("_last_mode") != mode
    if mode_changed:
        st.session_state.pop("active_run", None)
        st.session_state.pop("loaded_recording_id", None)
        st.session_state["_last_mode"] = mode
    if mode == "latest":
        st.session_state["question"] = recent_case["question"]
    elif mode_changed or st.session_state.get("_last_scenario") != scenario_id:
        st.session_state["question"] = by_id[scenario_id]["question"]
        st.session_state["_last_scenario"] = scenario_id
    question = st.text_area("Question métier", key="question", height=100, disabled=mode != "live")
    selected_dataset = "active"
    if mode == "live":
        selected_dataset = st.selectbox("Données de l'analyse", ["active", "legacy", *demo_service.available_datasets(ROOT)],
            format_func=lambda value: "Version active" if value == "active" else "Extrait historique 10 000 lignes" if value == "legacy" else value,
            key="data_version_choice")
        dataset_identity = (selected_dataset, demo_service.active_dataset(ROOT) if selected_dataset == "active" else selected_dataset)
        if st.session_state.get("_display_dataset") != dataset_identity:
            st.session_state.pop("active_run", None)
            st.session_state["_display_dataset"] = dataset_identity
    if mode == "live" and st.session_state.get("_display_question") != question:
        st.session_state.pop("active_run", None)
        st.session_state["_display_question"] = question
    try:
        if mode == "replay":
            st.caption("La question est celle de l'exécution archivée. Pour poser une autre question, choisir le mode direct.")
            reload = st.button("Recharger l'exécution enregistrée", key="reload_recording")
            if reload or st.session_state.get("loaded_recording_id") != scenario_id:
                st.session_state.pop("active_run", None)
                with st.spinner("Chargement et contrôle de l'enregistrement…"):
                    st.session_state["active_run"] = demo_service.load_recording(scenario_id, ROOT)
                st.session_state["loaded_recording_id"] = scenario_id
        elif mode == "live":
            st.info("Le mode direct peut prendre plusieurs minutes sur ce poste. Le bouton est nécessaire pour lancer une nouvelle analyse.")
            supported = by_id[scenario_id].get("supports_live", True)
            if not supported:
                st.warning("Ce scénario correspond à un test de rejet contrôlé. Utiliser le mode « Test contrôlé du validateur » pour le démontrer.")
            if st.button("Analyser avec Granite", type="primary", key="run_live", disabled=not question.strip() or not supported):
                st.session_state.pop("active_run", None)
                with st.spinner("Analyse Python, appel Ollama et validation de Granite en cours…"):
                    st.session_state["active_run"] = (demo_service.run_live(question, ROOT) if selected_dataset == "active" else
                        demo_service.run_live(question, ROOT, dataset_version=selected_dataset))
        elif mode == "latest":
            st.caption("Rapport issu de la dernière campagne réelle, relu sans nouvel appel à Granite.")
            if mode_changed or st.session_state.get("loaded_latest_id") != selected_recent:
                st.session_state["active_run"] = demo_service.load_latest_evaluation(selected_recent, ROOT)
                st.session_state["loaded_latest_id"] = selected_recent
        else:
            st.info("Ce mode utilise le cas de rejet prédéfini, indépendamment de la question métier. Les réponses incorrectes sont injectées et clairement identifiées.")
            if st.button("Exécuter le test contrôlé", type="primary", key="run_controlled"):
                st.session_state.pop("active_run", None)
                with st.spinner("Injection contrôlée et vérification du fallback…"):
                    st.session_state["active_run"] = demo_service.run_rejection_demo(ROOT)
    except Exception as exc:
        st.session_state.pop("active_run", None)
        st.error(f"La démonstration n'a pas pu être exécutée : {str(exc)[:500]}")
    run = st.session_state.get("active_run")
    if run is not None:
        _render_run(run)
    else:
        st.info("Aucun nouveau résultat n'est présenté tant qu'une exécution ou un enregistrement n'a pas été chargé.")


if __name__ == "__main__":
    main()
