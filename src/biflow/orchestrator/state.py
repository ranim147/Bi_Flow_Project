"""State partagé du pipeline (pydantic), transmis de nœud en nœud par LangGraph.

Règle : un agent ne modifie JAMAIS le state directement. Il retourne son objet de sortie
(sous-classe de `AgentOutput`) ; `BaseAgent.execute` se charge de l'écrire dans
`state.outputs[<nom_agent>]` (sous forme de dict JSON-sérialisable).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# Ordre canonique des étapes. Les noms servent de clé dans `outputs`, de nom de nœud
# LangGraph et de valeur `step` dans audit.trace_log.
STEP_ORDER: list[str] = [
    "data_profiler",
    "quality_etl",
    "kpi_semantic",
    "analyste_bi",
    "dashboard_generator",
    "auditor_xai",
]


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"          # tout est passé, l'Auditeur approuve -> publiable
    FAILED = "failed"            # une étape a échoué après tous les essais
    UNRELIABLE = "unreliable"    # l'Auditeur signale encore un problème après correction


class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class DatasetRef(BaseModel):
    """Référence vers les données d'entrée (on ne met jamais de DataFrame dans le state)."""

    table: str = "raw.nyc311"
    start_date: str | None = None   # AAAA-MM-JJ inclus
    end_date: str | None = None     # AAAA-MM-JJ inclus
    file_path: str | None = None    # alternative : extrait parquet/CSV (prioritaire si défini)


class Check(BaseModel):
    """Un contrôle de validation élémentaire."""

    name: str
    passed: bool
    message: str = ""
    severity: Literal["error", "warning"] = "error"


class ValidationResult(BaseModel):
    step: str
    checks: list[Check] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Faux si au moins un contrôle bloquant (severity=error) échoue."""
        return all(c.passed for c in self.checks if c.severity == "error")


class StepError(BaseModel):
    step: str
    attempt: int
    error_type: str
    message: str


class StepRun(BaseModel):
    """Historique : une entrée par exécution (réussie ou non) d'une étape."""

    step: str
    status: StepStatus
    attempts: int
    feedback_round: int = 0  # > 0 : exécution relancée par l'Auditeur
    started_at: datetime
    ended_at: datetime
    message: str = ""

    @property
    def duration_s(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


class AuditFinding(BaseModel):
    """Problème détecté par l'Auditeur, à renvoyer vers l'étape fautive."""

    target_step: str
    message: str
    severity: Literal["error", "warning"] = "error"


class PipelineState(BaseModel):
    run_id: str
    dataset: DatasetRef = Field(default_factory=DatasetRef)
    status: RunStatus = RunStatus.RUNNING
    workspace: str | None = Field(
        None, description="Dossier des artefacts du run : instantanés parquet, spec du dashboard, run.json")
    questions: list[str] = Field(default_factory=list, description="Questions métier à traduire en SQL")

    # Sorties par agent : outputs["kpi_semantic"] = KPIOutput(...).model_dump(mode="json")
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validations: dict[str, ValidationResult] = Field(default_factory=dict)
    errors: list[StepError] = Field(default_factory=list)
    history: list[StepRun] = Field(default_factory=list)

    # Boucle de rétroaction de l'Auditeur
    corrections: dict[str, str] = Field(
        default_factory=dict,
        description="Consignes de l'Auditeur par étape, à lire par l'agent quand il est relancé",
    )
    feedback_rounds: int = 0
    findings: list[AuditFinding] = Field(default_factory=list)

    # Routage (rempli par les nœuds, lu par le routeur du graphe)
    next_step: str | None = None
    published: bool = False

    def require_workspace(self) -> str:
        if not self.workspace:
            raise ValueError("state.workspace non défini (run_pipeline le fixe automatiquement)")
        return self.workspace

    def output(self, step: str) -> dict[str, Any]:
        """Sortie d'une étape précédente (KeyError explicite si absente)."""
        if step not in self.outputs:
            raise KeyError(f"Sortie de '{step}' absente du state (étape non exécutée ?)")
        return self.outputs[step]
