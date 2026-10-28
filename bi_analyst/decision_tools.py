"""Deterministic drill-down controller for evidence-backed investigations."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import pandas as pd

from .analytics import (MIN_GROUP_N, agency_comparability, backlog_snapshots,
                        compare_delay_periods, decompose_delay_change,
                        prepare_investigation_frame, select_comparison_periods)
from .models import EvidenceFact

BASE_LIMITS = ["descriptive_not_causal", "nonrandom_sample", "closed_work_selection",
               "sample_backlog_only", "no_staffing_or_budget_evidence", "no_sla", "mean_not_median"]
LIMIT_TEXT = {
    "descriptive_not_causal": "Les contributions arithmétiques ne démontrent aucune cause.",
    "nonrandom_sample": "L'extrait par fenêtres est non aléatoire; les résultats ne représentent pas la population.",
    "closed_work_selection": "Les délais portent sur les dossiers clôturés pendant chaque période, pas sur des cohortes entrantes comparables.",
    "sample_backlog_only": "Le backlog reconstitué concerne uniquement les dossiers échantillonnés; les réouvertures et changements de statut ne sont pas observés.",
    "no_staffing_or_budget_evidence": "Aucune preuve d'effectifs, de capacité ou de budget ne justifie une réallocation.",
    "no_sla": "Aucun SLA métier n'a été fourni.",
    "mean_not_median": "La décomposition concerne la moyenne, pas la médiane; leurs variations peuvent être de signes différents.",
    "partial_period_excluded": "Les périodes partielles sont exclues de la comparaison.",
    "missing_values": "Les valeurs manquantes ou incohérentes sont comptées sans remplacement par zéro.",
    "small_segments": "Les segments avec un effectif insuffisant dans une période sont exclus du classement, mais conservés dans la décomposition.",
    "unmatched_groups": "Les groupes nouveaux ou absents ne permettent pas d'estimer une évolution interne; leur contribution reste séparée.",
    "agency_case_mix": "Les moyennes brutes des agences ne sont pas directement comparables sans contrôler les catégories.",
    "insufficient_history": "Deux périodes complètes contiguës ne sont pas disponibles pour cette comparaison.",
    "small_scope": "Le périmètre ne possède pas assez de clôtures valides dans chaque période pour établir une dégradation.",
    "no_observed_increase": "Aucune hausse descriptive n'est établie dans les périmètres demandés sur les périodes retenues.",
    "synthetic_only": "SCÉNARIO SYNTHÉTIQUE DE TEST; ces résultats ne décrivent pas NYC 311 réel.",
}


def decision_question(question: str) -> bool:
    text = question.casefold()
    return any(term in text for term in ("investig", "hypothès", "hypothes", "dégrad", "degrad", "drill", "backlog", "ancienneté", "contribu", "composition", "raisons", "causes possibles")) or (
        any(term in text for term in ("délai", "delai")) and any(term in text for term in ("augment", "hausse", "agence", "recommand")))


def clean_records(table: pd.DataFrame) -> list[dict[str, Any]]:
    # JSON round trip normalizes pandas NaN to null, not to an invented zero.
    return json.loads(table.to_json(orient="records"))


def build_decision_analysis(question: str, bundle: dict[str, Any]) -> tuple[list[EvidenceFact], dict[str, Any], str]:
    metadata = bundle["metadata"]
    data = prepare_investigation_frame(bundle["clean"], metadata["extracted_at_utc"])
    periods = select_comparison_periods(metadata)
    kind = bundle["kind"]
    source = bundle["source_paths"]["clean"]
    digest = bundle["hashes"].get(source)
    if not digest:
        raise ValueError("Investigation requires an input fingerprint.")
    facts: list[EvidenceFact] = []

    def add(scope: str, metric: str, value: float | int | None, n: int | None,
            filters: dict[str, str] | None = None, unit="days", method=None) -> str:
        token = hashlib.sha256(json.dumps([scope, filters or {}, metric], sort_keys=True).encode()).hexdigest()[:10]
        fact_id = f"ds.{token}"
        if any(fact.fact_id == fact_id for fact in facts):
            raise ValueError("Duplicate investigation evidence identifier.")
        fact = EvidenceFact(fact_id=fact_id, metric=metric, scope=json.dumps(filters or {"scope": scope}, sort_keys=True),
                            value=None if value is None else float(value), unit=unit, n=n,
                            source=source + "#" + scope + "/" + metric, source_sha256=digest,
                            data_kind=kind, method=method or "Descriptive completed-work monthly comparison",
                            reference_period=None if periods is None else "/".join(periods))
        facts.append(fact)
        return fact_id

    base_limits = [*BASE_LIMITS]
    if metadata.get("partial_months"):
        base_limits.append("partial_period_excluded")
    if kind == "synthetic":
        base_limits.append("synthetic_only")
    invalid_n = int((data.closed_date.notna() & ~data.valid_delay).sum())
    if invalid_n or data.created_date.isna().any() or data[["borough", "complaint_type", "agency"]].eq("__MISSING__").any().any():
        base_limits.append("missing_values")
    quality_ids = [add("quality", "invalid_closed_duration_n", invalid_n, len(data), unit="requests"),
                   add("quality", "sample_requests_n", len(data), len(data), unit="requests")]
    details: dict[str, Any] = dict(data_kind=kind, periods=periods, as_of=metadata["extracted_at_utc"],
                                  period_basis="closure_month", min_group_n=MIN_GROUP_N,
                                  method="Exact symmetric decomposition of mean closed-request duration; no causal attribution",
                                  quality=dict(invalid_closed_n=invalid_n, unknown_creation_n=int(data.created_date.isna().sum())),
                                  problems=[], borough_comparisons=[], drilldowns={})
    if periods is None:
        details["problems"] = [dict(problem_id="history", kind="insufficient_history", filters={},
                                    evidence_ids=quality_ids, limitation_codes=[*base_limits, "insufficient_history"],
                                    priority_basis="Les périodes disponibles ne permettent pas la comparaison demandée.")]
        return facts, details, "skipped"

    comparisons = compare_delay_periods(data, periods)
    details["borough_comparisons"] = clean_records(comparisons)
    named = [value for value in data.borough.unique() if value != "__MISSING__"
             and re.search(r"(?<!\w)" + re.escape(value.casefold()) + r"(?!\w)", question.casefold())]
    selected = comparisons.loc[comparisons.borough.isin(named)] if named and not comparisons.empty else comparisons
    rising = selected.loc[selected.deterioration_observed] if not selected.empty else selected
    if not rising.empty:
        rising = rising.assign(impact=rising.delta_mean_days * rising[["n_baseline", "n_current"]].min(axis=1).pow(.5))
        selected_rows = rising.sort_values(["impact", "borough"], ascending=[False, True]).head(2).to_dict("records")
    else:
        selected_rows = selected.sort_values("borough").head(1).to_dict("records") if not selected.empty else []

    for index, row in enumerate(selected_rows):
        borough = row["borough"]
        subset = data.loc[data.borough.eq(borough)]
        decomposition = decompose_delay_change(subset, periods, ("complaint_type", "agency"))
        breakdowns = {dimension: decompose_delay_change(subset, periods, (dimension,))
                      for dimension in ("complaint_type", "agency")}
        snapshots = backlog_snapshots(subset, periods)
        comparability = agency_comparability(subset, periods)
        details["drilldowns"][borough] = dict(joint_segments=decomposition, marginal_partitions=breakdowns,
                                              backlog=snapshots, agency_comparability=comparability,
                                              period_profile=clean_records(compare_delay_periods(subset, periods, ("closure_month",))))
        limits = [*base_limits]
        if any(not group["rankable"] for group in decomposition["groups"]):
            limits.append("small_segments")
        if any(group["unmatched_days"] for group in decomposition["groups"]):
            limits.append("unmatched_groups")
        if any(item["category_overlap"] < 1 - 1e-10 for item in comparability):
            limits.append("agency_case_mix")
        if not row["eligible"]:
            problem_kind = "small_scope"
            limits.append("small_scope")
        elif not row["deterioration_observed"]:
            problem_kind = "no_observed_increase"
            limits.append("no_observed_increase")
        elif abs(decomposition["unmatched_days"]) > max(abs(decomposition["within_days"]), abs(decomposition["composition_days"])):
            problem_kind = "unmatched_change"
        elif decomposition["composition_days"] > abs(decomposition["within_days"]):
            problem_kind = "composition_shift"
        else:
            problem_kind = "within_segment_increase"
        if problem_kind == "no_observed_increase" and "agence" in question.casefold() and "agency_case_mix" in limits:
            problem_kind = "agency_noncomparable"
        positive = sorted([group for group in decomposition["groups"] if group["contribution_days"] > 0],
                          key=lambda group: (-group["contribution_days"], str(group["complaint_type"]), str(group["agency"])))
        ranked = sorted([group for group in positive if group["rankable"]],
                        key=lambda group: (-group["priority_score"], str(group["complaint_type"]), str(group["agency"])))
        lead = ranked[0] if ranked and problem_kind not in {"small_scope", "no_observed_increase", "agency_noncomparable"} else None
        # Do not hide the largest contributor merely because it is poorly observed.
        # It becomes a data-collection target, never an operational ranking.
        if positive and problem_kind not in {"small_scope", "no_observed_increase", "agency_noncomparable"} and (
            lead is None or positive[0]["contribution_days"] > 2 * lead["contribution_days"]
        ):
            lead = positive[0]
        filters = {"borough": borough}
        if lead:
            filters.update({key: lead[key] for key in ("complaint_type", "agency")})
        scope_id = f"scope_{index}"
        n = int(min(row["n_baseline"], row["n_current"]))
        evidence_ids = [add(scope_id, key, decomposition.get(key), n, {"borough": borough})
                        for key in ("delta_mean_days", "within_days", "composition_days", "unmatched_days")]
        evidence_ids.extend([add(scope_id, "mean_delay_baseline", decomposition.get("mean_baseline"), int(row["n_baseline"]), {"borough": borough}),
                             add(scope_id, "mean_delay_current", decomposition.get("mean_current"), int(row["n_current"]), {"borough": borough}),
                             add(scope_id, "valid_closed_baseline_n", int(row["n_baseline"]), int(row["closed_baseline"]), {"borough": borough}, unit="requests"),
                             add(scope_id, "valid_closed_current_n", int(row["n_current"]), int(row["closed_current"]), {"borough": borough}, unit="requests"),
                             add(scope_id, "median_change_days", None if not n else float(row["median_current"] - row["median_baseline"]), n, {"borough": borough})])
        if lead:
            evidence_ids.extend([add(scope_id, "segment_contribution_days", lead["contribution_days"], int(min(lead["n_baseline"], lead["n_current"])), filters),
                                 add(scope_id, "segment_priority_score", lead["priority_score"], int(min(lead["n_baseline"], lead["n_current"])), filters, unit="heuristic score")])
        backlog_delta = snapshots[1]["backlog_n"] - snapshots[0]["backlog_n"]
        evidence_ids.extend([add(scope_id, "backlog_sample_change", backlog_delta, len(subset), {"borough": borough}, unit="sample requests"),
                             add(scope_id, "current_backlog_sample_n", snapshots[1]["backlog_n"], len(subset), {"borough": borough}, unit="sample requests"),
                             add(scope_id, "current_open_age_p90_days", snapshots[1]["age_p90_days"], snapshots[1]["backlog_n"], {"borough": borough}),
                             add(scope_id, "incoming_sample_change", snapshots[1]["incoming_sample_n"] - snapshots[0]["incoming_sample_n"], len(subset), {"borough": borough}, unit="sample requests")])
        if "agency_case_mix" in limits:
            evidence_ids.append(add(scope_id, "agency_category_overlap_min", min(item["category_overlap"] for item in comparability), len(subset), {"borough": borough}, unit="share"))
        if lead and lead["rankable"]:
            priority_basis = (f"Contribution descriptive de {lead['contribution_days']:.3f} jour à la variation de moyenne; "
                              f"{lead['n_baseline']} clôtures valides avant et {lead['n_current']} après; "
                              "priorité calculée par contribution positive, volume observé et complétude, sans interprétation causale.")
        elif lead:
            priority_basis = "Ce groupe présente une forte contribution descriptive mais un effectif ou une comparabilité insuffisant; compléter les données sans le classer opérationnellement."
        else:
            priority_basis = "Le contrôle de la couverture et de la comparabilité précède une attribution à un segment."
        details["problems"].append(dict(problem_id=scope_id, kind=problem_kind, filters=filters,
                                        evidence_ids=evidence_ids, limitation_codes=sorted(set(limits)),
                                        llm_evidence_ids=[fact_id for fact_id in evidence_ids if next(f for f in facts if f.fact_id == fact_id).metric in {
                                            "delta_mean_days", "within_days", "composition_days", "unmatched_days", "segment_contribution_days",
                                            "agency_category_overlap_min", *( ("backlog_sample_change", "current_open_age_p90_days") if backlog_delta > 0 else ())}],
                                        priority_basis=priority_basis, backlog_increase=backlog_delta > 0,
                                        comparable_segment=lead is not None and lead["rankable"], segment=lead,
                                        agency_mix_warning="agency_case_mix" in limits))
    if not details["problems"]:
        details["problems"] = [dict(problem_id="coverage", kind="small_scope", filters={}, evidence_ids=quality_ids,
                                    limitation_codes=[*base_limits, "small_scope"],
                                    priority_basis="Aucune comparaison éligible n'est disponible.")]
    # Convert potential dataframe/numpy nulls at the audit boundary, never emit NaN.
    json.dumps(details, allow_nan=False)
    return facts, details, "success"
