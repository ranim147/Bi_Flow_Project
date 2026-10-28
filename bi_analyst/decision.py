"""End-to-end investigation branch of analyze(), sharing analytics and audit contracts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any

import pandas as pd
from benchmark_llm.runner import OllamaClient, OllamaFailure
from .analytics import load_real_bundle
from .decision_tools import build_decision_analysis, LIMIT_TEXT
from .investigation import materialize_plan, plan_payload, policy_for, validate_plan
from .models import AnalysisContext, BIAnalysisReport, BIInsight, DecisionDraft, ValidationResult
from .tools import execute_tool


def analyze_decision(question: str, context: AnalysisContext, *, client=None,
                     use_llm=True, audit_root: Path | None = None, _synthetic_bundle=None) -> BIAnalysisReport:
    from .orchestrator import _append_audit, _audit_report, _base_report, _timestamp, _default_output_root
    if not question.strip():
        raise ValueError("A business question is required.")
    context = context if isinstance(context, AnalysisContext) else AnalysisContext.model_validate(context)
    if _synthetic_bundle is None:
        from .routing import route_question
        plan = route_question(question)
        if plan.executor == "scoped":
            from .scoped import analyze_scoped
            return analyze_scoped(question, context, plan, client=client, use_llm=use_llm, audit_root=audit_root)
    started = time.perf_counter()
    execution_id = uuid.uuid4().hex
    audit_dir = (audit_root or _default_output_root(context, investigation=True)) / execution_id
    audit_path = audit_dir / "audit.jsonl"
    kind = "synthetic" if _synthetic_bundle is not None else "real"
    _append_audit(audit_path, dict(event="request_received", question=question, data_kind=kind,
                                 execution_id=execution_id, timestamp=_timestamp()))
    timings: dict[str, float] = {}
    local_client = client or OllamaClient(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"), timeout=180)

    def failed(message):
        report = _base_report(execution_id, question, status="failed", decision="refuse", finding="insufficient_data",
                              errors=[message], timing_ms={**timings, "total": (time.perf_counter() - started) * 1000})
        report.contract_version, report.data_kind = "biflow-bi-analyst-v2", kind
        _append_audit(audit_path, dict(event="execution_failed", error=message, timestamp=_timestamp()))
        _audit_report(report, audit_dir)
        return report

    if use_llm:
        before = time.perf_counter()
        try:
            available = local_client.installed()
            if "granite3.3:2b" not in available:
                return failed("required_model_not_installed:granite3.3:2b")
            version = local_client.call("version", timeout=5)
            _append_audit(audit_path, dict(event="model_preflight_succeeded", model="granite3.3:2b",
                                         model_digest=available["granite3.3:2b"].get("digest"), server_version=version.get("version")))
        except OllamaFailure as exc:
            return failed(f"ollama_preflight_{exc.status}:{str(exc)[:250]}")
        timings["model_preflight"] = (time.perf_counter() - before) * 1000
    before = time.perf_counter()
    try:
        bundle = _synthetic_bundle if _synthetic_bundle is not None else load_real_bundle(context.project_root, context.dataset_version)
        timings["data_validation"] = (time.perf_counter() - before) * 1000
        if _synthetic_bundle is None:
            tool = execute_tool("investigate_degradation", question, context, bundle)
            evidence, details, trace = tool.evidence, tool.result, [tool.trace]
            _append_audit(audit_path, dict(event="tool_executed", trace=tool.trace.model_dump(mode="json"),
                                         evidence=[fact.model_dump(mode="json") for fact in evidence]))
        else:
            before = time.perf_counter()
            evidence, details, status = build_decision_analysis(question, bundle)
            from .models import ToolTrace
            trace = [ToolTrace(tool="investigate_degradation", parameters={"question": question}, status=status,
                               started_at=_timestamp(), elapsed_ms=(time.perf_counter() - before) * 1000,
                               evidence_ids=[fact.fact_id for fact in evidence], detail="EXPLICIT SYNTHETIC FIXTURE")]
            _append_audit(audit_path, dict(event="tool_executed", trace=trace[0].model_dump(mode="json"),
                                         evidence=[fact.model_dump(mode="json") for fact in evidence]))
        timings["analytics"] = sum(item.elapsed_ms for item in trace)
    except (ValueError, OSError, KeyError) as exc:
        return failed(f"data_or_analysis_failed:{type(exc).__name__}:{str(exc)[:400]}")
    (audit_dir / "analysis.json").write_text(json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    facts_by_id = {fact.fact_id: fact for fact in evidence}
    plans, all_errors, validations = [], [], []
    accepted_count = 0
    for index, problem in enumerate(details["problems"]):
        policy = policy_for(problem, details["periods"])
        draft = None
        validation = ValidationResult(status="not_run", json_valid=False, schema_valid=False, evidence_valid=False,
                                      numerical_claims_valid=False, causal_claims_clear=False, errors=[])
        if use_llm:
            for attempt in range(2):
                public_facts = [{key: getattr(facts_by_id[fact_id], key) for key in ("fact_id", "metric", "scope", "value", "unit", "n")}
                                for fact_id in policy.problem["evidence_ids"]]
                payload = plan_payload(question, policy, public_facts, validation.errors if attempt else None)
                _append_audit(audit_path, dict(event="llm_request", problem_id=problem["problem_id"], attempt=attempt + 1, payload=payload))
                before = time.perf_counter()
                try:
                    response = local_client.call("chat", payload=payload, timeout=180)
                except OllamaFailure as exc:
                    timings[f"problem_{index}_inference_{attempt + 1}"] = (time.perf_counter() - before) * 1000
                    all_errors.extend(validation.errors)
                    all_errors.append(f"inference_{exc.status}:{str(exc)[:200]}")
                    _append_audit(audit_path, dict(event="inference_failed", problem_id=problem["problem_id"], attempt=attempt + 1, error=str(exc)))
                    break
                elapsed = (time.perf_counter() - before) * 1000
                timings[f"problem_{index}_inference_{attempt + 1}"] = elapsed
                content = response.get("message", {})
                raw = content.get("content", "") if isinstance(content, dict) else ""
                raw = raw if isinstance(raw, str) else ""
                _append_audit(audit_path, dict(event="llm_response", problem_id=problem["problem_id"], attempt=attempt + 1,
                                             raw_response=raw, elapsed_ms=elapsed,
                                             response_model=response.get("model"), done=response.get("done"), done_reason=response.get("done_reason"),
                                             ollama_metrics={key: response[key] for key in ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration") if key in response}))
                before = time.perf_counter()
                draft, validation = validate_plan(raw, policy)
                for key in ("load_duration", "prompt_eval_duration", "eval_duration"):
                    if key in response:
                        timings[f"problem_{index}_{key}_{attempt + 1}"] = response[key] / 1_000_000
                if response.get("model", "granite3.3:2b") != "granite3.3:2b":
                    validation.errors.append("unexpected_model_identity")
                if response.get("done") is False or response.get("done_reason") == "length":
                    validation.errors.append("incomplete_model_response")
                if validation.errors:
                    validation.status = "rejected"
                timings[f"problem_{index}_validation_{attempt + 1}"] = (time.perf_counter() - before) * 1000
                (audit_dir / f"{problem['problem_id']}_attempt_{attempt + 1}.txt").write_text(raw, encoding="utf-8")
                _append_audit(audit_path, dict(event="validation", problem_id=problem["problem_id"], attempt=attempt + 1,
                                             result=validation.model_dump(mode="json")))
                if validation.status == "validated":
                    break
        accepted = use_llm and draft is not None and validation.status == "validated"
        if accepted:
            accepted_count += 1
        else:
            all_errors.extend(validation.errors)
            draft = DecisionDraft.model_validate(policy.example())
            _append_audit(audit_path, dict(event="deterministic_fallback" if use_llm else "deterministic_baseline", problem_id=problem["problem_id"]))
        plans.append(materialize_plan(draft, policy, validated=accepted))
        validations.append(validation)
    status = "validated" if accepted_count == len(plans) and use_llm else "fallback"
    sufficient = any(problem["kind"] not in {"small_scope", "insufficient_history"} for problem in details["problems"])
    combined_errors = sorted(set(all_errors))
    final_validation = ValidationResult(
        status="validated" if status == "validated" else "rejected" if use_llm else "not_run",
        **{key: all(getattr(value, key) for value in validations) for key in ("json_valid", "schema_valid", "evidence_valid", "numerical_claims_valid", "causal_claims_clear")},
        errors=combined_errors)
    report = BIAnalysisReport(contract_version="biflow-bi-analyst-v2", execution_id=execution_id,
                              generated_at=_timestamp(), question=question, model="granite3.3:2b" if use_llm else None,
                              data_kind=kind, status=status, decision="answer" if sufficient else "insufficient_data",
                              finding=details["problems"][0]["kind"], decision_support=plans,
                              insights=[BIInsight(insight_id=f"{execution_id}-{plan.problem_id}", category="comparison",
                                                  finding=problem["kind"], insight=plan.problem + " Périmètre : " + json.dumps(plan.priority_scope.filters, ensure_ascii=False) + "; périodes : " + str(details["periods"]),
                                                  recommendation=plan.recommended_actions[0].action + " Cible : " + json.dumps(plan.priority_scope.filters, ensure_ascii=False),
                                                  evidence_fact_ids=plan.evidence_ids, limitation_codes=problem["limitation_codes"])
                                        for plan, problem in zip(plans, details["problems"])],
                              evidence=evidence, limitations=sorted({LIMIT_TEXT[code] for problem in details["problems"] for code in problem["limitation_codes"]}),
                              source_refs=sorted({fact.source for fact in evidence}), validation=final_validation,
                              tool_trace=trace, errors=combined_errors, timing_ms={**timings, "total": (time.perf_counter() - started) * 1000})
    from .decision_evaluation import verify_decision_report
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    semantic_errors = verify_decision_report(report, bundle, events)["errors"]
    _append_audit(audit_path, dict(event="final_semantic_validation", execution_id=execution_id,
                                 passed=not semantic_errors, errors=semantic_errors))
    if semantic_errors:
        return failed("final_semantic_validation:" + ",".join(semantic_errors))
    _append_audit(audit_path, dict(event="report_completed", status=status, accepted_problem_count=accepted_count,
                                 problem_count=len(plans), timestamp=_timestamp()))
    _audit_report(report, audit_dir)
    return report


def analyze_synthetic_decision(question: str, frame: pd.DataFrame, metadata: dict[str, Any],
                               context: AnalysisContext, **kwargs) -> BIAnalysisReport:
    """Explicit test API. Never used as a fallback for absent real inputs."""
    source = "synthetic://decision-fixture/requests.csv"
    digest = hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()
    bundle = dict(kind="synthetic", clean=frame.copy(), metadata=metadata,
                  source_paths={"clean": source}, hashes={source: digest})
    return analyze_decision(question, context, _synthetic_bundle=bundle, **kwargs)
