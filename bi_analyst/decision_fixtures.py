"""Explicit, deterministic SYNTHETIC cases; never substitutes for real inputs."""
from __future__ import annotations
import pandas as pd

SCENARIOS = ("category_increase", "composition_shift", "backlog_increase", "agency_case_mix",
             "small_group", "insufficient_history", "unknown_cause", "insufficient_recommendation")


def synthetic_case(name: str):
    if name not in SCENARIOS:
        raise ValueError(name)
    rows = []
    for month in ("2026-08", "2026-09"):
        if name == "insufficient_history" and month == "2026-08":
            continue
        current = month == "2026-09"
        if name == "composition_shift":
            groups = [("SLOW", "A", 400 if current else 100, 10),
                      ("FAST", "B", 100 if current else 400, 1)]
        elif name == "agency_case_mix":
            groups = [("COMPLEX", "A", 60, 12), ("SIMPLE", "B", 60, 1)]
        elif name in {"small_group", "insufficient_recommendation"}:
            groups = [("TARGET", "A", 5, 8 if current else 2)]
        else:
            groups = [("TARGET", "A", 60, 8 if current else 2), ("OTHER", "B", 60, 2)]
        for category, agency, count, days in groups:
            closed = pd.Timestamp(month + "-25T12:00:00Z")
            for _ in range(count):
                rows.append(dict(unique_key=f"SYNTHETIC-{name}-{len(rows)}", created_date=closed - pd.Timedelta(days=days),
                                 closed_date=closed, resolution_days=float(days), borough="BROOKLYN",
                                 complaint_type=category, agency=agency))
        if name == "backlog_increase" and current:
            for _ in range(40):
                rows.append(dict(unique_key=f"SYNTHETIC-{name}-{len(rows)}", created_date=pd.Timestamp("2026-09-05T00:00:00Z"),
                                 closed_date=pd.NaT, resolution_days=float("nan"), borough="BROOKLYN", complaint_type="TARGET", agency="A"))
    frame = pd.DataFrame(rows)
    counts = frame.created_date.dt.strftime("%Y-%m").value_counts().sort_index().to_dict()
    metadata = dict(source="EXPLICIT SYNTHETIC DECISION TEST", sample_monthly_counts=counts,
                    extracted_at_utc="2026-10-17T20:00:00Z", period_start_inclusive="2026-08-01T00:00:00Z",
                    period_end_exclusive="2026-10-18T00:00:00Z", partial_months=["2026-10"])
    question = "Investiguer la hausse des délais à Brooklyn et proposer un plan de vérification."
    if name == "agency_case_mix":
        question = "Comparer les délais des agences à Brooklyn et investiguer leur comparabilité."
    if name == "insufficient_recommendation":
        question = "Recommande des actions pour investiguer la dégradation des délais à Brooklyn."
    return frame, metadata, question
