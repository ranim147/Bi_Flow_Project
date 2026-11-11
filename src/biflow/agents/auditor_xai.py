"""Agent 6 — Auditeur / XAI : c'est le CODE qui vérifie, pas le LLM.

Contrôles (chaque échec devient un `AuditFinding` adressé à l'étape fautive) :
- instantané : l'empreinte recalculée = empreinte enregistrée par Qualité/ETL ;
- flux : lignes profilées = lignes lues par Qualité/ETL ;
- KPI affiché = résultat enregistré de sa requête, sur le même instantané ;
- reproductibilité : chaque requête rejouée sur l'instantané redonne le même résultat ;
- recalcul indépendant (pandas, sans SQL) des principaux KPI ;
- seuil anti-identification respecté dans les ventilations ;
- insights : chaque nombre du texte est justifié par ses `evidence`, et les `evidence`
  qui citent un KPI ont sa valeur ;
- dashboard : empreinte de la spec, valeurs affichées = KPI et ventilations vérifiés.
L'`explanation` décrit, en clair, comment les chiffres ont été obtenus.

Boucle de rétroaction : un finding `error` renvoie le run (une fois) vers l'étape la plus en
amont signalée, avec le message dans `state.corrections[<étape>]` ; si le problème persiste
au 2e audit, le run est UNRELIABLE et rien n'est publié. Un finding `warning` est rapporté
sans renvoi.

Entrée  : tout le state (outputs, validations, history, errors) + audit.trace_log
Sortie  : AuditOutput -> state.outputs["auditor_xai"]
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.analysis.numbers import unsupported_numbers
from biflow.common.reference import load_reference
from biflow.common.snapshot import file_hash, frame_hash, read_snapshot
from biflow.orchestrator.state import STEP_ORDER, AuditFinding, Check, PipelineState
from biflow.semantic.sandbox import CATALOG_MAX_ROWS, SQLSandbox

log = logging.getLogger(__name__)


class AuditCheck(BaseModel):
    name: str
    passed: bool
    target_step: str
    message: str = ""


class AuditOutput(AgentOutput):
    approved: bool
    findings: list[AuditFinding] = Field(default_factory=list)
    explanation: str = ""
    checks: list[AuditCheck] = Field(default_factory=list)


def same(a: Any, b: Any, rel: float = 1e-9) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], rel) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y, rel) for x, y in zip(a, b, strict=True))
    return a == b


def same_rows(a: list[dict], b: list[dict]) -> bool:
    """Mêmes lignes, dans n'importe quel ordre (l'ordre des égalités n'est pas garanti en SQL)."""
    def key(row: dict) -> str:
        return repr(sorted((k, round(v, 9) if isinstance(v, float) else v) for k, v in row.items()))
    return same(sorted(a, key=key), sorted(b, key=key))


def independent_kpis(clean: pd.DataFrame) -> dict[str, float]:
    """Recalcul en pandas, sans réutiliser le SQL du catalogue."""
    closed = clean[clean["is_closed"]]
    with_target = clean["within_sla"].dropna()
    out = {"total_requests": float(len(clean)), "closure_rate": float(clean["is_closed"].mean())}
    if len(closed):
        out["median_resolution_hours"] = float(closed["resolution_hours"].median())
    if len(with_target):
        out["sla_compliance_rate"] = 100 * float(with_target.astype(bool).mean())
    return out


class AuditorXAIAgent(BaseAgent):
    name = "auditor_xai"
    output_model = AuditOutput

    def __init__(self, flag_step: str | None = None, persistent: bool = False) -> None:
        """Paramètres de SIMULATION (démo/tests de la boucle de rétroaction) :

        flag_step  : étape à signaler comme fautive, en plus des contrôles réels
        persistent : signaler aussi après correction (=> run UNRELIABLE)
        """
        self.flag_step = flag_step
        self.persistent = persistent

    def run(self, state: PipelineState) -> AuditOutput:
        checks: list[AuditCheck] = []

        def check(name: str, passed: bool, target: str, message: str = "") -> bool:
            checks.append(AuditCheck(name=name, passed=bool(passed), target_step=target, message=message))
            return passed

        profile = state.output("data_profiler")
        etl = state.output("quality_etl")
        kpi_out = state.output("kpi_semantic")
        analysis = state.output("analyste_bi")
        dash = state.output("dashboard_generator")

        # 1. Données
        check("flux_profil_etl", profile["row_count"] == etl["rows_in"], "quality_etl",
              f"profil {profile['row_count']} lignes, ETL {etl['rows_in']} lignes lues")
        clean = read_snapshot(etl["snapshot_path"])
        recomputed = frame_hash(clean)
        check("empreinte_instantane", recomputed == etl["snapshot_hash"], "quality_etl",
              f"empreinte recalculée {recomputed[:12]}, enregistrée {etl['snapshot_hash'][:12]}")
        check("lignes_instantane", len(clean) == etl["rows_out"], "quality_etl",
              f"{len(clean)} lignes dans l'instantané, {etl['rows_out']} annoncées")

        # 2. KPI = requête enregistrée, rejouée sur le même instantané
        queries = {q["id"]: q for q in kpi_out.get("queries", [])}
        stale = [q["id"] for q in queries.values() if q["snapshot_hash"] != etl["snapshot_hash"]]
        check("requetes_meme_instantane", not stale, "kpi_semantic",
              f"requêtes sur un autre instantané : {stale}" if stale else "ok")
        for k in kpi_out["kpis"]:
            q = queries.get(k.get("query_id") or "")
            recorded = q["rows"][0].get("value") if q and q["rows"] else None
            check(f"kpi_egal_requete:{k['name']}", q is not None and same(k["value"], recorded),
                  "kpi_semantic", f"affiché {k['value']}, résultat enregistré {recorded}")
        sandbox = SQLSandbox({"requests": clean, **load_reference(state.dataset)})
        try:
            replay_failed = []
            for q in queries.values():
                try:
                    if not same_rows(sandbox.query(q["sql"], CATALOG_MAX_ROWS), q["rows"]):  # déjà acceptée
                        replay_failed.append(q["id"])
                except Exception as exc:  # noqa: BLE001 — une requête non rejouable est un écart
                    replay_failed.append(f"{q['id']} ({type(exc).__name__})")
        finally:
            sandbox.close()
        check("reproductibilite", not replay_failed, "kpi_semantic",
              f"résultats différents au rejeu : {replay_failed}" if replay_failed
              else f"{len(queries)} requêtes rejouées, résultats identiques")
        kpis = {k["name"]: k["value"] for k in kpi_out["kpis"]}
        for name, value in independent_kpis(clean).items():
            if name in kpis:
                check(f"recalcul_independant:{name}", same(kpis[name], value, rel=1e-6), "kpi_semantic",
                      f"SQL {kpis[name]}, pandas {value}")
        min_n = kpi_out.get("min_group_size", 10)
        exposed = [f"{b['name']}={r['key']}" for b in kpi_out.get("breakdowns", []) for r in b["rows"]
                   if not r["masked"] and r["n"] < min_n]
        check("seuil_anti_identification", not exposed, "kpi_semantic",
              f"agrégats < {min_n} demandes non masqués : {exposed}" if exposed else "ok")
        for b in kpi_out.get("breakdowns", []):
            q = queries.get(b["query_id"])
            raw = {str(r[b["dimension"]]): r["value"] for r in (q["rows"] if q else [])}
            wrong = [r["key"] for r in b["rows"] if not r["masked"] and not same(r["value"], raw.get(r["key"]))]
            check(f"ventilation_egale_requete:{b['name']}", q is not None and not wrong, "kpi_semantic",
                  f"valeurs différentes du résultat enregistré : {wrong}" if wrong else "ok")

        # 3. Insights
        for i in analysis["insights"]:
            unsupported = unsupported_numbers(i["text"], i["evidence"])
            check(f"insight_nombres:{i['title']}", not unsupported, "analyste_bi",
                  f"nombres sans preuve : {unsupported}" if unsupported else "ok")
            wrong = [k for k, v in i["evidence"].items() if k in kpis and not same(v, kpis[k])]
            check(f"insight_preuves:{i['title']}", not wrong, "analyste_bi",
                  f"preuves différentes des KPI : {wrong}" if wrong else "ok")

        # 4. Dashboard
        spec_ok = bool(dash.get("spec_path")) and file_hash(dash["spec_path"]) == dash.get("spec_hash")
        check("empreinte_dashboard", spec_ok, "dashboard_generator", "spec du dashboard inchangée")
        breakdowns = {b["name"]: {r["key"]: r["value"] for r in b["rows"]} for b in kpi_out.get("breakdowns", [])}
        for c in dash["charts"]:
            if c["chart_type"] == "kpi_card":
                check(f"dashboard_kpi:{c['id']}", same(c["value"], kpis.get(c["id"])), "dashboard_generator",
                      f"affiché {c['value']}, KPI {kpis.get(c['id'])}")
            elif c["id"] in breakdowns:
                wrong = [d["key"] for d in c["data"] if not same(d["value"], breakdowns[c["id"]].get(d["key"]))]
                check(f"dashboard_donnees:{c['id']}", not wrong, "dashboard_generator",
                      f"valeurs différentes : {wrong}" if wrong else "ok")

        findings = [AuditFinding(target_step=c.target_step, message=f"{c.name} : {c.message}")
                    for c in checks if not c.passed]
        if self.flag_step and (self.persistent or state.feedback_rounds == 0):
            findings.append(AuditFinding(target_step=self.flag_step,
                                         message=f"simulation : incohérence détectée dans {self.flag_step}"))
        warnings = [AuditFinding(target_step=v.step, severity="warning",
                                 message=f"contrôle {c.name} en avertissement : {c.message}")
                    for v in state.validations.values() for c in v.checks
                    if not c.passed and c.severity == "warning" and v.step in STEP_ORDER]
        return AuditOutput(
            approved=not findings,
            findings=findings + warnings,
            checks=checks,
            explanation=self.explain(state, checks, findings, clean),
            input_rows=len(clean),
            notes=[f"{sum(c.passed for c in checks)}/{len(checks)} contrôles réussis"],
        )

    @staticmethod
    def explain(state: PipelineState, checks: list[AuditCheck], findings: list[AuditFinding],
                clean: pd.DataFrame) -> str:
        etl = state.output("quality_etl")
        kpi_out = state.output("kpi_semantic")
        analysis = state.output("analyste_bi")
        retries = sum(h.attempts - 1 for h in state.history)
        lines = [
            (f"Données : {etl['rows_in']} demandes lues, {etl['rows_dropped']} supprimées par les règles "
            f"de nettoyage ({len(etl['rules_applied'])} règles tracées), {etl['rows_out']} conservées dans "
            f"l'instantané {etl['snapshot_hash'][:12]}."),
            (f"KPI : {len(kpi_out['kpis'])} indicateurs et {len(kpi_out.get('breakdowns', []))} ventilations "
            f"calculés par SQL (DuckDB, lecture seule) à partir du catalogue Git ; "
            f"{len(kpi_out.get('queries', []))} requêtes enregistrées et rejouées. Agrégats de moins de "
            f"{kpi_out.get('min_group_size', 10)} demandes masqués."),
            (f"Insights : {len(analysis['insights'])} insights, chiffres issus des KPI et de la détection "
            f"d'anomalies ({analysis.get('detection_method') or 'n/a'}) ; rédaction : "
            f"{analysis['llm_model'] + ' (nombres vérifiés)' if analysis.get('llm_model') else 'gabarits sans LLM'}."),
            (f"Contrôles d'audit : {sum(c.passed for c in checks)}/{len(checks)} réussis ; "
            f"{retries} nouvel(s) essai(s) d'étape pendant le run."),
        ]
        if findings:
            lines.append("Écarts : " + " | ".join(f"{f.target_step} — {f.message}" for f in findings))
        else:
            lines.append("Aucun écart : chaque chiffre affiché correspond à une requête enregistrée et rejouable.")
        return "\n".join(lines)

    def validate(self, output: AuditOutput, state: PipelineState) -> list[Check]:
        auditable = set(STEP_ORDER) - {self.name}
        bad = [f.target_step for f in output.findings if f.target_step not in auditable]
        blocking = any(f.severity == "error" for f in output.findings)
        return [
            Check(name="finding_targets_valid", passed=not bad,
                  message=f"étapes inconnues : {bad}" if bad else "ok"),
            Check(name="approval_consistent", passed=output.approved != blocking,
                  message="approved doit être False ssi il existe un finding 'error'"),
        ]
