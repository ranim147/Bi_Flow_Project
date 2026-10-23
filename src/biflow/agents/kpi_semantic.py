"""Agent 3 — KPI & couche sémantique.

À IMPLÉMENTER (coéquipier « KPI & Semantic ») :
    À partir de la table nettoyée (`state.output("quality_etl")["output_table"]`) :
    - définir et calculer les KPI (volume, délai médian de résolution, taux de respect SLA
      via ref.sla_targets, taux de clôture, demandes pour 10 000 habitants via ref.boroughs...) ;
    - décrire la couche sémantique (`semantic_model`) : mesures, dimensions, définitions,
      pour que l'Analyste et le Dashboard utilisent les mêmes définitions.
    ⚠ ref.sla_targets contient des valeurs PLACEHOLDER (is_placeholder = true) à valider.

Unités autorisées et plages contrôlées automatiquement :
    count >= 0 | hours >= 0 | percent [0, 100] | ratio [0, 1] | per_10k >= 0

Entrée  : state.outputs["quality_etl"]
Sortie  : KPIOutput -> state.outputs["kpi_semantic"]
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.orchestrator.state import Check, PipelineState
from biflow.orchestrator.validation import check_in_range

KPI_RANGES: dict[str, tuple[float | None, float | None]] = {
    "count": (0, None),
    "hours": (0, None),
    "percent": (0, 100),
    "ratio": (0, 1),
    "per_10k": (0, None),
}


class KPIValue(BaseModel):
    name: str
    value: float
    unit: Literal["count", "hours", "percent", "ratio", "per_10k"]
    definition: str
    dimensions: dict[str, str] = Field(default_factory=dict, description="ex. {'borough': 'BRONX'}")


class KPIOutput(AgentOutput):
    kpis: list[KPIValue]
    semantic_model: dict[str, Any] = Field(default_factory=dict)


class KPISemanticAgent(BaseAgent):
    name = "kpi_semantic"
    output_model = KPIOutput

    def run(self, state: PipelineState) -> KPIOutput:
        rows = state.output("quality_etl")["rows_out"]
        # STUB : KPI factices.
        return KPIOutput(
            kpis=[
                KPIValue(name="total_requests", value=rows, unit="count",
                         definition="Nombre de demandes après nettoyage"),
                KPIValue(name="median_resolution_hours", value=12.5, unit="hours",
                         definition="Médiane de closed_date - created_date"),
                KPIValue(name="sla_compliance_rate", value=78.0, unit="percent",
                         definition="% de demandes clôturées sous le délai cible"),
                KPIValue(name="closure_rate", value=0.92, unit="ratio",
                         definition="Demandes clôturées / demandes totales"),
            ],
            semantic_model={"measures": ["total_requests"], "dimensions": ["borough", "complaint_type"]},
            input_rows=rows,
            is_stub=True,
        )

    def validate(self, output: KPIOutput, state: PipelineState) -> list[Check]:
        checks = [Check(name="kpis_not_empty", passed=bool(output.kpis), message=f"{len(output.kpis)} KPI")]
        for kpi in output.kpis:
            low, high = KPI_RANGES[kpi.unit]
            checks.append(check_in_range(f"kpi:{kpi.name}", kpi.value, low, high))
        return checks
