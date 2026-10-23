"""Agent 2 — Qualité / ETL.

À IMPLÉMENTER (coéquipier « Qualité/ETL ») :
    À partir des données brutes (`load_raw_frame(state.dataset)`) et du profil
    (`state.output("data_profiler")`, notamment `anomalies`) :
    - nettoyer : doublons, closed_date < created_date, boroughs « Unspecified », zips invalides,
      coordonnées hors NYC, normalisation des libellés de complaint_type ;
    - enrichir : resolution_hours, jour/heure, jointure ref.boroughs / ref.sla_targets ;
    - écrire le résultat dans une table du schéma `processed` (ex. processed.nyc311_clean)
      et renseigner `output_table`.
    Chaque règle appliquée doit être listée dans `rules_applied` (traçabilité pour l'Auditeur).

Entrée  : state.dataset, state.outputs["data_profiler"]
Sortie  : QualityOutput -> state.outputs["quality_etl"]
"""

from __future__ import annotations

from pydantic import Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.orchestrator.state import Check, PipelineState


class QualityOutput(AgentOutput):
    rows_in: int
    rows_out: int
    rows_dropped: int
    invalid_dates: int = Field(0, description="lignes avec closed_date < created_date")
    duplicates: int = 0
    output_table: str = "processed.nyc311_clean"
    rules_applied: list[str] = Field(default_factory=list)


class QualityETLAgent(BaseAgent):
    name = "quality_etl"
    output_model = QualityOutput

    def run(self, state: PipelineState) -> QualityOutput:
        rows_in = state.output("data_profiler")["row_count"]
        # STUB : aucune transformation réelle, la table de sortie n'existe pas encore.
        return QualityOutput(
            rows_in=rows_in,
            rows_out=rows_in - 10,
            rows_dropped=10,
            invalid_dates=10,
            rules_applied=["stub: drop closed_date < created_date"],
            input_rows=rows_in,
            output_rows=rows_in - 10,
            is_stub=True,
        )

    def validate(self, output: QualityOutput, state: PipelineState) -> list[Check]:
        return [
            Check(name="rows_out_le_rows_in", passed=output.rows_out <= output.rows_in,
                  message=f"rows_out={output.rows_out}, rows_in={output.rows_in}"),
            Check(name="rows_balance",
                  passed=output.rows_out + output.rows_dropped == output.rows_in,
                  message="rows_out + rows_dropped doit égaler rows_in"),
            Check(name="counts_non_negative",
                  passed=min(output.rows_out, output.rows_dropped,
                             output.invalid_dates, output.duplicates) >= 0,
                  message="compteurs >= 0"),
        ]
