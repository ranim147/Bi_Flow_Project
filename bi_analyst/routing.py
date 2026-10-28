"""Deterministic business intent parsing. No model and no conversational cache."""
from __future__ import annotations

import re
import unicodedata

from .models import AnalyticalPlan


def normalized(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)).replace("’", "'")


def route_question(question: str) -> AnalyticalPlan:
    text = normalized(question)
    forbidden = bool(re.search(r"(?:sans(?:\s+effectuer\s+d['e])?|aucune?|pas(?:\s+d['e])?)\s*(?:analyse|comparaison)\s+temporelle|sans\s+temporel", text))
    # Remove explicitly rejected metrics before interpreting the requested KPI.
    positive = re.sub(r"(?:pas|sans|non)\s+(?:les?\s+|la\s+)?(?:moyennes?|medianes?)", "", text)
    metric = ("median_resolution_days" if "median" in positive else
              "mean_resolution_days" if "moyen" in positive else
              "sample_request_count" if re.search(r"\bvolume|nombre de (?:plaintes|demandes|dossiers)", positive) else
              "closure_rate" if "taux" in positive else "median_resolution_days")
    dimensions = [dim for dim, pattern in (
        ("borough", r"arrondissement|borough"),
        ("complaint_type", r"categorie|type[s]? de (?:plainte|reclamation|demande)|complaint"),
        ("agency", r"agence|agency")) if re.search(pattern, text)]
    filters = {}
    boroughs = [b for b in ("BROOKLYN", "BRONX", "MANHATTAN", "QUEENS", "STATEN ISLAND")
                if re.search(r"(?<!\w)" + normalized(b) + r"(?!\w)", text)]
    if boroughs:
        filters["borough"] = boroughs
    months = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
              "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
    years = re.findall(r"\b(20\d{2})\b", text)
    periods = [(m.start(), m.group()) for m in re.finditer(r"\b20\d{2}-(?:0[1-9]|1[0-2])\b", text)]
    for m in re.finditer(r"\b(" + "|".join(months) + r")\b(?:\s+(20\d{2}))?", text):
        year = m.group(2) or (years[0] if len(set(years)) == 1 else None)
        periods.append((m.start(), f"{year}-{months[m.group(1)]:02d}" if year else "year_required"))
    time_scope = list(dict.fromkeys(value for _, value in sorted(periods)))
    unsupported = []
    causal = bool(re.search(r"\b(?:prouve|prouver|demontre|demontrer|preuve causale)\b", positive))
    if causal:
        unsupported.append("unproven_causality")
    if re.search(r"recrut|combien.*(?:personn|agent|effectif)|dimensionn.*(?:effectif|personnel)", text):
        unsupported.append("staffing_without_capacity_data")
    if re.search(r"\bsla\b|objectif contractuel|depassement.*cible", text):
        unsupported.append("unknown_sla")
    if "year_required" in time_scope:
        unsupported.append("year_required")
    ranking = bool(re.search(r"plus elev|plus long|maximal|maximum|classement|classer|classe\b", positive))
    drilldown = bool(re.search(r"drill|dans cet arrondissement|dans ce borough|pour l'arrondissement qui", text)) or (
        ranking and "borough" in dimensions and bool(set(dimensions) & {"agency", "complaint_type"}))
    contextual_reference = bool(re.match(r"\s*dans cet arrondissement", text))
    if drilldown and not boroughs and (not ranking or contextual_reference):
        unsupported.append("reference_scope_required")
    if "causes possibles" in text and not dimensions and not re.search(r"delai|retard|cloture", text):
        unsupported.append("analysis_context_required")
    temporal = not forbidden and (len(time_scope) > 1 or bool(re.search(r"hausse|baisse|augment|degrad|entre deux mois|variation|evolution", text)))
    if forbidden and len(time_scope) > 1:
        unsupported.append("conflicting_time_scope")
    if len(time_scope) > 2:
        unsupported.append("unsupported_period_count")
    intent = ("justified_refusal" if unsupported else "ranking" if ranking else "drilldown" if drilldown else
              "anomaly" if re.search(r"anomal|inhabituel|pic\b", text) else
              "trend" if re.search(r"tendance|mensuel", text) and not temporal else
              "investigation" if re.search(r"investig|hypothes|raisons|causes possibles|degrad|contribu|backlog", text) else
              "comparison" if re.search(r"compar", text) else "summary")
    if temporal and intent == "summary":
        intent = "comparison"
    explicit_metric = bool(re.search(r"median|moyen", positive))
    scoped = bool(unsupported or ranking or drilldown or time_scope or forbidden or explicit_metric or "agency" in dimensions
                  or (intent == "investigation" and not temporal and "delai" in text))
    if scoped and intent in {"trend", "anomaly"}:
        unsupported.append("unsupported_temporal_test")
        intent = "justified_refusal"
    if temporal and not explicit_metric:
        metric = "mean_resolution_days"
    if scoped:
        if unsupported:
            operations = ["check_capabilities"]
        elif temporal:
            operations = ["filter_scope", "compare_periods"]
            if metric == "mean_resolution_days":
                operations.append("decompose_mean_change")
        else:
            operations = ["filter_scope"]
            if ranking:
                operations.append("rank_groups")
            if drilldown:
                operations.append("lock_scope")
            operations.append("compare_scoped_groups")
            if drilldown or "investig" in text:
                operations.append("prioritize_segments")
        executor = "scoped"
    else:
        from .decision_tools import decision_question
        executor = "investigation" if decision_question(question) else "legacy"
        operations = ["compare_periods", "decompose_mean_change", "investigate"] if executor == "investigation" else [intent]
        if executor == "investigation":
            metric = "mean_resolution_days"
    return AnalyticalPlan(intent=intent, metric=metric, dimensions=dimensions, filters=filters,
                          time_scope=time_scope, temporal_forbidden=forbidden,
                          operation_sequence=operations, unsupported_request=unsupported, executor=executor)
