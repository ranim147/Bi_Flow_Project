"""Scoped branch of the existing orchestrator: Python plan, Granite language, final gate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from benchmark_llm.runner import OllamaClient, OllamaFailure
from .analytics import load_real_bundle
from .draft import DraftPolicy, draft_payload, validate_draft
from .models import AnalyticalPlan, BIAnalysisReport, BIInsight, LLMDraft, ValidationResult
from .scoped_analytics import LIMITS, execute_plan


def scoped_policy(details, facts):
    refused = details["decision"] == "insufficient_data"
    if refused:
        insights = ("Les informations nécessaires à la demande centrale sont insuffisantes; une investigation documentée est nécessaire.",)
        recommendations = ("Recueillir les données nécessaires et tester les hypothèses avant toute conclusion ou estimation.",)
    elif "period_comparison" in details:
        insights = ("La comparaison temporelle porte sur le KPI et les mois verrouillés par Python; les écarts restent descriptifs.",)
        recommendations = ("Examiner les segments éligibles et leurs étapes de traitement sur les périodes retenues.",
                           "Contrôler la composition des dossiers et les temps d'attente dans les segments documentés.")
    else:
        insights = ("Le classement et les comparaisons portent sur le KPI demandé et le périmètre verrouillé par Python.",)
        recommendations = ("Examiner les étapes de traitement et la composition des dossiers dans les segments éligibles du périmètre retenu.",
                           "Vérifier les affectations et la complexité des dossiers dans les segments documentés avant toute décision.")
    return DraftPolicy(details["finding"], tuple(details["primary_evidence_ids"]),
                       tuple(details["limitations"]), insights, recommendations)


def materialize_sections(execution_id, details, draft=None):
    output = []
    for index, section in enumerate(details["sections"]):
        output.append(BIInsight(insight_id=execution_id + "-" + section["key"], category=section["category"],
            finding=details["finding"], insight=(draft.insight + " " if draft and index == 0 else "") + section["text"],
            recommendation=(draft.recommendation + " " if draft and index == 0 else "") + section["recommendation"],
            evidence_fact_ids=section["evidence_ids"], limitation_codes=details["limitations"]))
    return output


def analyze_scoped(question, context, plan, *, client=None, use_llm=True, audit_root: Path | None = None):
    from .orchestrator import MODEL_NAME, AGENT_OPTIONS, _append_audit, _audit_report, _base_report, _timestamp, _default_output_root
    started = time.perf_counter()
    execution_id = uuid.uuid4().hex
    folder = (audit_root or _default_output_root(context)) / execution_id
    audit = folder / "audit.jsonl"
    log = lambda event, **data: _append_audit(audit, dict(event=event, execution_id=execution_id, timestamp=_timestamp(), **data))
    log("request_received", question=question, data_kind=context.data_kind)
    log("analytical_plan", plan=plan.model_dump(mode="json"))
    timings, errors = {}, []
    local_client = client or OllamaClient(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"), timeout=180)

    def failed(error):
        log("execution_failed", error=error)
        report = _base_report(execution_id, question, status="failed", decision="refuse", finding="insufficient_data",
                              errors=[error], timing_ms={**timings, "total": (time.perf_counter() - started) * 1000})
        report.contract_version = "biflow-bi-analyst-v2"
        report.analytical_plan = plan
        _audit_report(report, folder)
        return report

    if use_llm:
        before = time.perf_counter()
        try:
            installed = local_client.installed()
            if MODEL_NAME not in installed:
                return failed("required_model_not_installed:" + MODEL_NAME)
            version = local_client.call("version", timeout=5)
            log("model_preflight_succeeded", model=MODEL_NAME, model_digest=installed[MODEL_NAME].get("digest"), server_version=version.get("version"))
        except OllamaFailure as exc:
            return failed(f"ollama_preflight_{exc.status}:{exc}")
        timings["model_preflight"] = (time.perf_counter() - before) * 1000
    try:
        before = time.perf_counter()
        bundle = load_real_bundle(context.project_root, context.dataset_version)
        timings["data_validation"] = (time.perf_counter() - before) * 1000
        results, details = execute_plan(question, plan, bundle)
    except (ValueError, OSError, KeyError) as exc:
        return failed(f"data_or_analysis_failed:{type(exc).__name__}:{exc}")
    facts = [f for result in results for f in result.evidence]
    for result in results:
        log("tool_executed", trace=result.trace.model_dump(mode="json"), result=result.result)
    timings["analytics"] = sum(result.trace.elapsed_ms for result in results)
    (folder / "analysis.json").write_text(json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    policy = scoped_policy(details, facts)
    draft, accepted = None, False
    validation = ValidationResult(status="not_run", json_valid=False, schema_valid=False, evidence_valid=False,
                                  numerical_claims_valid=False, causal_claims_clear=False)
    if use_llm:
        for attempt in range(1, 3):
            payload = draft_payload(question, policy, AGENT_OPTIONS, validation.errors if attempt > 1 else None)
            # The short prompt includes actual facts, validated plan, mandatory limits and conclusion.
            content = json.loads(payload["messages"][-1]["content"])
            content.update(analytical_plan=details["plan"], expected_conclusion=details["decision"],
                           facts=[{k: getattr(f, k) for k in ("fact_id", "metric", "scope", "value", "unit", "n", "reference_period")}
                                  for f in facts if f.fact_id in policy.evidence_ids],
                           mandatory_limitations={code: LIMITS[code] for code in policy.limitation_codes},
                           example=policy.example())
            payload["messages"][-1]["content"] = json.dumps(content, ensure_ascii=False)
            log("llm_request", attempt=attempt, payload=payload)
            before = time.perf_counter()
            try:
                response = local_client.call("chat", payload=payload, timeout=180)
            except OllamaFailure as exc:
                timings[f"inference_attempt_{attempt}"] = (time.perf_counter() - before) * 1000
                errors.append(f"inference_{exc.status}:{exc}")
                log("inference_failed", attempt=attempt, error=errors[-1])
                break
            timings[f"inference_attempt_{attempt}"] = (time.perf_counter() - before) * 1000
            raw = response.get("message", {}).get("content", "")
            log("llm_response", attempt=attempt, raw_response=raw, response_model=response.get("model"),
                done=response.get("done"), done_reason=response.get("done_reason"), elapsed_ms=timings[f"inference_attempt_{attempt}"],
                ollama_metrics={key: response[key] for key in ("load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration") if key in response})
            (folder / f"attempt_{attempt}.txt").write_text(raw, encoding="utf-8")
            draft, validation = validate_draft(raw, policy)
            if response.get("model") != MODEL_NAME:
                validation.errors.append("unexpected_model_identity")
            if response.get("done") is False or response.get("done_reason") == "length":
                validation.errors.append("incomplete_model_response")
            if validation.errors:
                validation.status = "rejected"
            log("validation", attempt=attempt, result=validation.model_dump(mode="json"))
            if validation.status == "validated":
                accepted = True
                break
            errors.extend(validation.errors)
    if not accepted:
        draft = None
        log("deterministic_fallback" if use_llm else "deterministic_baseline", errors=errors)
    report = BIAnalysisReport(contract_version="biflow-bi-analyst-v2", execution_id=execution_id,
        generated_at=_timestamp(), question=question, model=MODEL_NAME if use_llm else None,
        status="validated" if accepted else "fallback", decision=details["decision"], finding=details["finding"],
        insights=materialize_sections(execution_id, details, draft), evidence=facts,
        limitations=[LIMITS[code] for code in details["limitations"]], source_refs=sorted({f.source for f in facts}),
        validation=validation, tool_trace=[r.trace for r in results], errors=[] if accepted else sorted(set(errors)),
        analytical_plan=AnalyticalPlan.model_validate(details["plan"]),
        timing_ms={**timings, "total": (time.perf_counter() - started) * 1000})
    # This check runs on every final artifact, including a repair and a Python fallback.
    from .validation import validate_scoped_report
    semantic_errors = validate_scoped_report(report, bundle)
    log("final_semantic_validation", passed=not semantic_errors, errors=semantic_errors)
    if semantic_errors:
        return failed("final_semantic_validation:" + ",".join(semantic_errors))
    log("report_completed", status=report.status, decision=report.decision, fallback=not accepted)
    _audit_report(report, folder)
    return report
