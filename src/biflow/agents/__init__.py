"""Agents du pipeline BIFlow — un fichier par agent, un coéquipier par fichier."""

from __future__ import annotations

from biflow.agents.analyste_bi import AnalysteBIAgent
from biflow.agents.auditor_xai import AuditorXAIAgent
from biflow.agents.base import AgentOutput, BaseAgent, RunContext
from biflow.agents.dashboard_generator import DashboardGeneratorAgent
from biflow.agents.data_profiler import DataProfilerAgent
from biflow.agents.kpi_semantic import KPISemanticAgent
from biflow.agents.quality_etl import QualityETLAgent

__all__ = ["AgentOutput", "BaseAgent", "RunContext", "default_agents"]


def default_agents() -> dict[str, BaseAgent]:
    """Registre nom d'étape -> instance d'agent (ordre = STEP_ORDER)."""
    agents: list[BaseAgent] = [
        DataProfilerAgent(),
        QualityETLAgent(),
        KPISemanticAgent(),
        AnalysteBIAgent(),
        DashboardGeneratorAgent(),
        AuditorXAIAgent(),
    ]
    return {a.name: a for a in agents}
