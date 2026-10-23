"""Agent 5 — Dashboard Generator.

À IMPLÉMENTER (coéquipier « Dashboard ») :
    À partir des KPI et des insights :
    - générer la spécification du tableau de bord (`charts`) : un graphique par KPI/insight
      pertinent, type de graphique adapté (Plotly) ;
    - générer/mettre à jour l'application Streamlit (ex. src/biflow/dashboard/app.py) qui lit
      cette spec et la base, et renseigner `app_path`.
    Le dashboard n'est « publié » par l'orchestrateur que si le run finit en statut SUCCESS
    (state.published = True) : ne rien déployer directement depuis cet agent.

Entrée  : state.outputs["kpi_semantic"], state.outputs["analyste_bi"]
Sortie  : DashboardOutput -> state.outputs["dashboard_generator"]
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.orchestrator.state import Check, PipelineState


class ChartSpec(BaseModel):
    id: str
    title: str
    chart_type: str = Field(description="bar | line | map | kpi_card | table ...")
    kpi_refs: list[str] = Field(default_factory=list)


class DashboardOutput(AgentOutput):
    title: str
    charts: list[ChartSpec]
    app_path: str | None = None


class DashboardGeneratorAgent(BaseAgent):
    name = "dashboard_generator"
    output_model = DashboardOutput

    def run(self, state: PipelineState) -> DashboardOutput:
        kpis = state.output("kpi_semantic")["kpis"]
        # STUB : une carte KPI par indicateur, pas d'application générée.
        return DashboardOutput(
            title="NYC 311 - Tableau de bord (stub)",
            charts=[ChartSpec(id=k["name"], title=k["definition"], chart_type="kpi_card",
                              kpi_refs=[k["name"]]) for k in kpis],
            is_stub=True,
        )

    def validate(self, output: DashboardOutput, state: PipelineState) -> list[Check]:
        known = {k["name"] for k in state.output("kpi_semantic")["kpis"]}
        unknown = sorted({r for c in output.charts for r in c.kpi_refs} - known)
        return [
            Check(name="charts_not_empty", passed=bool(output.charts),
                  message=f"{len(output.charts)} graphique(s)"),
            Check(name="chart_kpi_refs_exist", passed=not unknown,
                  message=f"KPI inconnus : {unknown}" if unknown else "ok"),
        ]
