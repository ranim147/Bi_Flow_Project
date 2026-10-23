"""Agent 4 — Analyste BI.

À IMPLÉMENTER (coéquipier « Analyste BI ») :
    À partir des KPI (`state.output("kpi_semantic")["kpis"]`) et des données nettoyées :
    - produire des insights : tendances, comparaisons entre boroughs, types de plaintes
      hors SLA, anomalies temporelles... (éventuellement avec un LLM) ;
    - chaque insight cite les KPI sur lesquels il s'appuie (`kpi_refs`) et ses preuves
      chiffrées (`evidence`) : c'est ce que l'Auditeur/XAI vérifiera.

Entrée  : state.outputs["kpi_semantic"] (+ table de state.outputs["quality_etl"]["output_table"])
Sortie  : AnalysisOutput -> state.outputs["analyste_bi"]
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.orchestrator.state import Check, PipelineState


class Insight(BaseModel):
    title: str
    text: str
    kpi_refs: list[str] = Field(default_factory=list, description="noms de KPI de kpi_semantic")
    evidence: dict[str, Any] = Field(default_factory=dict)


class AnalysisOutput(AgentOutput):
    insights: list[Insight]


class AnalysteBIAgent(BaseAgent):
    name = "analyste_bi"
    output_model = AnalysisOutput

    def run(self, state: PipelineState) -> AnalysisOutput:
        kpis = {k["name"]: k["value"] for k in state.output("kpi_semantic")["kpis"]}
        # STUB : un insight factice basé sur un KPI.
        return AnalysisOutput(
            insights=[Insight(
                title="Respect des délais (stub)",
                text=f"{kpis.get('sla_compliance_rate', 0):.0f} % des demandes respectent le SLA.",
                kpi_refs=["sla_compliance_rate"],
                evidence={"sla_compliance_rate": kpis.get("sla_compliance_rate")},
            )],
            is_stub=True,
        )

    def validate(self, output: AnalysisOutput, state: PipelineState) -> list[Check]:
        known = {k["name"] for k in state.output("kpi_semantic")["kpis"]}
        unknown = sorted({r for i in output.insights for r in i.kpi_refs} - known)
        return [
            Check(name="insights_not_empty", passed=bool(output.insights),
                  message=f"{len(output.insights)} insight(s)"),
            Check(name="kpi_refs_exist", passed=not unknown,
                  message=f"KPI inconnus : {unknown}" if unknown else "ok"),
        ]
