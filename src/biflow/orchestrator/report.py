"""Résumé lisible d'un run (affiché par `python -m biflow.run`)."""

from __future__ import annotations

from biflow.orchestrator.state import PipelineState, RunStatus, StepStatus

_ICONS = {StepStatus.SUCCESS: "OK  ", StepStatus.FAILED: "FAIL"}
_VERDICT = {
    RunStatus.SUCCESS: "SUCCÈS — résultats publiables",
    RunStatus.FAILED: "ÉCHEC — une étape a échoué, rien n'est publié",
    RunStatus.UNRELIABLE: "NON FIABLE — l'Auditeur signale un problème non corrigé, rien n'est publié",
    RunStatus.RUNNING: "INTERROMPU",
}


def format_summary(state: PipelineState, trace_target: str = "") -> str:
    lines = [
        "=" * 80,
        f"BIFlow — run {state.run_id}",
        (f"Données : {state.dataset.file_path or state.dataset.table} "
        f"[{state.dataset.start_date or '…'} -> {state.dataset.end_date or '…'}]"),
        "-" * 80,
        f"{'étape':<30}{'statut':<8}{'essais':>7}{'durée':>10}  validation",
    ]
    for run in state.history:
        output = state.outputs.get(run.step, {})
        validation = state.validations.get(run.step)
        if run.status == StepStatus.FAILED:
            vtxt = run.message[:40]
        elif validation:
            warns = sum(1 for c in validation.checks if not c.passed)
            vtxt = f"{len(validation.checks)} contrôles" + (f", {warns} warning(s)" if warns else "")
        else:
            vtxt = ""
        stub = " [stub]" if output.get("is_stub") and run.status == StepStatus.SUCCESS else ""
        label = run.step + (f" (tour {run.feedback_round})" if run.feedback_round else "")
        lines.append(f"{label:<30}{_ICONS[run.status]:<8}{run.attempts:>7}"
                     f"{run.duration_s:>9.2f}s  {vtxt}{stub}")
    lines.append("-" * 80)
    kpi = state.outputs.get("kpi_semantic", {})
    if kpi.get("kpis"):
        lines.append("KPI :")
        lines += [f"  - {k['name']:<26}{k['value']:>14,.2f}  ({k['unit']})" for k in kpi["kpis"]]
    for a in kpi.get("answers", []):
        lines.append(f"Question [{a['mode']}] : {a['question']}")
        if a.get("refusal"):
            lines.append(f"    -> {a['refusal']}")
            continue
        result = a["error"] or (a["rows"][:3] if a["rows"] else "aucune ligne")
        lines += [f"    SQL : {a['sql']}", f"    -> {result}"]
    analysis = state.outputs.get("analyste_bi", {})
    if analysis.get("insights"):
        lines.append("Insights :")
        lines += [f"  - {i['text']}" for i in analysis["insights"]]
    audit = state.outputs.get("auditor_xai", {})
    if audit.get("explanation"):
        lines.append("Audit :")
        lines += [f"  {line}" for line in audit["explanation"].splitlines()]
    if state.findings:
        lines.append(f"Findings de l'Auditeur (tours de rétroaction : {state.feedback_rounds}) :")
        lines += [f"  - [{f.severity}] {f.target_step} : {f.message}" for f in state.findings]
    if state.errors:
        lines.append(f"Erreurs ({len(state.errors)}) :")
        lines += [f"  - {e.step} essai {e.attempt} : {e.error_type}: {e.message[:80]}"
                  for e in state.errors]
    lines.append(f"Statut final : {_VERDICT[state.status]}")
    lines.append(f"Publié : {'oui' if state.published else 'non'}")
    if trace_target:
        lines.append(f"Trace : {trace_target}")
    if state.workspace:
        lines.append(f"Artefacts : {state.workspace}")
    if state.published:
        lines.append("Dashboard : streamlit run src/biflow/dashboard/app.py")
    lines.append("=" * 80)
    return "\n".join(lines)
