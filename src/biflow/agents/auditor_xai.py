"""Agent 6 — Auditeur / XAI.

À IMPLÉMENTER (coéquipier « Auditeur/XAI ») :
    Vérifier la cohérence de bout en bout et expliquer les résultats :
    - relire audit.trace_log du run (`DbTraceLogger(engine).fetch_run(state.run_id)`) :
      étapes, essais, contrôles en warning, nombre de lignes à chaque étape ;
    - recalculer quelques KPI de façon indépendante et comparer à state.outputs["kpi_semantic"] ;
    - vérifier que chaque insight est justifié par ses `evidence` / `kpi_refs` ;
    - produire une explication lisible (`explanation`) de la façon dont les chiffres ont été obtenus.

Boucle de rétroaction :
    Pour chaque problème, ajouter un `AuditFinding(target_step=<étape fautive>, message=...,
    severity="error")`. L'orchestrateur renvoie alors le run vers l'étape la plus en amont
    signalée (UNE fois, cf. BIFLOW_MAX_FEEDBACK_ROUNDS), avec `message` disponible pour
    l'agent dans `state.corrections[<étape>]`. Si le problème persiste au 2e audit, le run
    est marqué UNRELIABLE et le dashboard n'est pas publié.
    Les findings de sévérité "warning" sont rapportés mais ne déclenchent pas de renvoi.

Entrée  : tout le state (outputs, validations, history, errors) + audit.trace_log
Sortie  : AuditOutput -> state.outputs["auditor_xai"]
"""

from __future__ import annotations

from pydantic import Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.orchestrator.state import STEP_ORDER, AuditFinding, Check, PipelineState


class AuditOutput(AgentOutput):
    approved: bool
    findings: list[AuditFinding] = Field(default_factory=list)
    explanation: str = ""


class AuditorXAIAgent(BaseAgent):
    name = "auditor_xai"
    output_model = AuditOutput

    def __init__(self, flag_step: str | None = None, persistent: bool = False) -> None:
        """Paramètres de SIMULATION pour le stub (démo/tests de la boucle de rétroaction) :

        flag_step  : étape à signaler comme fautive (None = tout approuver)
        persistent : signaler aussi après correction (=> run UNRELIABLE)
        """
        self.flag_step = flag_step
        self.persistent = persistent

    def run(self, state: PipelineState) -> AuditOutput:
        # STUB : approuve tout, sauf simulation demandée.
        if self.flag_step and (self.persistent or state.feedback_rounds == 0):
            return AuditOutput(
                approved=False,
                findings=[AuditFinding(target_step=self.flag_step,
                                       message=f"simulation : incohérence détectée dans {self.flag_step}")],
                explanation="stub : problème simulé",
                is_stub=True,
            )
        return AuditOutput(approved=True, explanation="stub : aucun contrôle réel effectué",
                           is_stub=True)

    def validate(self, output: AuditOutput, state: PipelineState) -> list[Check]:
        auditable = set(STEP_ORDER) - {self.name}
        bad = [f.target_step for f in output.findings if f.target_step not in auditable]
        blocking = any(f.severity == "error" for f in output.findings)
        return [
            Check(name="finding_targets_valid", passed=not bad,
                  message=f"étapes inconnues : {bad}" if bad else "ok"),
            Check(name="approval_consistent", passed=output.approved != blocking,
                  message="approved doit être False ssi il existe un finding 'error'"),
        ]
