"""Agent 5 — Dashboard Generator (code classique : règles de choix des graphiques).

À partir des KPI et des insights, produit la SPÉCIFICATION du tableau de bord
(`dashboard_spec.json` dans le dossier du run) : un graphique par KPI / ventilation, type
choisi par règles, avec les valeurs à afficher et les requêtes d'origine (`query_ids`).
L'application Streamlit (`src/biflow/dashboard/app.py`) n'affiche QUE cette spec, après avoir
vérifié son empreinte : chaque chiffre affiché est relié à sa requête.
Le dashboard n'est « publié » que si le run finit en SUCCESS (state.published = True) :
cet agent ne publie rien lui-même.

Règles de choix :
    KPI global -> kpi_card | dimension temporelle -> line (+ anomalies)
    dimension catégorielle <= 6 modalités -> bar, sinon bar_h (top 15) | questions -> table

Entrée  : state.outputs["kpi_semantic"], state.outputs["analyste_bi"]
Sortie  : DashboardOutput -> state.outputs["dashboard_generator"]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.common.snapshot import file_hash
from biflow.orchestrator.state import Check, PipelineState

APP_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
SPEC_FILE = "dashboard_spec.json"
TEMPORAL_DIMENSIONS = {"created_day"}
MAX_BAR_CATEGORIES = 6
MAX_ROWS_DISPLAYED = 15
NUMERIC_CHARTS = {"kpi_card", "bar", "bar_h", "line"}


class ChartSpec(BaseModel):
    id: str
    title: str
    chart_type: str = Field(description="bar | bar_h | line | kpi_card | table | text")
    kpi_refs: list[str] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list, description="requêtes d'origine (kpi_semantic.queries)")
    unit: str | None = None
    value: float | None = Field(None, description="valeur affichée (kpi_card)")
    data: list[dict[str, Any]] = Field(default_factory=list, description="données affichées")


class DashboardOutput(AgentOutput):
    title: str
    charts: list[ChartSpec]
    app_path: str | None = None
    spec_path: str | None = None
    spec_hash: str | None = None


def build_charts(kpi_out: dict, analysis: dict) -> list[ChartSpec]:
    charts = [
        ChartSpec(id=k["name"], title=k["definition"], chart_type="kpi_card", kpi_refs=[k["name"]],
                  query_ids=[k["query_id"]] if k.get("query_id") else [], unit=k["unit"], value=k["value"])
        for k in kpi_out["kpis"]
    ]
    for b in kpi_out.get("breakdowns", []):
        rows = [{"key": r["key"], "value": r["value"], "masked": r["masked"]} for r in b["rows"]]
        if b["dimension"] in TEMPORAL_DIMENSIONS:
            chart_type = "line"
            flagged = {a["day"] for a in analysis.get("anomalies", []) if a["zone"] == "ALL"}
            rows = [r | {"anomaly": r["key"] in flagged} for r in rows]
        elif len(rows) <= MAX_BAR_CATEGORIES:
            chart_type = "bar"
        else:
            chart_type, rows = "bar_h", rows[:MAX_ROWS_DISPLAYED]
        charts.append(ChartSpec(id=b["name"], title=b["definition"], chart_type=chart_type,
                                query_ids=[b["query_id"]], unit=b["unit"], data=rows))
    answered = [a for a in kpi_out.get("answers", []) if a.get("query_id")]
    if kpi_out.get("answers"):
        charts.append(ChartSpec(
            id="questions", title="Réponses aux questions métier", chart_type="table",
            query_ids=[a["query_id"] for a in answered],
            data=[{"question": a["question"], "mode": a["mode"], "sql": a["sql"], "rows": a["rows"],
                   "error": a["error"], "refusal": a.get("refusal"), "cross_check": a.get("cross_check")}
                  for a in kpi_out["answers"]],
        ))
    charts.append(ChartSpec(
        id="insights", title="Insights", chart_type="text",
        kpi_refs=sorted({r for i in analysis["insights"] for r in i["kpi_refs"]}),
        data=[{"title": i["title"], "text": i["text"], "written_by": i["written_by"]}
              for i in analysis["insights"]],
    ))
    return charts


class DashboardGeneratorAgent(BaseAgent):
    name = "dashboard_generator"
    output_model = DashboardOutput

    def run(self, state: PipelineState) -> DashboardOutput:
        kpi_out = state.output("kpi_semantic")
        analysis = state.output("analyste_bi")
        charts = build_charts(kpi_out, analysis)
        title = "NYC 311 — Tableau de bord"
        spec = {
            "run_id": state.run_id,
            "title": title,
            "snapshot_hash": kpi_out.get("snapshot_hash"),
            "dataset": state.dataset.model_dump(),
            "charts": [c.model_dump(mode="json") for c in charts],
        }
        spec_path = Path(state.require_workspace()) / SPEC_FILE
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return DashboardOutput(
            title=title, charts=charts, app_path=str(APP_PATH), spec_path=str(spec_path),
            spec_hash=file_hash(spec_path), notes=[f"{len(charts)} éléments, spec {SPEC_FILE}"],
        )

    def validate(self, output: DashboardOutput, state: PipelineState) -> list[Check]:
        known = {k["name"] for k in state.output("kpi_semantic")["kpis"]}
        unknown = sorted({r for c in output.charts for r in c.kpi_refs} - known)
        untraced = [c.id for c in output.charts if c.chart_type in NUMERIC_CHARTS and not c.query_ids]
        return [
            Check(name="charts_not_empty", passed=bool(output.charts),
                  message=f"{len(output.charts)} graphique(s)"),
            Check(name="chart_kpi_refs_exist", passed=not unknown,
                  message=f"KPI inconnus : {unknown}" if unknown else "ok"),
            Check(name="charts_traced", passed=not untraced,
                  message=f"graphiques sans requête d'origine : {untraced}" if untraced else "ok"),
        ]
