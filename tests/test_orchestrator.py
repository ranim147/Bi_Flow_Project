import pandas as pd
import pytest

from biflow.agents import default_agents
from biflow.agents.auditor_xai import AuditorXAIAgent
from biflow.agents.kpi_semantic import KPIOutput, KPISemanticAgent, KPIValue
from biflow.agents.testing import FailingAgent
from biflow.orchestrator.graph import build_graph, run_pipeline
from biflow.orchestrator.state import STEP_ORDER, DatasetRef, PipelineState, RunStatus
from biflow.orchestrator.tracing import DbTraceLogger
from biflow.orchestrator.validation import check_raw_frame


def _steps(state: PipelineState) -> list[str]:
    return [h.step for h in state.history]


def test_end_to_end_with_stubs(ctx):
    state = run_pipeline(DatasetRef(start_date="2025-01-01", end_date="2025-01-31"), ctx=ctx)
    assert state.status == RunStatus.SUCCESS
    assert state.published is True
    assert _steps(state) == STEP_ORDER
    assert set(state.outputs) == set(STEP_ORDER)
    assert all(v.passed for v in state.validations.values())
    # une ligne de trace par étape + la ligne de synthèse du run
    records = ctx.trace_logger.records
    assert [r.step for r in records] == [*STEP_ORDER, "__run__"]
    assert records[-1].status == "success"
    assert {r.run_id for r in records} == {state.run_id}


def test_transient_failure_is_retried(ctx):
    agents = default_agents()
    agents["quality_etl"] = FailingAgent(agents["quality_etl"], fail_times=1)
    state = run_pipeline(agents=agents, ctx=ctx)
    assert state.status == RunStatus.SUCCESS
    etl = next(h for h in state.history if h.step == "quality_etl")
    assert etl.attempts == 2
    assert [r.status for r in ctx.trace_logger.records if r.step == "quality_etl"] == ["retry", "success"]


def test_persistent_failure_marks_run_failed_and_stops(ctx):
    agents = default_agents()
    agents["kpi_semantic"] = FailingAgent(agents["kpi_semantic"])
    state = run_pipeline(agents=agents, ctx=ctx)
    assert state.status == RunStatus.FAILED
    assert state.published is False
    assert _steps(state) == ["data_profiler", "quality_etl", "kpi_semantic"]
    assert len(state.errors) == ctx.max_retries + 1
    assert "dashboard_generator" not in state.outputs
    assert ctx.trace_logger.records[-1].status == "failed"


def test_retry_count_is_configurable(ctx):
    ctx.max_retries = 0
    agents = default_agents()
    agents["data_profiler"] = FailingAgent(agents["data_profiler"], fail_times=1)
    state = run_pipeline(agents=agents, ctx=ctx)
    assert state.status == RunStatus.FAILED
    assert len(state.errors) == 1


class _OutOfRangeKPI(KPISemanticAgent):
    def run(self, state):
        return KPIOutput(kpis=[KPIValue(name="sla_compliance_rate", value=130, unit="percent",
                                        definition="x")])


def test_validation_failure_blocks_publication(ctx):
    agents = default_agents()
    agents["kpi_semantic"] = _OutOfRangeKPI()
    state = run_pipeline(agents=agents, ctx=ctx)
    assert state.status == RunStatus.FAILED
    assert all(e.error_type == "StepValidationError" for e in state.errors)
    assert "kpi:sla_compliance_rate" in state.errors[0].message


def test_auditor_feedback_loop_corrects_once(ctx):
    agents = default_agents()
    agents["auditor_xai"] = AuditorXAIAgent(flag_step="kpi_semantic")
    state = run_pipeline(agents=agents, ctx=ctx)
    assert state.status == RunStatus.SUCCESS
    assert state.feedback_rounds == 1
    assert "kpi_semantic" in state.corrections
    # retour à kpi_semantic puis réexécution de tout l'aval
    assert _steps(state) == [*STEP_ORDER, *STEP_ORDER[2:]]
    assert [h.feedback_round for h in state.history][-4:] == [1, 1, 1, 1]


def test_auditor_persistent_issue_marks_unreliable(ctx):
    agents = default_agents()
    agents["auditor_xai"] = AuditorXAIAgent(flag_step="quality_etl", persistent=True)
    state = run_pipeline(agents=agents, ctx=ctx)
    assert state.status == RunStatus.UNRELIABLE
    assert state.published is False
    assert _steps(state) == [*STEP_ORDER, *STEP_ORDER[1:]]  # un seul renvoi
    assert len(state.findings) == 2


def test_build_graph_requires_all_agents(ctx):
    agents = default_agents()
    del agents["analyste_bi"]
    with pytest.raises(ValueError, match="analyste_bi"):
        build_graph(agents, ctx)


@pytest.mark.db
def test_trace_written_to_database(clean_db, ctx):
    ctx.trace_logger = DbTraceLogger(clean_db)
    state = run_pipeline(ctx=ctx)
    rows = ctx.trace_logger.fetch_run(state.run_id)
    assert [r["step"] for r in rows] == [*STEP_ORDER, "__run__"]
    assert rows[1]["input_rows"] == 1000 and rows[1]["output_rows"] == 990


def test_check_raw_frame_detects_issues():
    df = pd.DataFrame({
        "unique_key": [1, 1, 2],
        "created_date": pd.to_datetime(["2025-01-02", "2025-01-02", "2025-01-02"]),
        "closed_date": pd.to_datetime(["2025-01-01", "2025-01-03", None]),
        **{c: [None] * 3 for c in ["agency", "complaint_type", "descriptor", "incident_zip",
                                    "borough", "status", "resolution_description",
                                    "latitude", "longitude"]},
    })
    checks = {c.name: c for c in check_raw_frame(df)}
    assert checks["schema"].passed
    assert not checks["unique_key_unique"].passed
    assert not checks["closed_after_created"].passed
    assert checks["closed_after_created"].severity == "warning"
    assert "1 lignes" in checks["closed_after_created"].message

    assert not check_raw_frame(df.drop(columns=["borough"]))[0].passed
