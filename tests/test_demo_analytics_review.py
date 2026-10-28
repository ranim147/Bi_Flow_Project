"""Independent arithmetic and temporal-boundary checks for the academic demo.

Fixtures are deliberately synthetic. The real-input test performs only Python
analysis and never runs an LLM or changes a historical campaign.
"""
from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from bi_analyst.analytics import (
    backlog_snapshots,
    decompose_delay_change,
    load_real_bundle,
    prepare_investigation_frame,
)
from bi_analyst.decision_tools import build_decision_analysis
from bi_analyst.models import AnalysisContext
from bi_analyst.tools import execute_tool


ROOT = Path(__file__).resolve().parents[1]
PERIODS = ("2026-08", "2026-09")


def synthetic_partition() -> pd.DataFrame:
    rows = []
    for month, groups in (
        ("2026-08", (("A", "X", 40, 2.0), ("B", "Y", 60, 8.0))),
        ("2026-09", (("A", "X", 70, 3.0), ("B", "Y", 30, 9.0))),
    ):
        closed = pd.Timestamp(month + "-25T00:00:00Z")
        for category, agency, count, days in groups:
            for _ in range(count):
                rows.append(dict(unique_key=f"SYNTHETIC-REVIEW-{len(rows)}",
                                 created_date=closed - pd.Timedelta(days=days), closed_date=closed,
                                 resolution_days=days, borough="BROOKLYN",
                                 complaint_type=category, agency=agency))
    return prepare_investigation_frame(pd.DataFrame(rows), "2026-10-17T00:00:00Z")


class AcademicAnalyticsReviewTests(unittest.TestCase):
    def test_decomposition_matches_independent_weighted_mean_and_reverses_sign(self):
        data = synthetic_partition()
        result = decompose_delay_change(data, PERIODS, ("complaint_type", "agency"))
        baseline = (40 * 2 + 60 * 8) / 100
        current = (70 * 3 + 30 * 9) / 100
        self.assertAlmostEqual(result["delta_mean_days"], current - baseline)
        self.assertAlmostEqual(result["within_days"], 1.0)
        self.assertAlmostEqual(result["composition_days"], -1.8)
        reverse = decompose_delay_change(data, tuple(reversed(PERIODS)), ("complaint_type", "agency"))
        for metric in ("delta_mean_days", "within_days", "composition_days", "unmatched_days"):
            self.assertAlmostEqual(result[metric], -reverse[metric])

    def test_joint_partition_preserves_all_valid_rows_and_unit_weights(self):
        data = synthetic_partition()
        result = decompose_delay_change(data, PERIODS, ("complaint_type", "agency"))
        for suffix in ("baseline", "current"):
            self.assertEqual(sum(row[f"n_{suffix}"] for row in result["groups"]), result[f"n_{suffix}"])
            self.assertAlmostEqual(sum(row[f"weight_{suffix}"] for row in result["groups"]), 1.0)
        self.assertAlmostEqual(sum(row["contribution_days"] for row in result["groups"]), result["delta_mean_days"])

    def test_missing_segment_labels_stay_in_identity_without_operational_ranking(self):
        data = synthetic_partition()
        data.loc[data.complaint_type.eq("A"), "complaint_type"] = "__MISSING__"
        result = decompose_delay_change(data, PERIODS, ("complaint_type", "agency"))
        unknown = next(row for row in result["groups"] if row["complaint_type"] == "__MISSING__")
        self.assertFalse(unknown["rankable"])
        self.assertEqual(unknown["priority_score"], 0.0)
        self.assertEqual(result["n_baseline"], 100)
        self.assertEqual(result["n_current"], 100)
        self.assertAlmostEqual(result["residual_days"], 0.0)

    def test_backlog_uses_exclusive_month_boundary_and_observable_arrivals(self):
        frame = pd.DataFrame([
            dict(unique_key="SYNTHETIC-BOUNDARY-CLOSE", created_date="2026-08-30T00:00:00Z",
                 closed_date="2026-09-01T00:00:00Z", resolution_days=2.0, borough="BROOKLYN"),
            dict(unique_key="SYNTHETIC-BOUNDARY-ARRIVE", created_date="2026-09-01T00:00:00Z",
                 closed_date=None, resolution_days=np.nan, borough="BROOKLYN"),
            dict(unique_key="SYNTHETIC-UNOBSERVED", created_date="2026-11-01T00:00:00Z",
                 closed_date=None, resolution_days=np.nan, borough="BROOKLYN"),
        ])
        data = prepare_investigation_frame(frame, "2026-10-17T00:00:00Z")
        self.assertEqual(len(data), 2)
        snapshots = backlog_snapshots(data, PERIODS)
        self.assertEqual(snapshots[0]["backlog_n"], 1)
        self.assertEqual(snapshots[0]["age_median_days"], 2.0)
        self.assertEqual(snapshots[1]["backlog_n"], 1)
        self.assertEqual(snapshots[1]["age_median_days"], 30.0)

    def test_real_drilldown_reconciles_and_does_not_confuse_mean_with_median(self):
        bundle = load_real_bundle(ROOT)
        facts, details, status = build_decision_analysis("Investiguer la hausse des délais à Brooklyn.", bundle)
        self.assertEqual(status, "success")
        self.assertEqual(tuple(details["periods"]), PERIODS)
        self.assertEqual(details["problems"][0]["filters"]["borough"], "BROOKLYN")
        joint = details["drilldowns"]["BROOKLYN"]["joint_segments"]
        self.assertAlmostEqual(joint["within_days"] + joint["composition_days"] + joint["unmatched_days"], joint["delta_mean_days"])
        metrics = {fact.metric: fact.value for fact in facts}
        self.assertGreater(metrics["delta_mean_days"], 0)
        self.assertLess(metrics["median_change_days"], 0)
        self.assertTrue(all(fact.data_kind == "real" and fact.source_sha256 for fact in facts))

    def test_real_temporal_tools_disclose_their_unmet_prerequisites(self):
        bundle = load_real_bundle(ROOT)
        context = AnalysisContext(project_root=ROOT)
        anomaly = execute_tool("detect_anomalies", "Observe les anomalies mensuelles.", context, bundle)
        trend = execute_tool("analyze_trend", "Observe la tendance mensuelle.", context, bundle)
        self.assertEqual(anomaly.trace.status, "skipped")
        self.assertEqual(trend.trace.status, "skipped")
        self.assertEqual(anomaly.result["available_complete_periods"], 4)
        self.assertEqual(anomaly.result["minimum_testable_periods"], 7)
        self.assertEqual(trend.result["required_periods_for_significance"], 36)
        self.assertNotIn("statistical_anomaly_score", {fact.metric for fact in anomaly.evidence})


if __name__ == "__main__":
    unittest.main()
