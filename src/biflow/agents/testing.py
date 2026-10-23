"""Outils de simulation de pannes (tests et démo `--simulate-failure`)."""

from __future__ import annotations

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.orchestrator.state import PipelineState


class FailingAgent(BaseAgent):
    """Enveloppe un agent : lève une erreur sur les `fail_times` premiers appels
    (None = échoue toujours), puis délègue à l'agent réel."""

    def __init__(self, wrapped: BaseAgent, fail_times: int | None = None) -> None:
        self.wrapped = wrapped
        self.name = wrapped.name
        self.output_model = wrapped.output_model
        self.fail_times = fail_times
        self.calls = 0

    def run(self, state: PipelineState) -> AgentOutput:
        self.calls += 1
        if self.fail_times is None or self.calls <= self.fail_times:
            raise RuntimeError(f"panne simulée de {self.name} (appel {self.calls})")
        return self.wrapped.run(state)

    def validate(self, output, state):
        return self.wrapped.validate(output, state)
