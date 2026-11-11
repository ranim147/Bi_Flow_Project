"""Agent 3 — KPI & couche sémantique (LLM : traduction question -> SQL).

- Les KPI viennent du catalogue `semantic/kpi_catalog.yaml` (définitions communes, revues
  dans Git) et sont calculés par SQL (DuckDB) sur l'instantané nettoyé : jamais par le LLM.
- Chaque requête est enregistrée (`queries`) avec l'empreinte de l'instantané et son
  résultat : l'Auditeur vérifie que la valeur affichée = résultat enregistré, et la rejoue.
- Seuil anti-identification : tout agrégat portant sur moins de N demandes
  (BIFLOW_MIN_GROUP_SIZE, 10 par défaut) est masqué.
- Questions métier (`state.questions`) : traduites en SQL par le LLM configuré (Mistral ou
  modèle local, voir `biflow.llm.client`), sinon par la baseline à règles ; exécutées dans le
  bac à sable (lecture seule, tables autorisées, temps et lignes limités). La question est
  contrôlée (données personnelles) avant l'envoi. Une question hors du périmètre des données
  (cause, effectifs, prévision) reçoit un refus motivé, sans requête ni appel au LLM.
⚠ ref.sla_targets contient des valeurs PLACEHOLDER (is_placeholder = true) à valider.

Unités autorisées et plages contrôlées automatiquement :
    count >= 0 | hours >= 0 | percent [0, 100] | ratio [0, 1] | per_10k >= 0

Entrée  : state.outputs["quality_etl"], state.questions
Sortie  : KPIOutput -> state.outputs["kpi_semantic"]
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from biflow.agents.base import AgentOutput, BaseAgent
from biflow.common.config import get_settings
from biflow.common.reference import load_reference
from biflow.llm.client import LLMClient, get_llm, llm_label
from biflow.llm.privacy import redact
from biflow.orchestrator.state import Check, PipelineState
from biflow.orchestrator.validation import check_in_range
from biflow.semantic.catalog import load_catalog
from biflow.semantic.sandbox import CATALOG_MAX_ROWS, SQLSandbox, UnsafeQueryError
from biflow.semantic.scope import out_of_scope
from biflow.semantic.text_to_sql import baseline_to_sql, dimension_values, llm_to_sql

log = logging.getLogger(__name__)

KPI_RANGES: dict[str, tuple[float | None, float | None]] = {
    "count": (0, None),
    "hours": (0, None),
    "percent": (0, 100),
    "ratio": (0, 1),
    "per_10k": (0, None),
}


class KPIValue(BaseModel):
    name: str
    value: float
    unit: Literal["count", "hours", "percent", "ratio", "per_10k"]
    definition: str
    dimensions: dict[str, str] = Field(default_factory=dict, description="ex. {'borough': 'BRONX'}")
    query_id: str | None = Field(None, description="requête de `queries` qui a produit la valeur")


class BreakdownRow(BaseModel):
    key: str
    value: float | None = Field(description="None si masqué (moins de N demandes)")
    n: int
    masked: bool = False


class Breakdown(BaseModel):
    name: str
    unit: str
    dimension: str
    definition: str
    query_id: str
    rows: list[BreakdownRow]


class QueryRecord(BaseModel):
    """Journal d'audit d'une requête : quoi, sur quelles données, quel résultat."""

    id: str
    purpose: str
    sql: str
    snapshot_hash: str
    executed_at: str
    rows: list[dict[str, Any]]


class Answer(BaseModel):
    question: str
    question_sent: str | None = Field(None, description="question envoyée au LLM (après contrôle)")
    personal_data_found: list[str] = Field(default_factory=list)
    mode: Literal["llm", "baseline", "refused", "none"]
    sql: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list, description="résultat après seuil")
    query_id: str | None = None
    error: str | None = None
    refusal: str | None = Field(None, description="question hors périmètre : explication")
    duration_s: float = Field(0.0, description="temps de traduction + exécution")
    cross_check: Literal["concordant", "divergent", "non vérifiable"] | None = Field(
        None, description="réponse du LLM comparée à celle de la baseline (si elle reconnaît la question)")
    baseline_sql: str | None = None


class KPIOutput(AgentOutput):
    kpis: list[KPIValue]
    breakdowns: list[Breakdown] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)
    queries: list[QueryRecord] = Field(default_factory=list)
    semantic_model: dict[str, Any] = Field(default_factory=dict)
    snapshot_hash: str | None = None
    min_group_size: int = 10
    llm_model: str | None = None


def _answer_key(row: dict[str, Any]) -> tuple[str | None, float | None]:
    """(modalité, valeur) d'une ligne de réponse, quels que soient les noms de colonnes."""
    key = next((v for k, v in row.items() if k not in ("value", "n", "masked")), None)
    value = row.get("value")
    if isinstance(value, str):
        key, value = key or value, None
    try:
        number = None if value is None else float(value)
    except (TypeError, ValueError):
        number = None
    return (None if key is None else str(key)), number


def same_answer(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """Deux réponses donnent le même résultat (même nombre de lignes, mêmes modalités,
    mêmes valeurs à 1e-6 près), même si leurs SQL diffèrent."""
    if not a or len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=True):
        (ka, va), (kb, vb) = _answer_key(ra), _answer_key(rb)
        if ka != kb or (va is None) != (vb is None):
            return False
        if va is not None and abs(va - vb) > 1e-6 * max(1.0, abs(va)):
            return False
    return True


def mask_rows(rows: list[dict[str, Any]], min_n: int) -> list[dict[str, Any]]:
    """Masque `value` (et `n`) des lignes portant sur moins de `min_n` demandes."""
    out = []
    for row in rows:
        n = row.get("n")
        if isinstance(n, (int, float)) and n < min_n:
            row = {k: (None if k in ("value", "n") else v) for k, v in row.items()} | {"masked": True}
        out.append(row)
    return out


class KPISemanticAgent(BaseAgent):
    name = "kpi_semantic"
    output_model = KPIOutput

    def __init__(self, llm: LLMClient | None | Literal["auto"] = "auto") -> None:
        self._llm = llm

    @property
    def llm(self) -> LLMClient | None:
        if self._llm == "auto":
            self._llm = get_llm()
        return self._llm

    def run(self, state: PipelineState) -> KPIOutput:
        etl = state.output("quality_etl")
        catalog = load_catalog()
        min_n = get_settings().min_group_size
        sandbox = SQLSandbox.from_snapshot(etl["snapshot_path"], load_reference(state.dataset))
        queries: list[QueryRecord] = []

        def run_query(qid: str, purpose: str, sql: str, require_n: bool = False,
                      max_rows: int | None = None) -> list[dict[str, Any]]:
            rows = sandbox.query(sql, max_rows)
            if require_n and rows and "n" not in rows[0]:
                raise UnsafeQueryError("colonne n (COUNT(*)) manquante : le seuil anti-identification "
                                       "ne peut pas être appliqué")
            queries.append(QueryRecord(id=qid, purpose=purpose, sql=sql, rows=rows,
                                       snapshot_hash=etl["snapshot_hash"],
                                       executed_at=datetime.now(UTC).isoformat()))
            return rows

        try:
            kpis, skipped = [], []
            for k in catalog.kpis:
                value = run_query(f"kpi:{k.name}", k.definition, k.sql, max_rows=CATALOG_MAX_ROWS)[0]["value"]
                if value is None:  # ex. aucune demande clôturée sur la période
                    skipped.append(k.name)
                    continue
                kpis.append(KPIValue(name=k.name, value=float(value), unit=k.unit,
                                     definition=k.definition, query_id=f"kpi:{k.name}"))
            breakdowns = []
            for b in catalog.breakdowns:
                rows = mask_rows(run_query(f"breakdown:{b.name}", b.definition, b.sql,
                                           max_rows=CATALOG_MAX_ROWS), min_n)
                breakdowns.append(Breakdown(
                    name=b.name, unit=b.unit, dimension=b.dimension, definition=b.definition,
                    query_id=f"breakdown:{b.name}",
                    rows=[BreakdownRow(key=str(r[b.dimension]), value=r["value"], n=r["n"] or 0,
                                       masked=r.get("masked", False)) for r in rows],
                ))
            values = dimension_values(sandbox.query, min_n) if self.llm and state.questions else None
            answers = []
            for i, q in enumerate(state.questions, 1):
                t0 = time.perf_counter()
                answers.append(self.answer(i, q, catalog, sandbox, run_query, min_n, values))
                answers[-1].duration_s = round(time.perf_counter() - t0, 3)
        finally:
            sandbox.close()

        notes = [f"{len(kpis)} KPI, {len(breakdowns)} ventilations, {len(answers)} question(s)",
                 f"mode question -> SQL : {llm_label(self.llm) or 'baseline'}"]
        if skipped:
            notes.append(f"KPI non calculables sur cette période : {skipped}")
        if state.corrections.get(self.name):
            notes.append(f"relancé après signalement de l'Auditeur : {state.corrections[self.name]}")
        return KPIOutput(
            kpis=kpis,
            breakdowns=breakdowns,
            answers=answers,
            queries=queries,
            semantic_model={
                "table": "requests (instantané de processed.nyc311_clean)",
                "measures": {k.name: {"unit": k.unit, "definition": k.definition} for k in catalog.kpis},
                "dimensions": sorted({b.dimension for b in catalog.breakdowns}),
                "catalog": "src/biflow/semantic/kpi_catalog.yaml",
            },
            snapshot_hash=etl["snapshot_hash"],
            min_group_size=min_n,
            llm_model=llm_label(self.llm),
            input_rows=etl["rows_out"],
            notes=notes,
        )

    def answer(self, index: int, question: str, catalog, sandbox, run_query, min_n: int,
               values: dict[str, list[str]] | None = None) -> Answer:
        qid = f"question:{index}"
        sent, found = redact(question)
        scope = out_of_scope(question)
        if scope:
            return Answer(question=question, mode="refused", personal_data_found=found,
                          refusal=f"Question hors périmètre ({scope[0]}) : {scope[1]}.")
        if self.llm:
            error = None
            for _ in range(2):  # un essai + une correction avec le message d'erreur
                try:
                    sql = llm_to_sql(sent, catalog, self.llm, error, values)
                    rows = run_query(qid, question, sql, require_n=True)
                    check, base_sql = self.cross_check(qid, question, catalog, rows, run_query)
                    return Answer(question=question, question_sent=sent, personal_data_found=found,
                                  mode="llm", sql=sql, rows=mask_rows(rows, min_n), query_id=qid,
                                  cross_check=check, baseline_sql=base_sql)
                except Exception as exc:  # noqa: BLE001 — l'erreur est renvoyée au LLM puis rapportée
                    error = str(exc)[:500]
            log.warning("question %d : SQL du LLM refusé (%s), essai de la baseline", index, error)
        sql = baseline_to_sql(question, catalog)
        if sql is None:
            return Answer(question=question, mode="none", personal_data_found=found,
                          error="question non reconnue par la baseline (pas de LLM configuré)"
                          if not self.llm else "SQL du LLM refusé et question non reconnue")
        try:
            rows = run_query(qid, question, sql)
        except Exception as exc:  # noqa: BLE001
            return Answer(question=question, mode="baseline", sql=sql, error=str(exc)[:500],
                          personal_data_found=found)
        return Answer(question=question, mode="baseline", sql=sql, rows=mask_rows(rows, min_n),
                      query_id=qid, personal_data_found=found)

    @staticmethod
    def cross_check(qid: str, question: str, catalog, rows, run_query) -> tuple[str, str | None]:
        """Contrôle croisé : si la baseline reconnaît la question, ses règles relues donnent une
        seconde réponse ; une divergence signale une réponse à vérifier (l'une des deux est fausse)."""
        sql = baseline_to_sql(question, catalog)
        if sql is None:
            return "non vérifiable", None
        try:
            base_rows = run_query(f"{qid}:controle", f"contrôle croisé (baseline) : {question}", sql)
        except Exception:  # noqa: BLE001 — contrôle facultatif
            return "non vérifiable", sql
        return ("concordant" if same_answer(rows, base_rows) else "divergent"), sql

    def validate(self, output: KPIOutput, state: PipelineState) -> list[Check]:
        checks = [Check(name="kpis_not_empty", passed=bool(output.kpis), message=f"{len(output.kpis)} KPI")]
        for kpi in output.kpis:
            low, high = KPI_RANGES[kpi.unit]
            checks.append(check_in_range(f"kpi:{kpi.name}", kpi.value, low, high))
        exposed = [f"{b.name}={r.key}" for b in output.breakdowns for r in b.rows
                   if not r.masked and r.n < output.min_group_size]
        checks.append(Check(name="min_group_size", passed=not exposed,
                            message=f"agrégats sous le seuil non masqués : {exposed}" if exposed else "ok"))
        failed = [a.question for a in output.answers if a.error]
        checks.append(Check(name="questions_answered", passed=not failed, severity="warning",
                            message=f"sans réponse : {failed}" if failed else "ok"))
        return checks


def ask(question: str, snapshot_path: str, snapshot_hash: str, reference: dict,
        llm: LLMClient | None | Literal["auto"] = "auto") -> tuple[Answer, list[QueryRecord]]:
    """Répond à une question hors run (page « Poser une question » du dashboard), avec les
    mêmes garde-fous : périmètre, contrôle de la question, bac à sable, seuil anti-identification.
    L'instantané doit être celui du run publié (empreinte vérifiée)."""
    from biflow.common.snapshot import frame_hash, read_snapshot

    clean = read_snapshot(snapshot_path)
    if frame_hash(clean) != snapshot_hash:
        raise ValueError("l'instantané a été modifié depuis l'audit : question refusée")
    agent = KPISemanticAgent(llm=llm)
    catalog = load_catalog()
    min_n = get_settings().min_group_size
    sandbox = SQLSandbox({"requests": clean, **reference})
    queries: list[QueryRecord] = []

    def run_query(qid: str, purpose: str, sql: str, require_n: bool = False,
                  max_rows: int | None = None) -> list[dict[str, Any]]:
        rows = sandbox.query(sql, max_rows)
        if require_n and rows and "n" not in rows[0]:
            raise UnsafeQueryError("colonne n (COUNT(*)) manquante : le seuil anti-identification "
                                   "ne peut pas être appliqué")
        queries.append(QueryRecord(id=qid, purpose=purpose, sql=sql, rows=rows, snapshot_hash=snapshot_hash,
                                   executed_at=datetime.now(UTC).isoformat()))
        return rows

    try:
        t0 = time.perf_counter()
        values = dimension_values(sandbox.query, min_n) if agent.llm else None
        answer = agent.answer(1, question, catalog, sandbox, run_query, min_n, values)
        answer.duration_s = round(time.perf_counter() - t0, 3)
    finally:
        sandbox.close()
    return answer, queries
