from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Protocol

from benchmark_llm.data import DataValidationError
from benchmark_llm.runner import OllamaClient, OllamaFailure

from .analytics import load_real_bundle
from .models import AnalysisContext, BIAnalysisReport, BIInsight, EvidenceFact, ToolTrace, ValidationResult
from .tools import ToolResult, execute_tool, select_tools
from .draft import build_policy, draft_payload, validate_draft

MODEL_NAME = "granite3.3:2b"
AGENT_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096,
                 "num_predict": 384, "top_k": 40, "top_p": 1.0, "repeat_penalty": 1.0}
INFERENCE_TIMEOUT_SECONDS = 180


class OllamaTransport(Protocol):
    def installed(self) -> dict[str, dict[str, Any]]: ...

    def call(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...


_RECOMMENDATIONS_FR = {
    "review_comparison": "Examiner les groupes concernés et vérifier la comparabilité des dossiers avant toute action.",
    "review_temporal_signal": "Vérifier le signal temporel dans les sources métier avant de décider d'une action.",
    "verify_data_quality": "Vérifier la complétude et les règles de saisie des données avant d'interpréter les délais.",
    "review_kpi_context": "Présenter ces KPI avec leur population, leur dénominateur et leurs limites d'échantillonnage.",
    "collect_more_history": "Collecter davantage de périodes complètes avant de conclure sur une tendance ou une anomalie.",
    "collect_more_observations": "Compléter les observations valides avant de comparer ou de classer ces groupes.",
    "no_strong_action": "Aucune action opérationnelle forte n'est justifiée par les éléments disponibles.",
}

_LIMITATIONS_FR = {
    "sample_only": "Les résultats décrivent l’échantillon préparé, pas l’ensemble de New York.",
    "nonrandom_sample": "L’échantillonnage par fenêtres n’est pas aléatoire; un biais de sélection est possible.",
    "closed_only": "Les délais concernent les demandes clôturées avec durée valide.",
    "no_cause": "Les données ne démontrent aucune cause.",
    "no_sla": "Aucune cible SLA n’a été fournie; aucun dépassement n’est affirmé.",
    "partial_month": "Les mois partiels sont exclus des comparaisons temporelles.",
    "small_sample": "Les groupes avec moins de 30 délais valides ne sont pas classés opérationnellement.",
    "missing_data": "Des délais sont manquants ou invalides; ils ne sont pas remplacés par zéro.",
    "insufficient_history": "L’historique complet est insuffisant pour le test temporel demandé.",
    "population_not_sample": "Les volumes mensuels proviennent des agrégats de population, pas de l’échantillon.",
    "serial_autocorrelation_not_adjusted": "Le test saisonnier ne corrige pas l’autocorrélation temporelle; ses p-values doivent être interprétées avec prudence.",
}

_TREND_TERMS = ("tendance", "évolution", "evolution", "hausse", "baisse", "mois", "mensuel")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_output_root(context: AnalysisContext, *, investigation=False) -> Path:
    if context.dataset_version is not None:
        return context.project_root / "outputs/pipeline_delivery/analyst_runs"
    return context.project_root / ("outputs/bi_analyst_decision_runs" if investigation else "outputs/bi_analyst_runs")


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize audit value of type {type(value).__name__}.")


def _is_trend_question(question: str) -> bool:
    text = question.casefold()
    return any(term in text for term in _TREND_TERMS)


def _append_audit(audit_path: Path | None, event: dict[str, Any]) -> None:
    if audit_path is None:
        return
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(event, ensure_ascii=False, default=_json_default, allow_nan=False)
            + "\n"
        )


def _required_limitations(
    tool_results: list[ToolResult], bundle: dict[str, Any], question: str
) -> set[str]:
    required = {"sample_only", "nonrandom_sample", "no_cause"}
    if any(fact.metric == "median_resolution_days" for result in tool_results for fact in result.evidence):
        required.update({"closed_only", "no_sla"})
    if any(fact.metric == "monthly_request_volume_change" for result in tool_results for fact in result.evidence):
        required.add("population_not_sample")
    if any(
        fact.metric == "monthly_trend_seasonal_kendall_tau"
        for result in tool_results
        for fact in result.evidence
    ):
        required.add("serial_autocorrelation_not_adjusted")
    if _insufficiency_finding(question, tool_results) == "insufficient_history":
        required.add("insufficient_history")
    if bundle["metadata"].get("partial_months"):
        required.add("partial_month")
    if bundle["quality"]["closed_missing_delay_n"] > 0:
        required.add("missing_data")
    if any(
        result.trace.tool == "compare_groups"
        and result.result.get("delay_group_count", 0)
        > result.result.get("eligible_group_count", 0)
        for result in tool_results
    ):
        required.add("small_sample")
    return required


def _insufficiency_finding(
    question: str,
    tool_results: list[ToolResult],
) -> str | None:
    text = question.casefold()
    anomaly_tool = next(
        (result for result in tool_results if result.trace.tool == "detect_anomalies"),
        None,
    )
    if any(word in text for word in ("anomal", "inhabituel", "pic")):
        if anomaly_tool is not None and anomaly_tool.trace.status == "skipped":
            return "insufficient_history"
        # The anomaly prerequisite is independent of the longer trend test.
        if anomaly_tool is not None and anomaly_tool.trace.status == "success":
            return None
    compare_tool = next(
        (result for result in tool_results if result.trace.tool == "compare_groups"),
        None,
    )
    if any(word in text for word in ("compar", "classe", "arrondissement", "type")):
        if compare_tool is not None and compare_tool.trace.status == "skipped":
            return "small_sample"
    trend_tool = next(
        (result for result in tool_results if result.trace.tool == "analyze_trend"),
        None,
    )
    if _is_trend_question(question):
        if trend_tool is not None and trend_tool.trace.status == "skipped":
            return "insufficient_history"
    return None


def _find_required_limits(
    tool_results: list[ToolResult], bundle: dict[str, Any], question: str
) -> list[str]:
    return sorted(_required_limitations(tool_results, bundle, question))


def _audit_report(report: BIAnalysisReport, audit_dir: Path | None) -> None:
    if audit_dir is None:
        return
    audit_dir.mkdir(parents=True, exist_ok=True)
    target = audit_dir / "report.json"
    target.write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _base_report(
    execution_id: str,
    question: str,
    *,
    status: str,
    decision: str,
    finding: str,
    evidence: list[EvidenceFact] | None = None,
    tool_trace: list[ToolTrace] | None = None,
    errors: list[str] | None = None,
    validation: ValidationResult | None = None,
    limitations: list[str] | None = None,
    timing_ms: dict[str, float] | None = None,
    model: str | None = None,
) -> BIAnalysisReport:
    return BIAnalysisReport(
        execution_id=execution_id,
        generated_at=_timestamp(),
        question=question,
        status=status,
        model=model,
        decision=decision,
        finding=finding,
        insights=[],
        evidence=evidence or [],
        limitations=limitations or [],
        source_refs=sorted({fact.source for fact in evidence or []}),
        validation=validation
        or ValidationResult(
            status="not_run",
            json_valid=False,
            schema_valid=False,
            evidence_valid=False,
            numerical_claims_valid=False,
            causal_claims_clear=False,
            errors=[],
        ),
        tool_trace=tool_trace or [],
        errors=errors or [],
        timing_ms=timing_ms or {},
    )


def _safe_fallback(
    execution_id: str,
    question: str,
    evidence: list[EvidenceFact],
    tool_results: list[ToolResult],
    required_codes: list[str],
    validation: ValidationResult,
    errors: list[str],
    timing_ms: dict[str, float],
    *,
    model_used: bool = False,
    selected_evidence: list[EvidenceFact] | None = None,
    recommendation_code: str = "no_strong_action",
) -> BIAnalysisReport:
    insufficiency_finding = _insufficiency_finding(question, tool_results)
    insufficiency = insufficiency_finding is not None
    selected = selected_evidence if selected_evidence is not None else evidence
    selected = _include_required_trend_evidence(question, selected, evidence)
    finding = insufficiency_finding or _derive_finding(selected, tool_results)
    if insufficiency and recommendation_code == "no_strong_action":
        recommendation_code = (
            _insufficiency_recommendation(question, tool_results)
            or "collect_more_history"
        )
    report = _base_report(
        execution_id,
        question,
        status="fallback",
        decision="insufficient_data" if insufficiency else "answer",
        finding=finding,
        evidence=_supporting_evidence(selected, evidence, required_codes),
        tool_trace=[result.trace for result in tool_results],
        errors=errors,
        validation=validation,
        limitations=[_LIMITATIONS_FR[code] for code in required_codes],
        timing_ms=timing_ms,
        model=MODEL_NAME if model_used else None,
    )
    if selected:
        selected_metrics = {fact.metric for fact in selected}
        summary_selection = any(
            fact.scope == "prepared NYC 311 sample" for fact in selected
        )
        category = (
            "anomaly"
            if "statistical_anomaly_score" in selected_metrics
            else "trend"
            if any(metric.startswith("monthly_") for metric in selected_metrics)
            else "summary"
            if summary_selection
            else "comparison"
            if any(
                metric.startswith(("median_", "sample_request_count"))
                for metric in selected_metrics
            )
            else "data_quality"
        )
        report.insights = [
            BIInsight(
                insight_id=f"{execution_id}-baseline",
                category=category,
                finding=finding,
                insight=_render_python_insight(
                    question,
                    selected,
                    tool_results,
                    insufficient_finding=insufficiency_finding,
                ),
                recommendation=_RECOMMENDATIONS_FR[recommendation_code],
                evidence_fact_ids=[fact.fact_id for fact in selected],
                limitation_codes=required_codes,
            )
        ]
    return report


def _format_number(value: float) -> str:
    formatted = f"{value:,.3f}".replace(",", " ").replace(".", ",")
    return formatted.rstrip("0").rstrip(",")


def _supporting_evidence(
    selected: list[EvidenceFact],
    all_evidence: list[EvidenceFact],
    required_codes: list[str],
) -> list[EvidenceFact]:
    included = {fact.fact_id for fact in selected}
    needed_metrics: set[str] = set()
    if "missing_data" in required_codes:
        needed_metrics.add("missing_or_invalid_resolution_days")
    if "small_sample" in required_codes:
        needed_metrics.add("small_sample_group_count")
    supporting = [
        fact
        for fact in all_evidence
        if fact.metric in needed_metrics and fact.fact_id not in included
    ]
    return [*selected, *supporting]


def _render_python_insight(
    question: str,
    evidence: list[EvidenceFact],
    tool_results: list[ToolResult],
    *,
    insufficient_finding: str | None,
) -> str:
    if insufficient_finding == "small_sample":
        return (
            "Les groupes concernés n'atteignent pas le minimum d'observations valides; "
            "aucun classement opérationnel n'est présenté. Les effectifs disponibles "
            "figurent dans les preuves."
        )
    if insufficient_finding == "insufficient_history":
        if any(word in question.casefold() for word in ("anomal", "inhabituel", "pic")):
            return (
                "L'historique mensuel complet est insuffisant pour le test d'anomalie demandé; "
                "aucune absence d'anomalie ne peut être établie. Le nombre de périodes "
                "disponibles et les seuils requis figurent dans les preuves."
            )
        if any(fact.metric == "small_sample_group_count" for fact in evidence):
            return (
                "Certains groupes n'atteignent pas le minimum de données valides prévu; "
                "aucun classement opérationnel n'est présenté. Les effectifs sont détaillés "
                "dans les preuves."
            )
        if _is_trend_question(question):
            return (
                "L'historique mensuel complet est trop court pour le test de tendance "
                "Mann–Kendall saisonnier requis (36 périodes contiguës). Les variations observées "
                "restent descriptives; aucune conclusion de significativité n'est tirée."
            )
        return (
            "Les périodes complètes disponibles ne suffisent pas à établir une conclusion "
            "sur la présence ou l’absence d’une anomalie. Les chiffres d’appui et leurs "
            "périodes figurent dans les preuves."
        )

    delays = [
        fact
        for fact in evidence
        if fact.metric == "median_resolution_days"
        and fact.scope.startswith(("borough=", "complaint_type="))
    ]
    if delays:
        unit = delays[0].unit
        ordered = sorted(delays, key=lambda fact: fact.value or 0)
        lowest, highest = ordered[0], ordered[-1]
        if len(ordered) == 1:
            return (
                f"La médiane descriptive pour {highest.scope} est "
                f"{_format_number(highest.value or 0)} {unit}. "
                "Les groupes de comparaison et leurs effectifs sont indiqués dans les preuves."
            )
        difference = (highest.value or 0) - (lowest.value or 0)
        return (
            f"Parmi les groupes retenus, la médiane la plus élevée concerne "
            f"{highest.scope} ({_format_number(highest.value or 0)} {unit}) et la plus "
            f"faible {lowest.scope} ({_format_number(lowest.value or 0)} {unit}); "
            f"l’écart descriptif calculé est de {_format_number(difference)} {unit}. "
            "Les effectifs associés figurent dans les preuves."
        )

    anomaly_facts = [
        fact for fact in evidence if fact.metric == "statistical_anomaly_score"
    ]
    if anomaly_facts:
        periods = ", ".join(fact.scope for fact in anomaly_facts)
        return (
            "Les tests statistiques ont signalé un signal à examiner pour "
            f"{periods}. Les scores, méthodes, seuils, périodes de référence et effectifs "
            "sont rattachés aux preuves; ce signal ne démontre pas une cause."
        )

    monthly = [
        fact for fact in evidence if fact.metric == "monthly_request_volume_change"
    ]
    if monthly:
        result = next(
            (
                result.result
                for result in tool_results
                if result.trace.tool == "analyze_trend"
            ),
            {},
        )
        test = result.get("trend_significance", {})
        trend_fact = next(
            (
                fact
                for fact in evidence
                if fact.metric == "monthly_trend_seasonal_kendall_tau"
            ),
            None,
        )
        if test.get("status") == "significant" and trend_fact is not None:
            direction = "à la hausse" if test.get("direction") == "up" else "à la baisse"
            return (
                f"Le test de Mann–Kendall indique une tendance statistiquement significative "
                f"{direction} sur les périodes complètes analysées "
                f"({trend_fact.reference_period}, p={_format_number(trend_fact.p_value or 0)} "
                f"< {_format_number(trend_fact.threshold or 0)}). "
                "La statistique tau-b et la source figurent dans les preuves; ce résultat "
                "n'établit aucune cause."
            )
        if test.get("status") == "not_significant" and trend_fact is not None:
            return (
                "Le test de Mann–Kendall ne détecte pas de tendance statistiquement "
                f"significative sur la période {trend_fact.reference_period} "
                f"(p={_format_number(trend_fact.p_value or 0)}, seuil "
                f"{_format_number(trend_fact.threshold or 0)}). Les variations mensuelles "
                "restent descriptives."
            )
        if test.get("status") == "no_variation":
            return (
                "Aucune variation interannuelle n'est observée pour les mêmes mois "
                "calendaires; le test saisonnier ne peut pas estimer une tendance "
                "statistique sur cette série."
            )
        return (
            "Les variations mensuelles sont descriptives; l'historique complet disponible "
            "est insuffisant pour effectuer le test de significativité de tendance requis."
        )

    summary_metrics = {
        "request_volume": "volume",
        "mean_resolution_days": "durée moyenne",
        "median_resolution_days": "durée médiane",
        "closure_rate": "taux de clôture",
    }
    summary = [
        fact for fact in evidence if fact.metric in summary_metrics
    ]
    if summary:
        descriptions = [
            f"{summary_metrics[fact.metric]} : {_format_number(fact.value or 0)} "
            f"{fact.unit} (effectif de référence {fact.n})"
            for fact in summary
        ]
        return (
            "Synthèse des KPI de l’extrait préparé : "
            + "; ".join(descriptions)
            + ". Ces indicateurs décrivent l’extrait et ne démontrent aucune cause."
        )

    quality = [fact for fact in evidence if fact.metric in {
        "sample_rows",
        "valid_resolution_days",
        "missing_or_invalid_resolution_days",
        "not_closed_requests",
    }]
    if quality:
        return (
            "Les preuves distinguent les demandes non clôturées des clôtures sans durée "
            "valide. Les délais non valides ne sont pas remplacés par zéro."
        )

    return (
        "Les indicateurs sélectionnés sont calculés par Python et rattachés à leurs sources; "
        "ils décrivent les données préparées et n’établissent aucune causalité."
    )


def _evidence_candidates(
    question: str,
    evidence: list[EvidenceFact],
) -> list[EvidenceFact]:
    text = question.casefold()
    quality_request = any(
        word in text
        for word in ("manqu", "qualité", "qualite", "quality", "incomplet", "non clôt")
    )
    anomaly_request = any(word in text for word in ("anomal", "inhabituel", "pic"))
    trend_request = _is_trend_question(question)
    comparison_request = any(
        word in text
        for word in ("compar", "classe", "borough", "arrondissement", "catégorie", "type")
    )
    summary_request = any(
        word in text
        for word in ("comité", "synthèse", "synthese", "pilotage", "résume", "resume")
    )

    if quality_request:
        metrics = {
            "sample_rows",
            "valid_resolution_days",
            "missing_or_invalid_resolution_days",
            "not_closed_requests",
        }
        candidates = [fact for fact in evidence if fact.metric in metrics]
    elif anomaly_request or trend_request:
        trend_test_facts = [
            fact
            for fact in evidence
            if fact.metric == "monthly_trend_seasonal_kendall_tau"
        ]
        candidates = trend_test_facts + [
            fact
            for fact in evidence
            if fact.metric in {
                "statistical_anomaly_score",
                "monthly_request_volume_change",
                "available_complete_periods",
            } and fact not in trend_test_facts
        ]
    elif comparison_request:
        candidates = [
            fact
            for fact in evidence
            if fact.metric in {"median_resolution_days", "sample_request_count"}
        ]
    elif summary_request:
        candidates = [
            fact
            for fact in evidence
            if fact.scope == "prepared NYC 311 sample"
            and fact.metric
            in {
                "request_volume",
                "mean_resolution_days",
                "median_resolution_days",
                "closure_rate",
            }
        ]
    else:
        candidates = [
            fact
            for fact in evidence
            if fact.metric not in {
                "sample_rows",
                "valid_resolution_days",
                "missing_or_invalid_resolution_days",
            }
        ]
    return (candidates or evidence)[:12]


def _include_required_trend_evidence(
    question: str,
    selected: list[EvidenceFact],
    evidence: list[EvidenceFact],
) -> list[EvidenceFact]:
    if not _is_trend_question(question):
        return selected
    test_fact = next(
        (
            fact
            for fact in evidence
            if fact.metric == "monthly_trend_seasonal_kendall_tau"
        ),
        None,
    )
    if test_fact is None or any(fact.fact_id == test_fact.fact_id for fact in selected):
        return selected
    return [*selected, test_fact]


def _insufficiency_recommendation(
    question: str,
    tool_results: list[ToolResult],
) -> str | None:
    finding = _insufficiency_finding(question, tool_results)
    return {
        "small_sample": "collect_more_observations",
        "insufficient_history": "collect_more_history",
    }.get(finding)


def _derive_finding(
    evidence: list[EvidenceFact], tool_results: list[ToolResult]
) -> str:
    metrics = {fact.metric for fact in evidence}
    if "statistical_anomaly_score" in metrics:
        return "statistical_anomaly"
    if "monthly_trend_seasonal_kendall_tau" in metrics:
        test = next(
            (
                result.result.get("trend_significance", {})
                for result in tool_results
                if result.trace.tool == "analyze_trend"
            ),
            {},
        )
        if test.get("status") != "significant":
            return "no_persistent_trend"
        return "higher" if test.get("direction") == "up" else "lower"
    if "monthly_request_volume_change" in metrics:
        if any(result.trace.tool == "detect_anomalies" and result.trace.status == "success"
               for result in tool_results):
            return "descriptive"
        return "insufficient_history"
    return "descriptive"


def _default_recommendation(
    candidates: list[EvidenceFact],
    tool_results: list[ToolResult],
    question: str,
) -> str:
    insufficiency_recommendation = _insufficiency_recommendation(
        question, tool_results
    )
    if insufficiency_recommendation:
        return insufficiency_recommendation
    metrics = {fact.metric for fact in candidates}
    if metrics & {
        "sample_rows",
        "valid_resolution_days",
        "missing_or_invalid_resolution_days",
        "not_closed_requests",
    }:
        return "verify_data_quality"
    if any(fact.scope == "prepared NYC 311 sample" for fact in candidates):
        return "review_kpi_context"
    if metrics & {"median_resolution_days", "sample_request_count"}:
        return "review_comparison"
    if metrics & {
        "monthly_request_volume_change",
        "monthly_trend_seasonal_kendall_tau",
        "statistical_anomaly_score",
    }:
        return "review_temporal_signal"
    return "no_strong_action"


def _analyze_legacy(
    question: str,
    context: AnalysisContext,
    *,
    client: OllamaTransport | None = None,
    use_llm: bool = True,
    audit_root: Path | None = None,
) -> BIAnalysisReport:
    from .decision_tools import decision_question
    if decision_question(question):
        from .decision import analyze_decision
        return analyze_decision(question, context, client=client, use_llm=use_llm, audit_root=audit_root)
    if not question.strip():
        raise ValueError("A business question is required.")
    if not isinstance(context, AnalysisContext):
        context = AnalysisContext.model_validate(context)
    execution_id = uuid.uuid4().hex
    started = time.perf_counter()
    output_root = audit_root or _default_output_root(context)
    audit_dir = output_root / execution_id if output_root is not None else None
    audit_path = audit_dir / "audit.jsonl" if audit_dir is not None else None
    _append_audit(
        audit_path,
        {
            "event": "request_received",
            "execution_id": execution_id,
            "timestamp": _timestamp(),
            "question": question,
            "data_kind": context.data_kind,
        },
    )
    timings: dict[str, float] = {}

    if use_llm:
        local_client = client or OllamaClient(
            os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            timeout=INFERENCE_TIMEOUT_SECONDS,
        )
        preflight_started = time.perf_counter()
        try:
            available = local_client.installed()
        except OllamaFailure as exc:
            message = f"ollama_preflight_{exc.status}:{str(exc)[:500]}"
            report = _base_report(
                execution_id,
                question,
                status="failed",
                decision="refuse",
                finding="insufficient_data",
                errors=[message],
            )
            _append_audit(
                audit_path,
                {"event": "model_preflight_failed", "error": message, "timestamp": _timestamp()},
            )
            _audit_report(report, audit_dir)
            return report
        if MODEL_NAME not in available:
            message = f"required_model_not_installed:{MODEL_NAME}"
            report = _base_report(
                execution_id,
                question,
                status="failed",
                decision="refuse",
                finding="insufficient_data",
                errors=[message],
            )
            _append_audit(
                audit_path,
                {"event": "model_preflight_failed", "error": message, "timestamp": _timestamp()},
            )
            _audit_report(report, audit_dir)
            return report
        model_metadata = available[MODEL_NAME]
        try:
            server_metadata = local_client.call("version", timeout=5)
            server_version = server_metadata.get("version")
        except OllamaFailure as exc:
            server_version = None
            _append_audit(
                audit_path,
                {
                    "event": "server_version_unavailable",
                    "error": f"{exc.status}:{str(exc)[:400]}",
                    "timestamp": _timestamp(),
                },
            )
        timings["model_preflight"] = (time.perf_counter() - preflight_started) * 1000
        _append_audit(
            audit_path,
            {
                "event": "model_preflight_succeeded",
                "model": MODEL_NAME,
                "model_digest": model_metadata.get("digest"),
                "server_version": server_version,
                "options": AGENT_OPTIONS,
                "timeout_seconds": INFERENCE_TIMEOUT_SECONDS,
                "timestamp": _timestamp(),
            },
        )
    else:
        local_client = client

    data_started = time.perf_counter()
    try:
        bundle = load_real_bundle(context.project_root, context.dataset_version)
    except (DataValidationError, OSError, ValueError) as exc:
        message = f"data_validation_failed:{type(exc).__name__}:{str(exc)[:800]}"
        report = _base_report(
            execution_id,
            question,
            status="failed",
            decision="refuse",
            finding="missing_data",
            errors=[message],
        )
        _append_audit(
            audit_path,
            {"event": "data_validation_failed", "error": message, "timestamp": _timestamp()},
        )
        _audit_report(report, audit_dir)
        return report
    timings["data_validation"] = (time.perf_counter() - data_started) * 1000

    tool_results: list[ToolResult] = []
    selected_tools = select_tools(question)
    try:
        for tool_name in selected_tools:
            result = execute_tool(tool_name, question, context, bundle)
            tool_results.append(result)
            _append_audit(
                audit_path,
                {
                    "event": "tool_executed",
                    "execution_id": execution_id,
                    "trace": result.trace.model_dump(mode="json"),
                    "result": result.result,
                    "evidence": [item.model_dump(mode="json") for item in result.evidence],
                },
            )
    except (ValueError, KeyError, DataValidationError) as exc:
        message = f"tool_execution_failed:{type(exc).__name__}:{str(exc)[:800]}"
        report = _base_report(
            execution_id,
            question,
            status="failed",
            decision="refuse",
            finding="insufficient_data",
            evidence=[fact for result in tool_results for fact in result.evidence],
            tool_trace=[result.trace for result in tool_results],
            errors=[message],
        )
        _append_audit(audit_path, {"event": "tool_execution_failed", "error": message})
        _audit_report(report, audit_dir)
        return report

    evidence_by_id = {
        fact.fact_id: fact for result in tool_results for fact in result.evidence
    }
    evidence = list(evidence_by_id.values())
    required_codes = _find_required_limits(tool_results, bundle, question)
    candidates = _evidence_candidates(question, evidence)
    if not use_llm:
        validation = ValidationResult(
            status="validated",
            json_valid=True,
            schema_valid=True,
            evidence_valid=True,
            numerical_claims_valid=True,
            causal_claims_clear=True,
            errors=[],
        )
        report = _safe_fallback(
            execution_id,
            question,
            evidence,
            tool_results,
            required_codes,
            validation,
            [],
            {"total": (time.perf_counter() - started) * 1000},
            selected_evidence=candidates,
            recommendation_code=_default_recommendation(
                candidates, tool_results, question
            ),
        )
        _append_audit(audit_path, {"event": "deterministic_baseline", "timestamp": _timestamp()})
        return report

    if local_client is None:
        message = "ollama_client_unavailable"
        report = _base_report(
            execution_id,
            question,
            status="failed",
            decision="refuse",
            finding="insufficient_data",
            errors=[message],
        )
        _append_audit(audit_path, {"event": "model_client_unavailable", "error": message})
        _audit_report(report, audit_dir)
        return report

    candidates = _include_required_trend_evidence(question, candidates, evidence)
    insufficiency_finding = _insufficiency_finding(question, tool_results)
    finding = insufficiency_finding or _derive_finding(candidates, tool_results)
    recommendation_code = _default_recommendation(candidates, tool_results, question)
    policy = build_policy(finding, candidates, required_codes,
                          _RECOMMENDATIONS_FR[recommendation_code], recommendation_code)
    _append_audit(audit_path, {"event": "draft_contract", "version": "granite-draft-v2",
                             "schema": policy.schema(), "python_finding": finding,
                             "allowed_evidence_ids": list(policy.evidence_ids)})
    first_raw: str | None = None
    corrected_raw: str | None = None
    latest_validation = ValidationResult(
        status="not_run",
        json_valid=False,
        schema_valid=False,
        evidence_valid=False,
        numerical_claims_valid=True,
        causal_claims_clear=True,
        errors=[],
    )
    errors: list[str] = []
    draft = None
    attempt_count = 0
    inference_attempted = False
    for attempt in range(2):
        payload = draft_payload(question, policy, AGENT_OPTIONS,
                                latest_validation.errors if attempt else None)
        _append_audit(audit_path, {"event": "llm_request", "attempt": attempt + 1,
                                 "payload": payload, "timestamp": _timestamp()})
        inference_started = time.perf_counter()
        inference_attempted = True
        try:
            response = local_client.call(
                "chat", payload=payload, timeout=INFERENCE_TIMEOUT_SECONDS
            )
        except OllamaFailure as exc:
            message = f"inference_{exc.status}:{str(exc)[:800]}"
            errors.extend(latest_validation.errors)
            errors.append(message)
            timings[f"inference_attempt_{attempt + 1}"] = (time.perf_counter() - inference_started) * 1000
            _append_audit(
                audit_path,
                {
                    "event": "inference_failed",
                    "attempt": attempt + 1,
                    "error": message,
                    "timestamp": _timestamp(),
                },
            )
            break
        elapsed = (time.perf_counter() - inference_started) * 1000
        timings[f"inference_attempt_{attempt + 1}"] = elapsed
        attempt_count += 1
        message_value = response.get("message")
        raw_value = (
            message_value.get("content")
            if isinstance(message_value, dict)
            else None
        )
        raw = raw_value if isinstance(raw_value, str) else ""
        if attempt == 0:
            first_raw = raw
        else:
            corrected_raw = raw
        _append_audit(
            audit_path,
            {
                "event": "llm_response",
                "execution_id": execution_id,
                "attempt": attempt + 1,
                "raw_response": raw,
                "elapsed_ms": elapsed,
                "model": MODEL_NAME,
                "options": AGENT_OPTIONS,
                "response_model": response.get("model"),
                "done": response.get("done"),
                "done_reason": response.get("done_reason"),
                "ollama_metrics": {
                    key: response[key]
                    for key in (
                        "total_duration",
                        "load_duration",
                        "prompt_eval_count",
                        "prompt_eval_duration",
                        "eval_count",
                        "eval_duration",
                    )
                    if key in response
                },
                "timestamp": _timestamp(),
            },
        )
        for metric in ("load_duration", "prompt_eval_duration", "eval_duration"):
            if isinstance(response.get(metric), (int, float)):
                timings[f"ollama_{metric}_attempt_{attempt + 1}"] = response[metric] / 1_000_000
        validation_started = time.perf_counter()
        draft, latest_validation = validate_draft(raw, policy)
        if response.get("done") is False or response.get("done_reason") == "length":
            latest_validation.status = "rejected"
            latest_validation.errors.append("incomplete_model_response")
        if response.get("model", MODEL_NAME) != MODEL_NAME:
            latest_validation.status = "rejected"
            latest_validation.errors.append("unexpected_model_identity")
        timings[f"validation_attempt_{attempt + 1}"] = (time.perf_counter() - validation_started) * 1000
        if audit_dir is not None:
            (audit_dir / f"response_attempt_{attempt + 1}.txt").write_text(raw, encoding="utf-8")
            (audit_dir / f"validation_attempt_{attempt + 1}.json").write_text(
                latest_validation.model_dump_json(indent=2), encoding="utf-8")
        _append_audit(
            audit_path,
            {
                "event": "validation",
                "attempt": attempt + 1,
                "result": latest_validation.model_dump(mode="json"),
                "timestamp": _timestamp(),
            },
        )
        if latest_validation.status == "validated" and draft is not None:
            break
        if attempt == 1:
            errors.extend(latest_validation.errors)

    if (
        draft is None
        or latest_validation.status != "validated"
        or attempt_count == 0
    ):
        if corrected_raw is not None:
            errors.append("correction_attempt_rejected")
        if first_raw is not None and corrected_raw is None and attempt_count == 1:
            errors.append("initial_response_rejected")
        report = _safe_fallback(
            execution_id,
            question,
            evidence,
            tool_results,
            required_codes,
            latest_validation,
            errors,
            {**timings, "total": (time.perf_counter() - started) * 1000},
            model_used=inference_attempted,
            selected_evidence=candidates,
            recommendation_code=_default_recommendation(
                candidates, tool_results, question
            ),
        )
        _append_audit(
            audit_path,
            {
                "event": "deterministic_fallback",
                "initial_response_present": first_raw is not None,
                "correction_response_present": corrected_raw is not None,
                "errors": errors,
                "timestamp": _timestamp(),
            },
        )
        return report

    selected = [evidence_by_id[fact_id] for fact_id in draft.evidence_ids]
    selected = _include_required_trend_evidence(question, selected, evidence)
    insufficiency_finding = _insufficiency_finding(question, tool_results)
    insufficiency = insufficiency_finding is not None
    finding = insufficiency_finding or _derive_finding(selected, tool_results)
    metrics = {fact.metric for fact in selected}
    summary_selection = any(
        fact.scope == "prepared NYC 311 sample" for fact in selected
    )
    category = (
        "anomaly"
        if "statistical_anomaly_score" in metrics
        else "trend"
        if any(metric.startswith("monthly_") for metric in metrics)
        else "summary"
        if summary_selection
        else "comparison"
        if any(metric.startswith(("median_", "sample_request_count")) for metric in metrics)
        else "data_quality"
    )
    insight = BIInsight(
        insight_id=f"{execution_id}-001",
        category=category,
        finding=finding,
        insight=draft.insight + " " + _render_python_insight(
            question,
            selected,
            tool_results,
            insufficient_finding=insufficiency_finding,
        ),
        recommendation=draft.recommendation,
        evidence_fact_ids=[fact.fact_id for fact in selected],
        limitation_codes=required_codes,
    )
    report = _base_report(
        execution_id,
        question,
        status="validated",
        decision="insufficient_data" if insufficiency else "answer",
        finding=finding,
        evidence=_supporting_evidence(selected, evidence, required_codes),
        tool_trace=[result.trace for result in tool_results],
        validation=latest_validation,
        limitations=[_LIMITATIONS_FR[code] for code in required_codes],
        timing_ms={**timings, "total": (time.perf_counter() - started) * 1000},
        model=MODEL_NAME,
    )
    report.insights = [insight]
    _append_audit(
        audit_path,
        {
            "event": "report_validated",
            "execution_id": execution_id,
            "validated_llm_draft": draft.model_dump(mode="json"),
            "materialization": "LLM interpretation followed by Python quantitative description; original evidence only",
            "initial_response": first_raw,
            "corrected_response": corrected_raw,
            "timestamp": _timestamp(),
        },
    )
    return report


def analyze(question: str, context: AnalysisContext, *, client: OllamaTransport | None = None,
            use_llm: bool = True, audit_root: Path | None = None) -> BIAnalysisReport:
    """Route once from the exact question, then gate the final report, never a draft alone."""
    from .routing import route_question
    if not question.strip():
        raise ValueError("A business question is required.")
    context = context if isinstance(context, AnalysisContext) else AnalysisContext.model_validate(context)
    plan = route_question(question)
    if plan.executor == "scoped":
        from .scoped import analyze_scoped
        return analyze_scoped(question, context, plan, client=client, use_llm=use_llm, audit_root=audit_root)
    report = _analyze_legacy(question, context, client=client, use_llm=use_llm, audit_root=audit_root)
    folder = (audit_root or _default_output_root(context, investigation=plan.executor == "investigation")) / report.execution_id
    audit_path = folder / "audit.jsonl"
    _append_audit(audit_path, dict(event="analytical_plan", execution_id=report.execution_id, plan=plan.model_dump(mode="json")))
    if report.status != "failed":
        events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        bundle = load_real_bundle(context.project_root, context.dataset_version)
        if plan.executor == "investigation":
            from .decision_evaluation import verify_decision_report
            errors = verify_decision_report(report, bundle, events)["errors"]
        else:
            from .verification import verify_report
            responses = [e for e in events if e["event"] == "llm_response"]
            errors = verify_report(report, context, responses[-1]["raw_response"] if responses else None)
        _append_audit(audit_path, dict(event="final_semantic_validation", execution_id=report.execution_id,
                                       passed=not errors, errors=errors))
        if errors:
            report = _base_report(report.execution_id, question, status="failed", decision="refuse",
                                  finding="insufficient_data", errors=["final_semantic_validation:" + e for e in errors],
                                  timing_ms=report.timing_ms)
    _audit_report(report, folder)
    return report
