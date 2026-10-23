"""Classe de base commune à tous les agents BIFlow.

Un agent = une sous-classe de `BaseAgent` qui définit :
- `name`          : identifiant de l'étape (clé dans STEP_ORDER / state.outputs / trace_log)
- `output_model`  : modèle pydantic de sa sortie (le CONTRAT avec les agents suivants)
- `run(state)`    : la logique métier -> retourne une instance de `output_model`
- `validate(...)` : (optionnel) contrôles métier sur la sortie

Tout le reste est automatique via `execute()` (appelé par l'orchestrateur) :
essais multiples, chronométrage, validation, écriture dans audit.trace_log,
mise à jour du state. Un agent ne doit donc PAS gérer lui-même les retries ni la trace.
"""

from __future__ import annotations

import logging
import time
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel

from biflow.orchestrator.state import (
    Check,
    PipelineState,
    RunStatus,
    StepError,
    StepRun,
    StepStatus,
    ValidationResult,
)
from biflow.orchestrator.tracing import MemoryTraceLogger, TraceLogger, TraceRecord

log = logging.getLogger(__name__)


class AgentOutput(BaseModel):
    """Champs communs à toutes les sorties d'agent (repris dans audit.trace_log)."""

    input_rows: int | None = None
    output_rows: int | None = None
    notes: list[str] = []
    is_stub: bool = False  # True tant que l'agent n'est pas implémenté


class StepValidationError(Exception):
    def __init__(self, result: ValidationResult) -> None:
        failed = [f"{c.name}: {c.message}" for c in result.checks
                  if not c.passed and c.severity == "error"]
        super().__init__("validation échouée — " + "; ".join(failed))
        self.result = result


@dataclass
class RunContext:
    """Dépendances d'exécution (non sérialisées dans le state)."""

    trace_logger: TraceLogger = field(default_factory=MemoryTraceLogger)
    max_retries: int = 2                 # nombre de NOUVEAUX essais après le premier échec
    retry_delay_seconds: float = 1.0     # délai * numéro d'essai (backoff linéaire)
    max_feedback_rounds: int = 1         # renvois de l'Auditeur vers une étape fautive
    sleep: Callable[[float], None] = time.sleep


def _now() -> datetime:
    return datetime.now(UTC)


class BaseAgent(ABC):
    name: ClassVar[str]
    output_model: ClassVar[type[AgentOutput]]

    @abstractmethod
    def run(self, state: PipelineState) -> AgentOutput:
        """Logique de l'agent. Lire les entrées dans `state`, retourner `output_model`.

        Lever une exception en cas d'erreur : l'orchestrateur relancera l'étape.
        """

    def validate(self, output: AgentOutput, state: PipelineState) -> list[Check]:
        """Contrôles métier sur la sortie. Par défaut : aucun (le schéma pydantic suffit)."""
        return []

    # ------------------------------------------------------------------ orchestrateur
    def execute(self, state: PipelineState, ctx: RunContext) -> dict[str, Any]:
        """Exécute l'étape avec retries + validation + trace. Retourne les mises à jour du state."""
        errors = list(state.errors)
        attempts = ctx.max_retries + 1
        step_started = _now()
        correction = state.corrections.get(self.name)

        for attempt in range(1, attempts + 1):
            started = _now()
            try:
                output = self.run(state)
                if not isinstance(output, self.output_model):
                    output = self.output_model.model_validate(output)
                validation = ValidationResult(step=self.name, checks=self.validate(output, state))
                if not validation.passed:
                    raise StepValidationError(validation)
            except Exception as exc:  # noqa: BLE001 — toute erreur d'agent est gérée ici
                final = attempt == attempts
                error = StepError(step=self.name, attempt=attempt,
                                  error_type=type(exc).__name__, message=str(exc))
                errors.append(error)
                details: dict[str, Any] = {
                    "error_type": error.error_type,
                    "traceback": traceback.format_exc(limit=5),
                }
                if isinstance(exc, StepValidationError):
                    details["validation"] = [c.model_dump() for c in exc.result.checks]
                ctx.trace_logger.log(TraceRecord(
                    run_id=state.run_id, step=self.name, attempt=attempt,
                    status="failed" if final else "retry", started_at=started,
                    ended_at=_now(), message=str(exc)[:2000], details=details,
                ))
                log.warning("[%s] essai %d/%d échoué : %s", self.name, attempt, attempts, exc)
                if final:
                    return {
                        "errors": errors,
                        "status": RunStatus.FAILED,
                        "history": [*state.history, StepRun(
                            step=self.name, status=StepStatus.FAILED, attempts=attempt,
                            feedback_round=state.feedback_rounds,
                            started_at=step_started, ended_at=_now(), message=str(exc)[:500])],
                    }
                ctx.sleep(ctx.retry_delay_seconds * attempt)
                continue

            ctx.trace_logger.log(TraceRecord(
                run_id=state.run_id, step=self.name, attempt=attempt, status="success",
                started_at=started, ended_at=_now(),
                input_rows=output.input_rows, output_rows=output.output_rows,
                message="; ".join(output.notes)[:2000] or None,
                details={
                    "is_stub": output.is_stub,
                    "feedback_round": state.feedback_rounds,
                    "correction_applied": correction,
                    "validation": [c.model_dump() for c in validation.checks],
                },
            ))
            log.info("[%s] OK (essai %d)%s", self.name, attempt, " [stub]" if output.is_stub else "")
            return {
                "outputs": {**state.outputs, self.name: output.model_dump(mode="json")},
                "validations": {**state.validations, self.name: validation},
                "errors": errors,
                "history": [*state.history, StepRun(
                    step=self.name, status=StepStatus.SUCCESS, attempts=attempt,
                    feedback_round=state.feedback_rounds,
                    started_at=step_started, ended_at=_now())],
            }
        raise RuntimeError("unreachable")
