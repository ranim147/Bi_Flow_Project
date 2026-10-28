from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnalysisContext(StrictModel):
    project_root: Path = Field(strict=False)
    data_kind: Literal["real"] = "real"
    dataset_version: str | None = None

    @field_validator("project_root")
    @classmethod
    def normalize_project_root(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Project root does not exist: {path}")
        return path


class AnalyticalPlan(StrictModel):
    intent: Literal["summary", "comparison", "ranking", "trend", "anomaly", "drilldown", "investigation", "justified_refusal"]
    metric: str
    dimensions: list[str] = Field(default_factory=list)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    time_scope: list[str] = Field(default_factory=list)
    temporal_forbidden: bool = False
    operation_sequence: list[str] = Field(default_factory=list)
    unsupported_request: list[str] = Field(default_factory=list)
    executor: Literal["scoped", "investigation", "legacy"]
    locked_filters: dict[str, str] = Field(default_factory=dict)


class EvidenceFact(StrictModel):
    fact_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    value: float | None
    unit: str = Field(min_length=1)
    n: int | None = Field(ge=0)
    source: str = Field(min_length=1)
    source_sha256: str | None = None
    data_kind: Literal["real", "synthetic"] = "real"
    method: str | None = None
    threshold: float | None = None
    reference_period: str | None = None
    p_value: float | None = Field(default=None, ge=0, le=1)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float | None) -> float | None:
        if value is not None and (value != value or value in (float("inf"), float("-inf"))):
            raise ValueError("Evidence values must be finite or null.")
        return value


class ToolTrace(StrictModel):
    tool: str
    parameters: dict[str, str | int | float | bool | None]
    status: Literal["success", "skipped", "error"]
    started_at: str
    elapsed_ms: float = Field(ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    detail: str | None = None


class LLMInterpretation(StrictModel):
    decision: Literal["answer", "insufficient_data"]
    finding: Literal[
        "descriptive",
        "higher",
        "lower",
        "statistical_anomaly",
        "no_persistent_trend",
        "insufficient_history",
        "small_sample",
        "missing_data",
    ]
    evidence_fact_ids: list[str]
    insight: str = Field(min_length=1, max_length=1200)
    recommendation: str = Field(min_length=1, max_length=800)
    limitation_codes: list[
        Literal[
            "sample_only",
            "nonrandom_sample",
            "closed_only",
            "no_cause",
            "no_sla",
            "partial_month",
            "small_sample",
            "missing_data",
            "insufficient_history",
            "synthetic_only",
            "population_not_sample",
            "serial_autocorrelation_not_adjusted",
        ]
    ]


class LLMSelection(StrictModel):
    evidence_slots: list[str] = Field(min_length=1, max_length=4)
    recommendation_code: Literal[
        "review_comparison",
        "review_temporal_signal",
        "verify_data_quality",
        "review_kpi_context",
        "collect_more_history",
        "collect_more_observations",
        "no_strong_action",
    ]


class LLMDraft(StrictModel):
    """Internal v2 contract: no copied values, sources, denominators or decision."""

    finding: str = Field(min_length=1, max_length=40)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    insight: str = Field(min_length=1, max_length=360)
    recommendation: str = Field(min_length=1, max_length=240)
    limitation_codes: list[str] = Field(min_length=1, max_length=12)


class BIInsight(StrictModel):
    insight_id: str
    category: Literal["comparison", "trend", "anomaly", "data_quality", "summary"]
    finding: str
    insight: str
    recommendation: str
    evidence_fact_ids: list[str]
    limitation_codes: list[str]


class ValidationResult(StrictModel):
    status: Literal["validated", "rejected", "not_run"]
    json_valid: bool
    schema_valid: bool
    evidence_valid: bool
    numerical_claims_valid: bool
    causal_claims_clear: bool
    errors: list[str] = Field(default_factory=list)


class PriorityScope(StrictModel):
    scope_id: str
    filters: dict[str, str]
    baseline_period: str | None
    current_period: str | None
    period_basis: Literal["closure_month"] = "closure_month"


class InvestigationHypothesis(StrictModel):
    hypothesis_id: str
    statement: str
    status: Literal["unverified"] = "unverified"
    evidence_ids: list[str]
    additional_data: list[str]


class InvestigationStep(StrictModel):
    hypothesis_id: str
    dataset: Literal["prepared_extract", "additional_data"]
    filters: dict[str, str]
    fields: list[str]
    comparison: str


class InvestigationAction(StrictModel):
    hypothesis_id: str
    priority: int = Field(ge=1)
    action: str
    priority_reason: str
    decision_if_supported: str
    decision_if_refuted: str


class ExpectedVerification(StrictModel):
    hypothesis_id: str
    supports: str
    refutes: str


class DecisionProblem(StrictModel):
    problem_id: str
    problem: str
    priority_scope: PriorityScope
    evidence_ids: list[str]
    investigation_hypotheses: list[InvestigationHypothesis]
    investigation_steps: list[InvestigationStep]
    recommended_actions: list[InvestigationAction]
    expected_verification: list[ExpectedVerification]
    limitations: list[str]
    origin: Literal["granite_validated", "python_fallback"]


class DecisionDraft(StrictModel):
    """Granite assembles operations and hypotheses; Python binds scopes and facts."""
    problem: str
    priority_scope: str
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    investigation_hypotheses: list[str] = Field(min_length=1, max_length=3)
    investigation_steps: list[str] = Field(min_length=1, max_length=3)
    recommended_actions: list[str] = Field(min_length=1, max_length=3)
    expected_verification: list[str] = Field(min_length=1, max_length=3)
    limitations: list[str] = Field(min_length=1, max_length=18)


class BIAnalysisReport(StrictModel):
    contract_version: Literal["biflow-bi-analyst-v1", "biflow-bi-analyst-v2"] = "biflow-bi-analyst-v1"
    execution_id: str
    generated_at: str
    question: str
    model: Literal["granite3.3:2b"] | None = None
    data_kind: Literal["real", "synthetic"] = "real"
    status: Literal["validated", "fallback", "failed"]
    decision: Literal["answer", "insufficient_data", "refuse"]
    finding: str
    insights: list[BIInsight]
    evidence: list[EvidenceFact]
    limitations: list[str]
    source_refs: list[str]
    validation: ValidationResult
    tool_trace: list[ToolTrace]
    errors: list[str] = Field(default_factory=list)
    timing_ms: dict[str, float] = Field(default_factory=dict)
    decision_support: list[DecisionProblem] = Field(default_factory=list)
    analytical_plan: AnalyticalPlan | None = None

    @model_validator(mode="after")
    def consistent_data_kind_and_version(self):
        if any(fact.data_kind != self.data_kind for fact in self.evidence):
            raise ValueError("A report cannot mix real and synthetic evidence.")
        if self.contract_version == "biflow-bi-analyst-v1" and (self.data_kind != "real" or self.decision_support):
            raise ValueError("Synthetic evidence and decision plans require the v2 contract.")
        return self

    @model_serializer(mode="wrap")
    def versioned_serialization(self, handler):
        result = handler(self)
        if self.contract_version == "biflow-bi-analyst-v1":
            result.pop("decision_support", None)
            result.pop("analytical_plan", None)
        return result

    def to_legacy_dict(self) -> dict:
        """Explicit v1 projection for consumers forbidding new fields."""
        if self.data_kind != "real" or any(fact.data_kind != "real" for fact in self.evidence):
            raise ValueError("Synthetic reports cannot be projected as real v1 reports.")
        result = self.model_dump(mode="json")
        result["contract_version"] = "biflow-bi-analyst-v1"
        result.pop("decision_support", None)
        result.pop("analytical_plan", None)
        return result
