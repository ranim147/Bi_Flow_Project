"""Execute an ordered, scoped plan and materialize only Python-computed claims."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import pandas as pd

from .analytics import (MIN_GROUP_N, decompose_delay_change, prepare_investigation_frame,
                        select_comparison_periods)
from .models import AnalyticalPlan, EvidenceFact, ToolTrace
from .routing import normalized
from .tools import ToolResult

REQUIRED_DATA = {
    "staffing": ("Effectifs disponibles par agence et période", {"agency", "period", "available_staff"}),
    "workload": ("Charge de travail et nombre de dossiers assignés", {"assigned_cases", "workload"}),
    "capacity": ("Capacité de traitement", {"processing_capacity"}),
    "times": ("Temps de traitement et d'attente", {"processing_time", "waiting_time"}),
    "complexity": ("Complexité des dossiers", {"case_complexity"}),
    "assignments": ("Historique des affectations", {"assignment_history"}),
}
LIMITS = {
    "sample_only": "Ces résultats décrivent uniquement l'extrait préparé, pas la population NYC.",
    "nonrandom_sample": "Les fenêtres d'échantillonnage ne sont pas aléatoires; un biais de sélection reste possible.",
    "closed_only": "Les délais portent sur les dossiers clôturés observables avec durée valide; aucune durée manquante n'est remplacée par zéro.",
    "no_cause": "Les écarts descriptifs ne démontrent aucune causalité.",
    "no_sla": "Aucun SLA métier n'est disponible.",
    "small_sample": "Les groupes de moins de 30 délais valides sont exclus du classement opérationnel.",
    "case_mix": "Les catégories traitées peuvent différer entre agences; leurs délais bruts ne permettent pas de classer leur performance.",
    "period_selection": "Comparaison par mois de clôture, sur des mois calendaires entièrement couverts; les cohortes entrantes et la complexité ne sont pas contrôlées.",
    "sample_window": "La couverture calendaire complète ne rend pas les fenêtres échantillonnées exhaustives; les dossiers créés avant le début de l'extrait ne sont pas observés.",
    "missing_capabilities": "Les données de causalité et de capacité requises ne sont pas disponibles dans les sources validées.",
    "cohort_followup": "Le taux porte sur les créations de la cohorte à la date d'extraction; il dépend du recul d'observation et ne mesure pas une performance à durée de suivi constante.",
}


def complete_periods(metadata: dict) -> list[str]:
    start, end, cutoff = [pd.to_datetime(metadata[key], utc=True) for key in
                          ("period_start_inclusive", "period_end_exclusive", "extracted_at_utc")]
    return [month for month in sorted(metadata.get("sample_monthly_counts", {}))
            if month not in metadata.get("partial_months", [])
            and pd.Period(month, "M").start_time.tz_localize("UTC") >= start
            and (pd.Period(month, "M") + 1).start_time.tz_localize("UTC") <= min(end, cutoff)]


def execute_plan(question: str, plan: AnalyticalPlan, bundle: dict) -> tuple[list[ToolResult], dict]:
    plan = plan.model_copy(deep=True)
    source = bundle["source_paths"]["clean"]
    digest = bundle["hashes"][source]
    facts: list[EvidenceFact] = []
    results: list[ToolResult] = []
    sections: list[dict] = []
    details: dict[str, Any] = dict(plan=plan.model_dump(), sections=sections, tables={},
                                  limitations=list(LIMITS)[:7], decision="answer", finding="descriptive")

    def add(metric, scope, value, n, unit="jours calendaires", period=None, method=None):
        scope_text = json.dumps(scope, sort_keys=True, ensure_ascii=False)
        token = hashlib.sha256(json.dumps([metric, scope, period], sort_keys=True).encode()).hexdigest()[:16]
        fact = EvidenceFact(fact_id="plan." + token, metric=metric, scope=scope_text,
                            value=None if value is None else float(value), unit=unit, n=n,
                            source=source + "#plan=" + token, source_sha256=digest,
                            data_kind=bundle["kind"], reference_period=period,
                            method=method or "Python grouped descriptive calculation; valid observed closed requests")
        if any(f.fact_id == fact.fact_id for f in facts):
            raise ValueError("Duplicate planned evidence identifier")
        facts.append(fact)
        return fact.fact_id

    def record(operation, before, offset, outcome=None, status="success", **parameters):
        results.append(ToolResult(trace=ToolTrace(tool=operation,
            parameters={"question": question, "metric": plan.metric, **parameters}, status=status,
            started_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=(perf_counter() - before) * 1000,
            evidence_ids=[f.fact_id for f in facts[offset:]]), evidence=facts[offset:].copy(), result=outcome or {}))

    def section(key, text, ids, recommendation, category="comparison"):
        sections.append(dict(key=key, text=text, evidence_ids=ids, recommendation=recommendation, category=category))

    def insufficient(reason, ids):
        details.update(decision="insufficient_data", finding="insufficient_data", reason=reason)
        section("insufficiency", reason, ids, "Préciser le périmètre et fournir les observations nécessaires avant de conclure.", "summary")

    if plan.unsupported_request:
        before, offset = perf_counter(), len(facts)
        available = set(bundle["clean"].columns)
        capacity_request = bool(set(plan.unsupported_request) & {"unproven_causality", "staffing_without_capacity_data"})
        if capacity_request:
            ids = [add("required_data_available", {"requirement": key}, int(fields <= available), len(available), "boolean",
                       method="Required fields checked against validated input schema") for key, (_, fields) in REQUIRED_DATA.items()]
            details["capabilities"] = {key: dict(description=label, available=fields <= available,
                                                  missing_fields=sorted(fields - available)) for key, (label, fields) in REQUIRED_DATA.items()}
            details["limitations"].append("missing_capabilities")
        else:
            ids = [add("request_requirement_met", {"requirement": key}, 0, None, "boolean",
                       method="Question constraints checked against supported tools and source metadata") for key in plan.unsupported_request]
            details["capabilities"] = {key: dict(available=False) for key in plan.unsupported_request}
        reasons = []
        if "unproven_causality" in plan.unsupported_request:
            reasons.append("Je ne peux pas démontrer que les retards résultent d'un manque de personnel : les délais observés ne constituent pas une preuve causale.")
        if "staffing_without_capacity_data" in plan.unsupported_request:
            reasons.append("Je ne peux pas calculer un nombre d'agents à recruter sans données d'effectifs, de charge et de capacité adaptées.")
        if "unknown_sla" in plan.unsupported_request:
            reasons.append("Aucune cible SLA documentée ne permet d'établir un dépassement contractuel.")
        for code, message in {
            "reference_scope_required": "L'expression « cet arrondissement » ne définit pas un périmètre dans cette exécution. Indiquer son nom ou demander explicitement un classement préalable.",
            "year_required": "L'année des mois demandés doit être précisée; aucune période n'est substituée.",
            "conflicting_time_scope": "La demande exclut l'analyse temporelle tout en demandant plusieurs périodes; préciser l'analyse attendue.",
            "unsupported_period_count": "Cet outil compare exactement deux périodes; préciser la paire de mois à comparer.",
            "analysis_context_required": "Les causes possibles ne peuvent être investiguées sans préciser le phénomène, le KPI et le périmètre. Formuler des hypothèses, recueillir des observations comparables et définir les constats qui les soutiendraient ou les réfuteraient; aucune explication causale n'est affirmée.",
            "unsupported_temporal_test": "Le test de tendance ou d'anomalie demandé pour ce KPI et ce périmètre n'est pas pris en charge par les outils disponibles. Aucune comparaison statique ni autre KPI n'est substitué.",
        }.items():
            if code in plan.unsupported_request:
                reasons.append(message)
        insufficient(" ".join(reasons), ids)
        if capacity_request:
            sections[0]["text"] += " Données supplémentaires nécessaires : " + "; ".join(label for label, _ in REQUIRED_DATA.values()) + "."
            sections[0]["recommendation"] = ("Relier les dossiers aux affectations et effectifs par agence et période; séparer attente et traitement; "
                "comparer les charges et capacités à complexité comparable. Tester l'hypothèse de sous-capacité par variation documentée des effectifs, "
                "groupes témoins et facteurs de confusion; estimer ensuite la capacité par agent et le besoin sous plusieurs hypothèses de service.")
        record("check_capabilities", before, offset, details["capabilities"], status="skipped")
    else:
        before, offset = perf_counter(), len(facts)
        data = prepare_investigation_frame(bundle["clean"], bundle["metadata"]["extracted_at_utc"])
        # Match explicit category/agency names only at word boundaries, never a substring.
        for dimension in ("complaint_type", "agency"):
            named = [value for value in sorted(data[dimension].unique()) if value != "__MISSING__"
                     and re.search(r"(?<!\w)" + re.escape(normalized(value)) + r"(?!\w)", normalized(question))]
            if named:
                plan.filters[dimension] = named
        for dimension, values in plan.filters.items():
            data = data.loc[data[dimension].isin(values)].copy()
        scope = {key: "|".join(value) for key, value in plan.filters.items()}
        population_id = add("scope_valid_delay_n", scope, int(data.valid_delay.sum()), len(data), "dossiers",
                            method="Count after categorical filters, before requested time filtering; complete prepared extract")
        record("filter_scope", before, offset, filters= json.dumps(plan.filters, sort_keys=True))
        temporal = "compare_periods" in plan.operation_sequence
        requested = plan.time_scope
        if temporal and not requested:
            requested = list(select_comparison_periods(bundle["metadata"]) or ())
            plan.time_scope = requested
            details["period_selection"] = "Last two complete contiguous months, selected explicitly by Python because no named months were supplied."
        allowed = complete_periods(bundle["metadata"])
        details["complete_periods"] = allowed
        if requested and (any(p not in allowed for p in requested) or (temporal and (len(requested) != 2 or requested[0] >= requested[1]))):
            insufficient("Les périodes demandées ne sont pas entièrement disponibles ou sont mal ordonnées. Aucune autre période n'est substituée. Périodes complètes : " + ", ".join(allowed) + ".", [population_id])
        elif temporal and len(requested) != 2:
            insufficient("Deux mois complets comparables ne sont pas disponibles; aucune comparaison temporelle n'est calculée.", [population_id])
        elif data.empty:
            insufficient("Aucun dossier ne correspond aux filtres demandés.", [population_id])
        elif temporal:
            details["limitations"].extend(["period_selection", "sample_window"])
            before, offset = perf_counter(), len(facts)
            valid = data.loc[data.valid_delay & data.closure_month.isin(requested)]
            metric_method = {"mean_resolution_days": "mean", "median_resolution_days": "median"}.get(plan.metric)
            if metric_method is None:
                insufficient("La comparaison temporelle de ce KPI n'est pas prise en charge par cet outil de délais.", [population_id])
            else:
                values, ids, counts = [], [], []
                for period in requested:
                    part = valid.loc[valid.closure_month.eq(period), "resolution_days"]
                    value = float(getattr(part, metric_method)()) if len(part) else None
                    counts.append(len(part)); values.append(value)
                    ids.append(add(plan.metric, scope, value, len(part), period=period))
                if min(counts) < MIN_GROUP_N:
                    insufficient("Les périodes demandées ont moins de 30 délais valides; aucun classement opérationnel ni conclusion de hausse n'est publié.", ids)
                    record("compare_periods", before, offset, status="skipped")
                else:
                    delta = values[1] - values[0]
                    delta_id = add("delta_" + plan.metric, scope, delta, min(counts), period="/".join(requested))
                    label = "moyenne" if metric_method == "mean" else "médiane"
                    text = (f"Sur le périmètre global filtré {json.dumps(scope, ensure_ascii=False)}, la {label} de clôture passe de "
                            f"{values[0]:.6f} jours (n={counts[0]}) en {requested[0]} à {values[1]:.6f} jours (n={counts[1]}) en {requested[1]}; "
                            f"variation descriptive {delta:+.6f} jours. " + ("Une hausse globale est observée." if delta > 1e-10 else "Aucune hausse globale n'est observée."))
                    section("period_comparison", text, [*ids, delta_id], "Examiner les contributions et les dossiers comparables avant d'interpréter cette variation.", "trend")
                    details["period_comparison"] = dict(periods=requested, values=values, counts=counts, delta=delta)
                    record("compare_periods", before, offset, details["period_comparison"])
                    if metric_method == "mean":
                        before, offset = perf_counter(), len(facts)
                        decomposition = decompose_delay_change(data, tuple(requested), ("borough", "complaint_type", "agency"))
                        details["tables"]["global_decomposition"] = decomposition
                        ids = [add(key, scope, decomposition[key], min(counts), period="/".join(requested),
                                   method="Exact symmetric mean decomposition: within + composition + unmatched") for key in
                               ("within_days", "composition_days", "unmatched_days", "residual_days")]
                        text = (f"Décomposition de la variation globale : évolution interne {decomposition['within_days']:+.6f} jours; "
                                f"composition {decomposition['composition_days']:+.6f}; groupes nouveaux ou absents {decomposition['unmatched_days']:+.6f}; "
                                f"résidu {decomposition['residual_days']:.9f}. Tous les groupes entrent dans cette identité arithmétique.")
                        eligible = sorted((g for g in decomposition["groups"] if g["rankable"] and g["contribution_days"] > 0),
                                          key=lambda g: (-g["contribution_days"], g["borough"], g["complaint_type"], g["agency"]))
                        details["tables"]["priority_segments"] = eligible if delta > 1e-10 else []
                        if delta > 1e-10:
                            text += " Priorités parmi les segments éligibles, par contribution positive décroissante (effectif minimal dans chaque mois) : "
                            for g in eligible[:3]:
                                filters = {d: g[d] for d in ("borough", "complaint_type", "agency")}
                                ids.append(add("segment_contribution_days", filters, g["contribution_days"], min(g["n_baseline"], g["n_current"]), period="/".join(requested)))
                                text += (f"{json.dumps(filters, ensure_ascii=False)} : {g['contribution_days']:+.6f} jours, "
                                         f"n={g['n_baseline']}/{g['n_current']}; ")
                            if not eligible:
                                text += "aucun segment éligible; compléter les observations."
                        else:
                            text += " Aucun classement de contributeurs à une hausse globale n'est présenté."
                        section("contributions", text, ids,
                                "Pour les segments cités, extraire les étapes de traitement et les horodatages sur les mêmes mois; distinguer attente et traitement, "
                                "contrôler les catégories et la complexité, puis confronter l'hypothèse de ralentissement aux journaux. Vérifier séparément la sélection des dossiers exclus.", "trend")
                        record("decompose_mean_change", before, offset, dict(residual=decomposition["residual_days"]))
        else:
            if requested:
                data = data.loc[data.closure_month.isin(requested)]
            valid = data.loc[data.valid_delay].copy()
            method = {"median_resolution_days": "median", "mean_resolution_days": "mean",
                      "sample_request_count": "size", "closure_rate": "rate"}.get(plan.metric)
            if method is None:
                insufficient("Ce KPI n'est pas pris en charge dans ce classement; aucune autre métrique n'est substituée.", [population_id])
            else:
                if method == "size":
                    valid = data.copy()
                if method == "rate":
                    valid = data.loc[data.created_date.notna()].copy()
                    details["limitations"].append("cohort_followup")
                label = {"median": "médiane", "mean": "moyenne", "size": "volume", "rate": "taux de clôture"}[method]
                def groups(dimensions, subset):
                    rows = []
                    for keys, part in subset.groupby(dimensions, sort=True):
                        keys = keys if isinstance(keys, tuple) else (keys,)
                        if any(k in {"__MISSING__", "Unspecified", "UNKNOWN"} for k in keys):
                            continue
                        value = (100 * (part.state.eq("closed").sum() if "state" in part else part.closed_date.notna().sum()) / len(part)
                                 if method == "rate" else float(getattr(part.resolution_days, method)()))
                        rows.append(dict(filters=dict(zip(dimensions, keys)), value=float(value), n=len(part), eligible=len(part) >= MIN_GROUP_N))
                    return sorted(rows, key=lambda r: (-r["value"], json.dumps(r["filters"], sort_keys=True)))
                def publish(rows, key, prefix):
                    ids, texts = [], []
                    for row in rows:
                        if not row["eligible"]:
                            continue
                        ids.append(add(plan.metric, row["filters"], row["value"], row["n"],
                                       unit="%" if method == "rate" else "dossiers" if method == "size" else "jours calendaires", period=requested[0] if requested else None))
                        texts.append(f"{json.dumps(row['filters'], ensure_ascii=False)} : {label}={row['value']:.6f}, n={row['n']}")
                    details["tables"][key] = rows
                    if ids:
                        section(key, prefix + "; ".join(texts) + ".", ids,
                                "Contrôler la composition et les étapes de traitement des dossiers dans ce périmètre avant toute décision.")
                    return ids
                ranking = "rank_groups" in plan.operation_sequence
                drill = "lock_scope" in plan.operation_sequence
                target_dimension = "borough" if "borough" in plan.dimensions or drill else (plan.dimensions or ["borough"])[0]
                if ranking:
                    before, offset = perf_counter(), len(facts)
                    rows = groups([target_dimension], valid)
                    ids = publish(rows, "ranking", f"Classement par {label} décroissante des groupes éligibles (n ≥ {MIN_GROUP_N}) : ")
                    eligible = [r for r in rows if r["eligible"]]
                    if eligible:
                        winner = eligible[0]
                        ties = [r for r in eligible if r["value"] == winner["value"]]
                        sections[-1]["text"] += " Maximum éligible : " + json.dumps(winner["filters"], ensure_ascii=False) + "."
                        if len(ties) > 1:
                            sections[-1]["text"] += " Ex æquo; ordre lexical déterministe utilisé pour le drill-down : " + json.dumps([r["filters"] for r in ties], ensure_ascii=False) + "."
                        details["winner"] = winner
                    else:
                        insufficient("Aucun groupe n'atteint 30 délais valides pour le classement demandé.", [population_id])
                    record("rank_groups", before, offset, dict(dimension=target_dimension))
                    if drill and eligible:
                        before, offset = perf_counter(), len(facts)
                        plan.locked_filters = winner["filters"].copy()
                        for dim, value in plan.locked_filters.items():
                            valid = valid.loc[valid[dim].eq(value)]
                        record("lock_scope", before, offset, filters=json.dumps(plan.locked_filters))
                elif drill:
                    before, offset = perf_counter(), len(facts)
                    plan.locked_filters = {key: values[0] for key, values in plan.filters.items() if len(values) == 1}
                    record("lock_scope", before, offset, filters=json.dumps(plan.locked_filters))
                if details["decision"] == "answer":
                    before, offset = perf_counter(), len(facts)
                    dims = [d for d in plan.dimensions if not drill or d != target_dimension]
                    dims = dims or (["complaint_type"] if drill else ["borough"])
                    for dim in dims:
                        if ranking and not drill and dim == target_dimension:
                            continue
                        rows = groups([dim], valid)
                        for row in rows:
                            row["filters"] = {**scope, **plan.locked_filters, **row["filters"]}
                        publish(rows, "comparison_" + dim, f"Comparaison descriptive limitée à {json.dumps(plan.locked_filters or scope, ensure_ascii=False)} par {dim} : ")
                    record("compare_scoped_groups", before, offset, dimensions=",".join(dims), filters=json.dumps(plan.locked_filters or scope))
                    if "prioritize_segments" in plan.operation_sequence:
                        before, offset = perf_counter(), len(facts)
                        segment_dims = [d for d in ("complaint_type", "agency") if d not in plan.locked_filters]
                        rows = groups(segment_dims, valid)
                        for row in rows:
                            row["filters"] = {**scope, **plan.locked_filters, **row["filters"]}
                        ids = publish([row for row in rows if row["eligible"]][:3], "priorities", f"Segments à investiguer par {label} décroissante, puis ordre lexical, hors groupes de faible effectif : ")
                        details["tables"]["all_segments"] = rows
                        if ids:
                            sections[-1]["recommendation"] = ("Dans les segments cités uniquement, extraire les horodatages des étapes, affectations et complexité; "
                                "distinguer temps d'attente et de traitement, comparer des dossiers de même catégorie et vérifier les exclusions. "
                                "L'hypothèse d'une étape plus lente serait soutenue par des temps d'étape supérieurs à complexité comparable, "
                                "et réfutée si l'écart disparaît après ce contrôle.")
                        record("prioritize_segments", before, offset, filters=json.dumps(plan.locked_filters or scope))
                    if not sections:
                        insufficient("Aucun groupe éligible ne permet la comparaison demandée.", [population_id])
    details["plan"] = plan.model_dump(mode="json")
    representatives = [s["evidence_ids"][0] for s in sections if s["evidence_ids"]]
    representatives += sections[-1]["evidence_ids"][-3:] if sections else []
    representatives += [fid for s in sections for fid in s["evidence_ids"]]
    details["primary_evidence_ids"] = list(dict.fromkeys(representatives))[:6]
    # The report retains every published fact and its trace. Granite receives a short relevant subset.
    details["limitations"] = sorted(set(details["limitations"]))
    json.dumps(details, allow_nan=False)
    return results, details
