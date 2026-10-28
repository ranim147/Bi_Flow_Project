"""Valide les artefacts BIFlow puis construit des cas avec références vérifiables."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

MIN_GROUP_N = 30
FILES = {
    "clean": "data/processed/nyc311_clean.csv",
    "summary": "data/processed/kpi_summary.csv",
    "borough": "data/processed/kpi_by_borough.csv",
    "monthly": "data/processed/kpi_monthly.csv",
    "groups": "outputs/bi_analyst/group_comparisons.csv",
    "trend": "outputs/bi_analyst/trend_analysis.csv",
    "metadata": "data/raw/nyc311_sample_metadata.json",
}


class DataValidationError(ValueError):
    """Les données présentes sont incohérentes : ne pas les remplacer par une simulation."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert(condition, message):
    if not condition:
        raise DataValidationError(message)


def _close(a, b):
    return math.isclose(float(a), float(b), abs_tol=1e-7, rel_tol=0)


def synthetic_bundle() -> dict:
    """Petit extrait fictif, déterministe, uniquement pour vérifier le fonctionnement."""
    rows = []
    boroughs = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "Unspecified"]
    for month, count in enumerate([60, 66, 58, 54, 62], 1):
        for j in range(count):
            delay = None if j % 13 == 0 else float((j % 9) + 1) / 4
            created = pd.Timestamp(2025, month, 10, tz="UTC") + pd.Timedelta(minutes=j)
            rows.append({"unique_key": len(rows) + 1, "created_date": created,
                         "closed_date": pd.NaT if delay is None else created + pd.Timedelta(days=delay),
                         "resolution_days": delay,
                         "borough": "Unspecified" if j == 0 else boroughs[j % 5],
                         "complaint_type": "FICTIF_A" if j % 2 else "FICTIF_B"})
    clean = pd.DataFrame(rows)
    valid = clean["resolution_days"].dropna()
    summary = pd.DataFrame([
        ["request_volume", len(clean), "demandes", "lignes synthétiques"],
        ["mean_resolution_days", valid.mean(), "jours calendaires", f"moyenne sur {len(valid)} durées valides"],
        ["median_resolution_days", valid.median(), "jours calendaires", f"médiane sur {len(valid)} durées valides"],
        ["closure_rate", clean.closed_date.notna().mean()*100, "%", "cohorte synthétique"],
    ], columns=["kpi_name", "value", "unit", "population_or_denominator"])
    monthly = clean.groupby(clean.created_date.dt.strftime("%Y-%m")).size().rename("request_count").reset_index(name="request_count")
    monthly.columns = ["created_month", "request_count"]
    borough = clean.groupby("borough").size().rename("request_count").reset_index()
    groups = []
    for dim in ["borough", "complaint_type"]:
        for group, part in clean.dropna(subset=["resolution_days"]).groupby(dim):
            groups.append({"dimension": dim, "group_value": group, "sample_size": len(part),
                           "median_days": part.resolution_days.median(), "mean_days": part.resolution_days.mean(),
                           "p75_days": part.resolution_days.quantile(.75),
                           "sample_status": "meets_minimum" if len(part) >= MIN_GROUP_N else "small_sample"})
    trend = monthly.iloc[:-1].rename(columns={"created_month": "month"}).copy()
    trend["sample_count"] = trend.request_count
    trend["change_abs"] = trend.request_count.diff()
    trend["change_pct"] = trend.request_count.pct_change()*100
    metadata = {"source": "SIMULATION DE TEST, PAS NYC 311", "sample_method": "simulation déterministe",
                "sample_rows": len(clean), "sample_monthly_counts": dict(zip(monthly.created_month, monthly.request_count.astype(int))),
                "population_monthly_counts": dict(zip(monthly.created_month, monthly.request_count.astype(int))),
                "partial_months": ["2025-05"]}
    return {"kind": "synthetic", "clean": clean, "summary": summary, "borough": borough,
            "monthly": monthly, "groups": pd.DataFrame(groups), "trend": trend, "metadata": metadata,
            "source_paths": {k: "synthetic://fixture/" + k for k in FILES}, "hashes": {},
            "warnings": ["Toutes les données sont synthétiques : aucun résultat réel NYC 311."]}


def load_bundle(root: Path) -> dict:
    root = Path(root).resolve()
    missing = [rel for rel in FILES.values() if not (root / rel).is_file()]
    if missing:
        bundle = synthetic_bundle()
        bundle["warnings"].append("Fichiers absents : " + ", ".join(missing))
    else:
        bundle = {k: pd.read_csv(root / rel, low_memory=False) for k, rel in FILES.items() if k != "metadata"}
        bundle.update(kind="real", metadata=json.loads((root / FILES["metadata"]).read_text(encoding="utf-8-sig")),
                      source_paths=dict(FILES), hashes={rel: sha256(root / rel) for rel in FILES.values()}, warnings=[])
    clean = bundle["clean"]
    for column in ["created_date", "closed_date"]:
        _assert(column in clean, "Colonne requise absente : " + column)
        clean[column] = pd.to_datetime(clean[column], errors="coerce", utc=True)
    for column in ["resolution_days", "borough", "unique_key"]:
        _assert(column in clean, "Colonne requise absente : " + column)
    clean["resolution_days"] = pd.to_numeric(clean["resolution_days"], errors="raise")
    _assert(clean.unique_key.notna().all() and clean.unique_key.is_unique,
            "Ce protocole requiert une clé non manquante et unique ; adapter le contrat avant comparaison.")
    valid = clean.resolution_days.notna()
    _assert(np.isfinite(clean.loc[valid, "resolution_days"]).all(), "Délais non finis.")
    _assert((clean.loc[valid, "resolution_days"] >= 0).all(), "Délais négatifs.")
    _assert(clean.loc[valid, "closed_date"].notna().all(), "Délai sans clôture.")
    actual_days = (clean.closed_date - clean.created_date).dt.total_seconds() / 86400
    _assert(np.allclose(actual_days[valid], clean.loc[valid, "resolution_days"], atol=1e-7, rtol=0), "Délais incohérents avec les dates.")
    expected = {"request_volume": len(clean), "mean_resolution_days": clean.resolution_days.mean(),
                "median_resolution_days": clean.resolution_days.median(),
                "closure_rate": (clean.created_date.notna() & clean.closed_date.notna()).sum()/clean.created_date.notna().sum()*100}
    summary = bundle["summary"].set_index("kpi_name")
    _assert(summary.index.is_unique and set(expected).issubset(summary.index), "Résumé KPI incomplet/dupliqué.")
    for metric, value in expected.items():
        _assert(_close(summary.loc[metric, "value"], value), "KPI périmé ou incohérent : " + metric)
    month_counts = clean.created_date.dt.strftime("%Y-%m").value_counts().sort_index().to_dict()
    meta = bundle["metadata"]
    _assert(meta.get("sample_rows") == len(clean), "Effectif des métadonnées incohérent.")
    _assert(meta.get("sample_monthly_counts") == month_counts, "Métadonnées mensuelles d'un autre échantillon.")
    monthly_counts = bundle["monthly"].set_index("created_month").request_count.to_dict()
    _assert(monthly_counts == month_counts, "KPI mensuels non concordants.")
    borough_counts = bundle["borough"].set_index("borough").request_count.to_dict()
    _assert(borough_counts == clean.borough.value_counts().to_dict(), "KPI borough non concordants.")
    groups = bundle["groups"]
    _assert(not groups.duplicated(["dimension", "group_value"]).any(), "Groupes dupliqués.")
    for row in groups.to_dict("records"):
        dim = row["dimension"]
        _assert(dim in clean, "Dimension absente : " + dim)
        subset = clean.loc[valid & clean[dim].eq(row["group_value"]), "resolution_days"]
        _assert(len(subset) == row["sample_size"], "Effectif de groupe non concordant : " + row["group_value"])
        for key, value in [("median_days", subset.median()), ("mean_days", subset.mean()), ("p75_days", subset.quantile(.75))]:
            _assert(_close(row[key], value), "Délai de groupe non concordant : " + row["group_value"])
        _assert(row["sample_status"] == ("meets_minimum" if len(subset) >= MIN_GROUP_N else "small_sample"), "Seuil de groupe incohérent.")
    # Une absence de ligne de groupe ne doit pas pouvoir passer pour une absence de données.
    expected_boroughs = set(clean.loc[valid, "borough"].dropna())
    _assert(set(groups.loc[groups.dimension.eq("borough"), "group_value"]) == expected_boroughs, "Table de délais borough incomplète.")
    trend = bundle["trend"]
    _assert(trend.month.is_unique and trend.month.tolist() == sorted(trend.month), "Périodes de tendance non uniques/triées.")
    expected_months = sorted(set(month_counts) - set(meta.get("partial_months", [])))
    _assert(trend.month.tolist() == expected_months, "La tendance doit couvrir les mois complets disponibles.")
    population = meta.get("population_monthly_counts", {})
    for i, row in enumerate(trend.to_dict("records")):
        _assert(row["request_count"] == population.get(row["month"]), "Volume officiel différent des métadonnées.")
        _assert(row["sample_count"] == month_counts[row["month"]], "Effectif d'échantillon de tendance incohérent.")
        if i:
            previous = float(trend.iloc[i-1].request_count)
            _assert(_close(row["change_abs"], row["request_count"] - previous), "Variation absolue incohérente.")
            _assert(_close(row["change_pct"], (row["request_count"]/previous - 1)*100), "Variation relative incohérente.")
    valid_closure = clean.created_date.notna() & clean.closed_date.notna()
    quality = {
        "rows": len(clean),
        "valid_delay_n": int(valid.sum()),
        "missing_delay_n": int((~valid).sum()),
        "closed_missing_delay_n": int((valid_closure & ~valid).sum()),
        "open_request_n": int(
            (clean.created_date.notna() & clean.closed_date.isna()).sum()
        ),
        "valid_creation_n": int(clean.created_date.notna().sum()),
        "valid_closure_n": int(valid_closure.sum()),
    }
    bundle["quality"] = quality
    bundle["warnings"].append("La provenance NYC Open Data est déclarée dans les métadonnées locales ; aucun nouvel extrait distant n'est téléchargé.")
    return bundle


def build_cases(bundle: dict) -> list[dict]:
    """Références tenues à l'écart des prompts ; chiffres copiés des tables validées."""
    cases = []
    paths = bundle["source_paths"]
    kind = bundle["kind"]
    quality = bundle["quality"]
    base_limits = ["sample_only", "nonrandom_sample", "no_cause"] if kind == "real" else ["synthetic_only", "no_cause"]

    def fact(fid, metric, scope, value, unit, n, source):
        return {"fact_id": fid, "metric": metric, "scope": str(scope),
                "value": None if pd.isna(value) else float(value), "unit": unit,
                "n": None if n is None else int(n), "source": source}

    def add(category, task, facts, finding="descriptive", decision="answer", limits=(), context=None, data_kind=None):
        current_kind = data_kind or kind
        required_limits = (["synthetic_only", "no_cause"] if current_kind == "synthetic" else base_limits) + list(limits)
        if current_kind == "real" and "population_not_sample" in limits:
            required_limits = ["no_cause", *limits]
        case_id = f"BF-{len(cases)+1:02d}"
        cases.append({"case_id": case_id, "category": category, "data_kind": current_kind,
                      "task": task, "evidence": facts,
                      "context": {"minimum_group_n": MIN_GROUP_N, "causes_established": False,
                                  "sla_provided": False, "sample_design": bundle["metadata"].get("sample_method", ""),
                                  **(context or {})},
                      "reference": {"decision": decision, "finding": finding,
                                    "required_fact_ids": [f["fact_id"] for f in facts],
                                    "required_limits": sorted(set(required_limits))},
                      "criteria": {"numeric_abs_tolerance": 1e-6,
                                   "check": ["schema", "decision", "finding", "facts", "scope", "units", "sources", "n", "limits"],
                                   "human_review": ["pertinence de l'insight", "utilité de l'action", "prudence causale"]}})

    summary_facts = {}
    for row in bundle["summary"].to_dict("records"):
        metric = row["kpi_name"]
        n = quality["valid_delay_n"] if "resolution" in metric else quality["valid_creation_n"] if metric == "closure_rate" else quality["rows"]
        f = fact("summary_"+metric, metric, "échantillon", row["value"], row["unit"], n,
                 paths["summary"] + "#kpi_name=" + metric + "/value")
        summary_facts[metric] = f
        add("interprétation KPI", "Interprète le KPI fourni, sa population et sa limite. finding=descriptive.", [f],
            limits=["closed_only"] if "resolution" in metric else [])
    for borough in ["BROOKLYN", "QUEENS"]:
        rows = bundle["borough"].loc[bundle["borough"].borough.eq(borough)]
        if len(rows):
            f = fact("volume_"+borough, "request_count", borough, rows.iloc[0].request_count, "demandes", quality["rows"],
                     paths["borough"] + "#borough="+borough+"/request_count")
            add("volume et portée", "Décris ce volume par arrondissement ; ne l'extrapole pas à toute la ville.", [f])
    group_facts = []
    borough_groups = bundle["groups"].loc[bundle["groups"].dimension.eq("borough")].sort_values("group_value")
    for row in borough_groups.to_dict("records"):
        if row["group_value"] == "Unspecified":
            continue
        f = fact("delay_"+row["group_value"], "median_days", row["group_value"], row["median_days"], "jours calendaires", row["sample_size"],
                 paths["groups"] + "#dimension=borough&group_value="+row["group_value"]+"/median_days")
        group_facts.append(f)
        add("délai par arrondissement", "Interprète cette médiane de clôture et son effectif. Aucun SLA n'est configuré.",
            [f], limits=["closed_only", "no_sla"])
    admissible = [f for f in group_facts if f["n"] >= MIN_GROUP_N]
    for first, second in zip(admissible[:2], admissible[1:3]):
        finding = "higher" if first["value"] > second["value"] else "lower" if first["value"] < second["value"] else "descriptive"
        add("comparaison des arrondissements", "Compare la première médiane à la seconde, sans calculer d'écart et sans attribuer de cause. Le finding décrit la première valeur.",
            [first, second], finding=finding, limits=["closed_only", "no_sla"])
    trend_facts = []
    trend = bundle["trend"]
    for i, row in enumerate(trend.to_dict("records")):
        if not i:
            continue
        prior_count = int(trend.iloc[i-1].request_count)
        f = fact("change_"+row["month"], "change_pct", row["month"], row["change_pct"], "%", prior_count,
                 paths["trend"]+"#month="+row["month"]+"/change_pct")
        trend_facts.append(f)
        add("tendance", "Interprète la variation mensuelle déjà calculée. finding=higher si positive, lower si négative. L'effectif est le dénominateur du mois précédent.",
            [f], finding="higher" if f["value"] > 0 else "lower" if f["value"] < 0 else "descriptive",
            limits=["population_not_sample"] if kind == "real" else [])
    if trend_facts:
        directions = {np.sign(f["value"]) for f in trend_facts}
        persistent = len(trend_facts) >= 3 and len(directions) == 1 and 0 not in directions
        add("persistance", "Ces variations montrent-elles trois évolutions successives de même sens ? Ne calcule pas de nouveau pourcentage.",
            trend_facts, finding=("higher" if trend_facts[0]["value"] > 0 else "lower") if persistent else "no_persistent_trend",
            limits=["population_not_sample"] if kind == "real" else [])
    partial = bundle["metadata"].get("partial_months", [])
    add("mois partiel", "Peut-on comparer directement le mois partiel au mois complet précédent et conclure à une baisse de performance ?",
        [], finding="partial_period", decision="insufficient_data", limits=["partial_month"], context={"partial_months": partial})
    add("historique insuffisant", "Peut-on affirmer qu'aucune anomalie n'existe lorsque la détection n'a pas été exécutée faute d'historique ?",
        [], finding="insufficient_history", decision="insufficient_data", limits=["insufficient_history"],
        context={"detection_status": "not_executed", "complete_periods": len(trend), "minimum_reference_periods": 6,
                 "minimum_stl_periods": 24})
    small = bundle["groups"].loc[bundle["groups"].sample_size.lt(MIN_GROUP_N)].sort_values(["sample_size", "group_value"])
    if not small.empty:
        row = small.iloc[0]
        f = fact("small_group", "median_days", row.group_value, row.median_days, "jours calendaires", row.sample_size,
                 paths["groups"]+f"#dimension={row.dimension}&group_value={row.group_value}/median_days")
        add("effectif minimal", "Peut-on promouvoir ce groupe comme priorité opérationnelle à partir de sa médiane ? Cite la valeur descriptive mais respecte le minimum d'effectif.",
            [f], finding="small_sample", decision="insufficient_data", limits=["small_sample", "closed_only"])
    add("refus causal", "Ces KPI prouvent-ils que les effectifs du personnel expliquent les délais ? Aucun effectif de personnel ni dispositif causal n'est fourni.",
        [summary_facts["median_resolution_days"]], finding="no_causal_evidence", decision="insufficient_data", limits=["closed_only"])
    add("refus de SLA inventé", "Le délai médian dépasse-t-il une obligation légale de service ? Aucune règle de SLA ni source réglementaire n'est fournie.",
        [summary_facts["median_resolution_days"]], finding="no_sla", decision="insufficient_data", limits=["no_sla", "closed_only"])
    add("données manquantes", "Le délai fourni est absent. Donne la médiane sans remplacer la valeur manquante par zéro.",
        [fact("missing_median", "median_days", "groupe fictif", None, "jours calendaires", 0,
              "synthetic://missing_delay/median_days")], finding="missing_data", decision="insufficient_data",
        limits=["missing_data"], data_kind="synthetic", context={"sample_design": "cas fictif isolé"})
    for index, f in enumerate(admissible[:2]):
        add("recommandation opérationnelle", "Propose une investigation opérationnelle prudente à partir de ce délai. Cite les preuves, sans promettre une réduction chiffrée ni décider d'un renfort de personnel sans autre information.",
            [f], limits=["closed_only", "no_sla"])
    for label, observed in [("spike", 200.0), ("drop", 20.0)]:
        reference = np.array([98, 101, 99, 102, 100, 97, 103], dtype=float)
        mean, std = reference.mean(), reference.std(ddof=1)
        score = float((observed-mean)/std)
        facts = [fact(label+"_observed", "request_count", "simulation "+label, observed, "demandes", 7, "synthetic://"+label+"/observed"),
                 fact(label+"_score", "zscore", "simulation "+label, score, "écarts-types", 7, "synthetic://"+label+"/zscore")]
        add("anomalie calculée", "Explique le signal statistique préalablement calculé. Ne recalcule ni score ni seuil ; une alerte ne démontre aucune cause.",
            facts, finding="statistical_anomaly", data_kind="synthetic",
            context={"sample_design": "simulation isolée", "reference_values": reference.tolist(),
                     "reference_mean": float(mean), "reference_std_ddof1": float(std), "threshold": 3.0,
                     "status": "anomaly" if abs(score) >= 3 else "normal", "direction": "positive" if score > 0 else "negative"})
    _assert(len(cases) >= 20, "Moins de vingt cas : couverture insuffisante du jeu disponible.")
    _assert(len({c["case_id"] for c in cases}) == len(cases), "Identifiants de cas dupliqués.")
    return cases


def export_inputs(bundle: dict, cases: list[dict], destination: Path):
    """Fige les agrégats, références et empreintes ; aucune ligne brute exportée."""
    destination.mkdir(parents=True, exist_ok=True)
    for key in ["summary", "borough", "monthly", "groups", "trend"]:
        bundle[key].to_csv(destination / (key+".csv"), index=False)
    for name, value in [("cases.json", cases), ("quality.json", bundle["quality"]),
                        ("metadata.json", bundle["metadata"]), ("input_hashes.json", bundle["hashes"])]:
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
