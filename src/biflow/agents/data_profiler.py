"""Agent 1 — Data Profiler.

À IMPLÉMENTER (coéquipier « Data Profiler ») :
    Charger les données brutes du run (`load_raw_frame(state.dataset)`) et produire un profil :
    - nombre de lignes, liste des colonnes, taux de valeurs nulles par colonne ;
    - plage de dates (created_date min/max) ;
    - distribution des complaint_type, boroughs, statuts ;
    - anomalies repérées (dates incohérentes, coordonnées hors NYC, codes postaux invalides...)
      -> à mettre dans `anomalies` pour que l'agent Qualité/ETL les traite.

Entrée  : state.dataset (DatasetRef)
Sortie  : ProfilerOutput (ci-dessous) -> state.outputs["data_profiler"]
"""

from __future__ import annotations

from pydantic import Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.common.schema import RAW_COLUMNS
from biflow.orchestrator.state import Check, PipelineState
from biflow.orchestrator.validation import check_in_range, check_required_columns


class ProfilerOutput(AgentOutput):
    row_count: int
    columns: list[str]
    null_rates: dict[str, float] = Field(default_factory=dict, description="colonne -> [0, 1]")
    date_min: str | None = None
    date_max: str | None = None
    complaint_type_counts: dict[str, int] = Field(default_factory=dict)
    anomalies: list[str] = Field(default_factory=list)


class DataProfilerAgent(BaseAgent):
    name = "data_profiler"
    output_model = ProfilerOutput

    def run(self, state: PipelineState) -> ProfilerOutput:
        # STUB : valeurs factices. Remplacer par un vrai profilage de load_raw_frame(state.dataset).
        return ProfilerOutput(
            row_count=1000,
            columns=list(RAW_COLUMNS),
            null_rates={c: 0.0 for c in RAW_COLUMNS},
            date_min=state.dataset.start_date,
            date_max=state.dataset.end_date,
            input_rows=1000,
            output_rows=1000,
            is_stub=True,
            notes=["stub: profil factice"],
        )

    def validate(self, output: ProfilerOutput, state: PipelineState) -> list[Check]:
        checks = [
            check_required_columns(output.columns, RAW_COLUMNS),
            check_in_range("row_count", output.row_count, low=0),
        ]
        bad = {c: r for c, r in output.null_rates.items() if not 0 <= r <= 1}
        checks.append(Check(name="null_rates_range", passed=not bad,
                            message=f"taux hors [0,1] : {bad}" if bad else "ok"))
        return checks
