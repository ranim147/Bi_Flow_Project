"""Évaluation du MVP sur un jeu synthétique à défauts et anomalies connus.

    python -m biflow.evaluation                 # rapport dans data/processed/evaluation/
    python -m biflow.evaluation --out <dossier> --seed 7

Mesure les critères de l'étude (étape 3, « Évaluation du MVP ») :
profilage, génération de SQL (LLM et baseline sans LLM), KPI = calcul manuel, détection
d'anomalies, fiabilité des insights, détection par l'Auditeur d'écarts volontaires, temps.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from biflow.agents import default_agents
from biflow.agents.analyste_bi import AnalysteBIAgent
from biflow.agents.base import RunContext
from biflow.agents.dashboard_generator import DashboardGeneratorAgent
from biflow.agents.kpi_semantic import KPISemanticAgent
from biflow.agents.quality_etl import QualityETLAgent
from biflow.analysis.numbers import unsupported_numbers
from biflow.common.config import PROCESSED_DIR
from biflow.common.logging import setup_logging
from biflow.common.reference import BOROUGH_SEED, SLA_SEED
from biflow.evaluation.synthetic import expected_kpis, generate
from biflow.llm.client import get_llm, llm_label
from biflow.orchestrator.graph import run_pipeline
from biflow.orchestrator.state import DatasetRef, PipelineState, RunStatus
from biflow.orchestrator.tracing import MemoryTraceLogger
from biflow.semantic.catalog import load_catalog
from biflow.semantic.sandbox import SQLSandbox
from biflow.semantic.text_to_sql import baseline_to_sql

log = logging.getLogger("biflow.evaluation")

# --------------------------------------------------------------------------- questions de test
# Réponse attendue calculée en pandas sur l'instantané nettoyé : ("scalar", x) | ("key", k) | ("map", {})
Expected = tuple[str, Any]
POP = {b["borough"]: b["population"] for b in BOROUGH_SEED}
SLA = {s["complaint_type"]: s["target_hours"] for s in SLA_SEED}


def _noise(d: pd.DataFrame) -> pd.Series:
    return d["complaint_type"].str.startswith("Noise")


def _closed(d: pd.DataFrame) -> pd.DataFrame:
    return d[d["is_closed"]]


def _sla(d: pd.DataFrame) -> float:
    return 100 * float(d["within_sla"].dropna().astype(bool).mean())


def _boroughs(d: pd.DataFrame) -> pd.DataFrame:
    """Les 5 boroughs, sans UNSPECIFIED (pas un borough)."""
    return d[d["borough"].isin(list(POP))]


def _median_by_borough(d: pd.DataFrame) -> pd.Series:
    return _closed(_boroughs(d)).groupby("borough")["resolution_hours"].median()


QUESTIONS: list[tuple[str, Callable[[pd.DataFrame], Expected]]] = [
    ("Combien de demandes au total ?", lambda d: ("scalar", len(d))),
    ("Combien de demandes à Brooklyn ?", lambda d: ("scalar", (d.borough == "BROOKLYN").sum())),
    ("Combien de demandes de bruit dans le Bronx ?",
     lambda d: ("scalar", (_noise(d) & (d.borough == "BRONX")).sum())),
    ("Combien de plaintes pour bruit résidentiel à Queens ?",
     lambda d: ("scalar", ((d.complaint_type == "Noise - Residential") & (d.borough == "QUEENS")).sum())),
    ("Combien de demandes d'éclairage public ?",
     lambda d: ("scalar", (d.complaint_type == "Street Light Condition").sum())),
    ("Combien de demandes sur l'état de la chaussée à Brooklyn ?",
     lambda d: ("scalar", ((d.complaint_type == "Street Condition") & (d.borough == "BROOKLYN")).sum())),
    ("Quel est le délai médian de résolution ?",
     lambda d: ("scalar", _closed(d).resolution_hours.median())),
    ("Quel est le délai médian de résolution à Manhattan ?",
     lambda d: ("scalar", _closed(d[d.borough == "MANHATTAN"]).resolution_hours.median())),
    ("Quel est le délai médian de résolution des plaintes pour bruit ?",
     lambda d: ("scalar", _closed(d[_noise(d)]).resolution_hours.median())),
    ("Quel est le taux de clôture ?", lambda d: ("scalar", d.is_closed.mean())),
    ("Quel est le taux de clôture à Staten Island ?",
     lambda d: ("scalar", d[d.borough == "STATEN ISLAND"].is_closed.mean())),
    ("Quel est le taux de respect du SLA ?", lambda d: ("scalar", _sla(d))),
    ("Quel est le taux de respect des délais cibles pour le bruit résidentiel ?",
     lambda d: ("scalar", _sla(d[d.complaint_type == "Noise - Residential"]))),
    ("Combien de demandes pour 10 000 habitants ?",
     lambda d: ("scalar", 10_000 * len(_boroughs(d)) / sum(POP.values()))),
    ("Combien de demandes pour 10 000 habitants dans le Bronx ?",
     lambda d: ("scalar", 10_000 * (d.borough == "BRONX").sum() / POP["BRONX"])),
    ("Quel borough a le plus de demandes ?", lambda d: ("key", _boroughs(d).borough.value_counts().idxmax())),
    ("Quel borough a le moins de demandes ?", lambda d: ("key", _boroughs(d).borough.value_counts().idxmin())),
    ("Quel borough a le délai médian de résolution le plus long ?",
     lambda d: ("key", _median_by_borough(d).idxmax())),
    ("Quel type de plainte est le plus fréquent ?", lambda d: ("key", d.complaint_type.value_counts().idxmax())),
    ("Combien de demandes par borough ?", lambda d: ("map", _boroughs(d).borough.value_counts().to_dict())),
    ("Combien de demandes à Manhattan ?", lambda d: ("scalar", (d.borough == "MANHATTAN").sum())),
    ("Combien de demandes dans le Queens ?", lambda d: ("scalar", (d.borough == "QUEENS").sum())),
    ("Combien de demandes de bruit à Staten Island ?",
     lambda d: ("scalar", (_noise(d) & (d.borough == "STATEN ISLAND")).sum())),
    ("Quel est le délai médian de résolution dans le Bronx ?",
     lambda d: ("scalar", _closed(d[d.borough == "BRONX"]).resolution_hours.median())),
    ("Quel est le taux de clôture des plaintes pour bruit ?", lambda d: ("scalar", d[_noise(d)].is_closed.mean())),
    ("Quel est le taux de respect du SLA à Brooklyn ?", lambda d: ("scalar", _sla(d[d.borough == "BROOKLYN"]))),
    ("Combien de demandes pour 10 000 habitants à Manhattan ?",
     lambda d: ("scalar", 10_000 * (d.borough == "MANHATTAN").sum() / POP["MANHATTAN"])),
    ("Quel borough a le délai médian de résolution le moins long ?",
     lambda d: ("key", _median_by_borough(d).idxmin())),
    ("Quel type de plainte est le moins fréquent ?", lambda d: ("key", d.complaint_type.value_counts().idxmin())),
    ("Quel jour a le plus de demandes ?", lambda d: ("key", str(d.created_day.value_counts().idxmax()))),
]
# Reformulations hors des règles de la baseline : mesurent ce qu'apporte le LLM.
PARAPHRASES: list[tuple[str, Callable[[pd.DataFrame], Expected]]] = [
    ("Dans quel quartier de New York y a-t-il le plus de signalements ?",
     lambda d: ("key", _boroughs(d).borough.value_counts().idxmax())),
    ("Quel est le temps moyen de résolution à Queens, en heures ?",
     lambda d: ("scalar", _closed(d[d.borough == "QUEENS"]).resolution_hours.mean())),
    ("Combien de signalements de bruit ont été faits un samedi ?",
     lambda d: ("scalar", (_noise(d) & (d.weekday == 5)).sum())),
    ("Combien de demandes restent ouvertes ?", lambda d: ("scalar", (~d.is_closed).sum())),
    ("Quelle agence traite le plus de demandes ?", lambda d: ("key", d.agency.value_counts().idxmax())),
    ("Combien de demandes ont été créées entre 22 h et 6 h ?",
     lambda d: ("scalar", ((d.created_hour >= 22) | (d.created_hour < 6)).sum())),
    ("Quelle est la durée moyenne de traitement des demandes clôturées, en heures ?",
     lambda d: ("scalar", _closed(d).resolution_hours.mean())),
    ("Combien de demandes ne sont pas encore clôturées à Brooklyn ?",
     lambda d: ("scalar", (~d.is_closed & (d.borough == "BROOKLYN")).sum())),
    ("Combien de signalements la police (NYPD) a-t-elle reçus ?", lambda d: ("scalar", (d.agency == "NYPD").sum())),
    ("Combien de demandes ont été adressées au département des transports (DOT) ?",
     lambda d: ("scalar", (d.agency == "DOT").sum())),
    ("Combien de signalements ont été reçus un dimanche ?", lambda d: ("scalar", (d.weekday == 6).sum())),
    ("Combien de demandes ont été déposées le week-end ?", lambda d: ("scalar", (d.weekday >= 5).sum())),
    ("À quelle heure de la journée reçoit-on le plus de demandes ?",
     lambda d: ("key", str(d.created_hour.value_counts().idxmax()))),
    ("Combien de types de plaintes différents y a-t-il ?", lambda d: ("scalar", d.complaint_type.nunique())),
    ("Quel est le délai de résolution le plus long observé, en heures ?",
     lambda d: ("scalar", d.resolution_hours.max())),
    ("Combien de plaintes pour bruit ont été réglées en moins de 2 heures ?",
     lambda d: ("scalar", (_noise(d) & d.is_closed & (d.resolution_hours < 2)).sum())),
    ("Quelle proportion des demandes du Queens a été clôturée ?",
     lambda d: ("scalar", d[d.borough == "QUEENS"].is_closed.mean())),
    ("Quel quartier compte le plus de plaintes pour l'éclairage public ?",
     lambda d: ("key", _boroughs(d[d.complaint_type == "Street Light Condition"]).borough.value_counts().idxmax())),
    ("Combien de demandes ont été créées en janvier 2026 ?",
     lambda d: ("scalar", (pd.to_datetime(d.created_day).dt.month == 1).sum())),
    ("En moyenne, combien de demandes reçoit-on par jour ?",
     lambda d: ("scalar", len(d) / d.created_day.nunique())),
    ("Quelle part des plaintes concerne le bruit, en pourcentage ?",
     lambda d: ("scalar", 100 * _noise(d).mean())),
]
# Questions hors du périmètre des données : la bonne réponse est un refus motivé.
OUT_OF_SCOPE = [
    "Pourquoi les délais sont-ils plus longs dans le Bronx ?",
    "Combien d'agents faut-il recruter pour réduire les délais ?",
    "Combien de demandes recevra-t-on le mois prochain ?",
]


def answer_matches(rows: list[dict], expected: Expected) -> bool:
    if not rows:
        return False
    kind, value = expected
    if kind == "scalar":
        got = rows[0].get("value", next(iter(rows[0].values())))
        try:
            return got is not None and abs(float(got) - float(value)) <= 1e-6 * max(1.0, abs(float(value)))
        except (TypeError, ValueError):
            return False

    def first_text(r: dict) -> str | None:
        """Modalité de la ligne : première colonne hors `value` / `n`, sinon `value` elle-même
        (« SELECT created_hour AS value ... LIMIT 1 »)."""
        key = next((v for k, v in r.items() if k not in ("value", "n", "masked")), None)
        if key is None and kind == "key":
            key = r.get("value")
        if isinstance(key, float) and key.is_integer():
            key = int(key)
        return None if key is None else str(key)[:10] if isinstance(key, str) and key[:4].isdigit() else str(key)
    if kind == "key":
        return first_text(rows[0]) == str(value)
    got = {first_text(r): r.get("value") for r in rows}
    return all(got.get(k) is not None and abs(float(got[k]) - v) < 1e-6 for k, v in value.items())


# --------------------------------------------------------------------------- écarts volontaires (audit)
class _TamperedKPI(KPISemanticAgent):
    """KPI affiché différent du résultat de sa requête."""

    def run(self, state):
        out = super().run(state)
        out.kpis[0].value += 1
        return out


class _TamperedSnapshot(QualityETLAgent):
    """Instantané modifié après calcul de son empreinte."""

    def run(self, state):
        out = super().run(state)
        df = pd.read_parquet(out.snapshot_path)
        df.iloc[1:].to_parquet(out.snapshot_path, index=False)
        return out


class _TamperedInsight(AnalysteBIAgent):
    """Insight citant un chiffre non justifié (contrôle de l'agent désactivé)."""

    def run(self, state):
        out = super().run(state)
        out.insights[0].text += " Soit 999 demandes de plus que prévu."
        return out

    def validate(self, output, state):
        return []


class _TamperedDashboard(DashboardGeneratorAgent):
    """Valeur affichée sur une carte KPI différente du KPI vérifié."""

    def run(self, state):
        out = super().run(state)
        out.charts[0].value = (out.charts[0].value or 0) * 2 + 1
        return out


SABOTAGES = [("quality_etl", _TamperedSnapshot), ("kpi_semantic", _TamperedKPI),
             ("analyste_bi", _TamperedInsight), ("dashboard_generator", _TamperedDashboard)]


# --------------------------------------------------------------------------- évaluation
def _ctx() -> RunContext:
    return RunContext(trace_logger=MemoryTraceLogger(), max_retries=0, retry_delay_seconds=0,
                      sleep=lambda _: None)


def evaluate(out_dir: Path, seed: int = 42) -> dict[str, Any]:
    ds = generate(seed=seed)
    data_path = ds.write(out_dir)
    dataset = DatasetRef(file_path=str(data_path))
    all_questions = [(q, f, "catalogue") for q, f in QUESTIONS] + [(q, f, "reformulation") for q, f in PARAPHRASES]
    questions = [q for q, _, _ in all_questions] + OUT_OF_SCOPE
    llm = get_llm()

    t0 = time.perf_counter()
    state = run_pipeline(dataset, ctx=_ctx(), questions=questions, output_dir=out_dir / "runs")
    pipeline_s = time.perf_counter() - t0
    if state.status != RunStatus.SUCCESS:
        raise RuntimeError(f"run de référence en échec : {state.status} {state.errors} {state.findings}")
    out = state.outputs
    clean = pd.read_parquet(out["quality_etl"]["snapshot_path"])
    results: dict[str, Any] = {"seed": seed, "run_id": state.run_id, "llm": llm_label(llm)}

    # 1. Profilage
    found = {(i["column"], i["issue_type"]): i["count"] for i in out["data_profiler"]["issues"]}
    defects = [{"column": d.column, "type": d.issue_type, "injected": d.count,
                "detected": found.get((d.column, d.issue_type), 0)} for d in ds.defects]
    results["profiling"] = {"defects": defects,
                            "recall": sum(d["detected"] >= d["injected"] for d in defects) / len(defects)}

    # 2. Question -> SQL : mode du pipeline + baseline seule, sur le même instantané
    sandbox = SQLSandbox({"requests": clean, "boroughs": pd.DataFrame(BOROUGH_SEED),
                          "sla_targets": pd.DataFrame(SLA_SEED)})
    catalog = load_catalog()
    rows_sql = []
    t_base = time.perf_counter()
    answers = out["kpi_semantic"]["answers"]
    for (question, expect, kind), answer in zip(all_questions, answers[:len(all_questions)], strict=True):
        expected = expect(clean)
        sql = baseline_to_sql(question, catalog)
        try:
            base_rows = sandbox.query(sql) if sql else []
        except Exception:  # noqa: BLE001
            base_rows = []
        raw = next((q["rows"] for q in out["kpi_semantic"]["queries"] if q["id"] == answer["query_id"]), [])
        rows_sql.append({"question": question, "set": kind, "pipeline_mode": answer["mode"],
                         "pipeline_ok": answer_matches(raw, expected),
                         "baseline_ok": answer_matches(base_rows, expected),
                         "duration_s": answer.get("duration_s", 0.0), "sql": answer["sql"],
                         "cross_check": answer.get("cross_check")})
    baseline_s = time.perf_counter() - t_base
    scope = answers[len(all_questions):]
    results["scope"] = {
        "out_of_scope": [{"question": a["question"], "refused": a["mode"] == "refused", "refusal": a["refusal"]}
                         for a in scope],
        "refusal_rate": sum(a["mode"] == "refused" for a in scope) / len(scope),
        "false_refusals": [r["question"] for r in rows_sql if r["pipeline_mode"] == "refused"],
    }
    sandbox.close()
    def accuracy(key: str, kind: str | None = None) -> float:
        rows = [r for r in rows_sql if kind is None or r["set"] == kind]
        return sum(r[key] for r in rows) / len(rows)

    checked = {c: [r["pipeline_ok"] for r in rows_sql if r["cross_check"] == c]
               for c in ("concordant", "divergent", "non vérifiable")}
    results["cross_check"] = {c: {"questions": len(v), "correct": sum(v)} for c, v in checked.items() if v}
    results["sql"] = {"questions": rows_sql,
                      "pipeline_accuracy": accuracy("pipeline_ok"),
                      "baseline_accuracy": accuracy("baseline_ok"),
                      "by_set": {k: {"pipeline": accuracy("pipeline_ok", k), "baseline": accuracy("baseline_ok", k)}
                                 for k in ("catalogue", "reformulation")}}

    # 3. KPI = calcul manuel sur la vérité terrain
    manual = expected_kpis(ds.truth)
    kpis = {k["name"]: k["value"] for k in out["kpi_semantic"]["kpis"]}
    results["kpi"] = [{"kpi": k, "pipeline": kpis.get(k), "manual": v,
                       "ok": kpis.get(k) is not None and abs(kpis[k] - v) <= 1e-9 * max(1, abs(v))}
                      for k, v in manual.items()]

    # 4. Anomalies : bonne zone et bon jour
    detected = out["analyste_bi"]["anomalies"]
    hits = [a for a in ds.anomalies
            if any(d["zone"] == a["zone"] and d["day"] == a["day"] for d in detected)]
    injected_days = {a["day"] for a in ds.anomalies}
    false_pos = [d for d in detected if d["day"] not in injected_days]
    results["anomalies"] = {"injected": ds.anomalies, "recall": len(hits) / len(ds.anomalies),
                            "detected": detected, "false_positives": len(false_pos)}

    # 5. Fiabilité des insights + traçabilité du dashboard
    insights = out["analyste_bi"]["insights"]
    bad = [i["title"] for i in insights if unsupported_numbers(i["text"], i["evidence"])]
    untraced = [c["id"] for c in out["dashboard_generator"]["charts"]
                if c["chart_type"] in ("kpi_card", "bar", "bar_h", "line") and not c["query_ids"]]
    results["insights"] = {"count": len(insights), "unsupported": bad,
                           "written_by": sorted({i["written_by"] for i in insights})}
    results["traceability"] = {"untraced_charts": untraced, "audit_approved": out["auditor_xai"]["approved"]}

    # 6. Audit : écarts volontaires
    audit_rows = []
    for target, agent_cls in SABOTAGES:
        agents = default_agents()
        agents[target] = agent_cls()
        s: PipelineState = run_pipeline(dataset, agents=agents, ctx=_ctx(), output_dir=out_dir / "sabotage")
        flagged = sorted({f.target_step for f in s.findings if f.severity == "error"})
        audit_rows.append({"sabotage": agent_cls.__doc__.strip(), "target": target, "flagged": flagged,
                           "detected": target in flagged, "status": s.status.value, "published": s.published})
    results["audit"] = {"cases": audit_rows,
                        "detection_rate": sum(r["detected"] for r in audit_rows) / len(audit_rows)}
    llm_rows = [r for r in rows_sql if r["pipeline_mode"] == "llm"]
    results["timing"] = {"pipeline_s": round(pipeline_s, 2), "baseline_sql_s": round(baseline_s, 3),
                         "questions_pipeline_s": round(sum(r["duration_s"] for r in rows_sql), 2),
                         "llm_mean_s": round(sum(r["duration_s"] for r in llm_rows) / len(llm_rows), 2)
                         if llm_rows else None}
    return results


def to_markdown(r: dict[str, Any]) -> str:
    pct = lambda x: f"{100 * x:.0f} %"
    ok = lambda b: "oui" if b else "**non**"
    lines = [
        f"# Évaluation BIFlow (seed {r['seed']}, run {r['run_id']})",
        "",
        f"LLM : {r['llm'] or 'aucun (BIFLOW_LLM_PROVIDER / MISTRAL_API_KEY) : le pipeline utilise la baseline'}",
        "",
        "| Critère | Mesure | Objectif | Atteint |",
        "| :-- | :-- | :-- | :-- |",
        (f"| Profilage | {pct(r['profiling']['recall'])} des défauts injectés détectés | ≥ 95 % | "
        f"{ok(r['profiling']['recall'] >= 0.95)} |"),
        (f"| Génération de SQL (pipeline) | {pct(r['sql']['pipeline_accuracy'])} de {len(r['sql']['questions'])} "
        f"questions | ≥ 80 % | {ok(r['sql']['pipeline_accuracy'] >= 0.8)} |"),
        f"| Génération de SQL (baseline sans LLM) | {pct(r['sql']['baseline_accuracy'])} | référence | — |",
        *[f"| … dont questions « {k} » | pipeline {pct(v['pipeline'])}, baseline {pct(v['baseline'])} | — | — |"
          for k, v in r["sql"]["by_set"].items()],
        *[f"| … contrôle croisé « {c} » | {v['correct']}/{v['questions']} réponses du LLM justes | — | — |"
          for c, v in r.get("cross_check", {}).items()],
        (f"| KPI = calcul manuel | {sum(k['ok'] for k in r['kpi'])}/{len(r['kpi'])} | tous | "
        f"{ok(all(k['ok'] for k in r['kpi']))} |"),
        (f"| Détection d'anomalies | {pct(r['anomalies']['recall'])} (faux positifs : "
        f"{r['anomalies']['false_positives']}) | ≥ 80 % | {ok(r['anomalies']['recall'] >= 0.8)} |"),
        (f"| Fiabilité des insights | {len(r['insights']['unsupported'])} insight(s) avec un nombre non justifié "
        f"sur {r['insights']['count']} | 0 | {ok(not r['insights']['unsupported'])} |"),
        (f"| Audit | {pct(r['audit']['detection_rate'])} des écarts volontaires détectés | 100 % | "
        f"{ok(r['audit']['detection_rate'] == 1)} |"),
        (f"| Traçabilité | {len(r['traceability']['untraced_charts'])} graphique(s) sans requête d'origine | 0 | "
        f"{ok(not r['traceability']['untraced_charts'])} |"),
        (f"| Questions hors périmètre | {pct(r['scope']['refusal_rate'])} refusées avec explication ; "
        f"{len(r['scope']['false_refusals'])} refus à tort | 100 % ; 0 | "
        f"{ok(r['scope']['refusal_rate'] == 1 and not r['scope']['false_refusals'])} |"),
        f"| Temps | pipeline complet {r['timing']['pipeline_s']} s ; {len(r['sql']['questions'])} questions : "
        f"pipeline {r['timing']['questions_pipeline_s']} s"
        + (f" (LLM {r['timing']['llm_mean_s']} s par question)" if r['timing']['llm_mean_s'] else "")
        + f", baseline seule {r['timing']['baseline_sql_s']} s | mesurer | — |",
        "",
        "## Défauts injectés",
        "",
        "| Colonne | Type | Injectés | Détectés |",
        "| :-- | :-- | --: | --: |",
        *[f"| {d['column']} | {d['type']} | {d['injected']} | {d['detected']} |" for d in r["profiling"]["defects"]],
        "",
        "## Questions",
        "",
        "| Question | Jeu | Mode pipeline | Pipeline | Baseline |",
        "| :-- | :-- | :-- | :-- | :-- |",
        *[f"| {q['question']} | {q['set']} | {q['pipeline_mode']} | {ok(q['pipeline_ok'])} | {ok(q['baseline_ok'])} |"
          for q in r["sql"]["questions"]],
        "",
        "## Écarts volontaires (Auditeur)",
        "",
        "| Écart | Étape visée | Étapes signalées | Statut du run | Publié |",
        "| :-- | :-- | :-- | :-- | :-- |",
        *[f"| {a['sabotage']} | {a['target']} | {', '.join(a['flagged'])} | {a['status']} | "
          f"{'oui' if a['published'] else 'non'} |" for a in r["audit"]["cases"]],
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    setup_logging("WARNING")
    p = argparse.ArgumentParser(description="Évaluation du MVP BIFlow sur données synthétiques")
    p.add_argument("--out", default=str(PROCESSED_DIR / "evaluation"))
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)
    out_dir = Path(args.out)
    results = evaluate(out_dir, args.seed)
    report = to_markdown(results)
    (out_dir / "evaluation.md").write_text(report, encoding="utf-8")
    (out_dir / "evaluation.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(report)
    print(f"Rapport : {out_dir / 'evaluation.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
