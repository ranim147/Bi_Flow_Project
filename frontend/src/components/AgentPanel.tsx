// Détail d'un agent pour un run : ce qu'il a fait, ce qu'il a produit, ses contrôles.
// Pendant un run en direct, seules les infos de la trace sont disponibles ; le détail
// complet (run.json) arrive à la fin du run.
import { useEffect, useMemo, useState } from "react";
import { api, type Check, type QueryRecord, type Run, type Step } from "../api";
import { STEP_LABEL, STEP_ROLE, checkParts, day, kpiValue, num, plural, short } from "../format";
import { STEPS, type Audit, type Station } from "../timeline";
import { Bars } from "./charts";
import { Checks, Notice, Provenance, Sql, Table } from "./ui";

const ISSUE_LABEL: Record<string, string> = {
  duplicate: "doublon",
  missing: "manquant",
  invalid_type: "mauvais type",
  invalid_format: "format invalide",
  invalid_value: "valeur invalide",
  out_of_range: "hors plage",
  inconsistent: "incohérence",
};

const CHART_RULE: Record<string, string> = {
  kpi_card: "KPI global : carte",
  line: "dimension temporelle : courbe avec anomalies",
  bar: "6 modalités ou moins : colonnes",
  bar_h: "plus de 6 modalités : barres horizontales, top 15",
  table: "réponses aux questions : table",
  text: "insights : texte",
};

const MODE_LABEL: Record<string, string> = {
  llm: "traduite par le LLM",
  baseline: "traduite par les règles (sans LLM)",
  refused: "refusée : hors périmètre",
  none: "non reconnue",
};

export default function AgentPanel({ step, station, run, maxAttempts, audits = [] }: {
  step: Step; station: Station; run: Run | null; maxAttempts: number; audits?: Audit[];
}) {
  const out = run?.outputs[step];
  const index = STEPS.indexOf(step);
  const runChecks = run?.validations[step]?.checks;
  const correction = run?.corrections?.[step] ?? station.correction;

  return (
    <section className="agent-panel" aria-labelledby={`agent-${step}`}>
      <header className="agent-head">
        <span className="agent-index">{index + 1}</span>
        <div>
          <h2 id={`agent-${step}`}>{STEP_LABEL[step]}</h2>
          <p className="agent-role">{STEP_ROLE[step]}</p>
        </div>
        <AgentFacts station={station} maxAttempts={maxAttempts} />
      </header>

      {correction && (
        <Notice tone="warn">
          Relancé à la demande de l'Auditeur : {correction}
        </Notice>
      )}
      {station.state === "failed" && station.message && (
        <Notice tone="critical">Arrêt après {station.attempt} essais : {station.message}</Notice>
      )}
      {station.state === "idle" && <p className="muted">Cet agent n'a pas encore tourné dans ce run.</p>}
      {(station.state === "running" || station.state === "retrying") && (
        <p className="muted">
          Essai {station.attempt} sur {maxAttempts} en cours…
          {station.state === "retrying" && station.message ? ` L'essai précédent a échoué : ${station.message}` : ""}
        </p>
      )}

      {out ? (
        <div className="agent-body">
          {step === "data_profiler" && <Profiler out={out} />}
          {step === "quality_etl" && <Quality out={out} runId={run!.run_id} />}
          {step === "kpi_semantic" && <Kpi out={out} />}
          {step === "analyste_bi" && <Analyste out={out} />}
          {step === "dashboard_generator" && <Dashboard out={out} published={run!.published} />}
          {step === "auditor_xai" && <Auditor out={out} audits={audits} />}
        </div>
      ) : station.state === "done" ? (
        <p className="muted">{station.message} Le détail complet s'affiche à la fin du run.</p>
      ) : null}

      {(runChecks || station.checks.length > 0) && (
        <div className="block">
          <h3>Contrôles de validation de l'orchestrateur</h3>
          <p className="block-intro">
            Vérifiés après chaque essai. Un contrôle bloquant en échec déclenche un nouvel essai.
          </p>
          <Checks checks={runChecks ?? station.checks} compact />
        </div>
      )}
    </section>
  );
}

function AgentFacts({ station, maxAttempts }: { station: Station; maxAttempts: number }) {
  const facts: [string, string][] = [];
  if (station.attempt) facts.push(["Essais", `${station.attempt} sur ${maxAttempts}`]);
  if (station.passes > 1) facts.push(["Passages", String(station.passes)]);
  if (station.inputRows != null) facts.push(["Lignes en entrée", num(station.inputRows)]);
  if (station.outputRows != null) facts.push(["Lignes en sortie", num(station.outputRows)]);
  if (station.durationS != null) facts.push(["Durée", `${num(station.durationS, 2)} s`]);
  if (!facts.length) return null;
  return (
    <dl className="facts">
      {facts.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ 1. Data Profiler */
function Profiler({ out }: { out: any }) {
  const boroughs = Object.entries(out.borough_counts as Record<string, number>).map(([key, value]) => ({ key, value }));
  const nulls = Object.entries(out.null_rates as Record<string, number>).filter(([, v]) => v > 0);
  return (
    <>
      <p className="lead">
        {num(out.row_count)} demandes brutes, créées du {day(out.date_min.slice(0, 10))} au{" "}
        {day(out.date_max.slice(0, 10))}, sur {out.columns.length} colonnes.{" "}
        {out.issues.length ? `${plural(out.issues.length, "type de défaut détecté", "types de défaut détectés")}, transmis à Qualité / ETL.` : "Aucun défaut détecté."}
      </p>
      {out.source?.notes?.length > 0 && (
        <Notice tone="warn">
          {out.source.notes.map((n: string) => <p key={n}>{n}</p>)}
        </Notice>
      )}
      <div className="block">
        <h3>Défauts détectés</h3>
        <Table
          rows={out.issues}
          empty="Aucun défaut."
          columns={[
            { key: "column", label: "Colonne", render: (r) => <code>{r.column}</code> },
            { key: "issue_type", label: "Type", render: (r) => ISSUE_LABEL[r.issue_type] ?? r.issue_type },
            { key: "count", label: "Lignes", align: "right", render: (r) => num(r.count) },
            { key: "description", label: "Constat" },
            { key: "sample_keys", label: "Exemples (unique_key)", render: (r) => r.sample_keys.slice(0, 3).join(", ") },
          ]}
        />
      </div>
      <div className="two-col">
        <div className="block">
          <h3>Demandes par borough (brut)</h3>
          <Bars data={boroughs} unit="count" horizontal />
        </div>
        <div className="block">
          <h3>Taux de valeurs nulles</h3>
          <Table
            rows={nulls.map(([c, v]) => ({ c, v }))}
            empty="Aucune valeur nulle."
            columns={[
              { key: "c", label: "Colonne", render: (r) => <code>{r.c}</code> },
              { key: "v", label: "Nulles", align: "right", render: (r) => `${num(100 * r.v, 1)} %` },
            ]}
          />
          {out.absent_columns?.length > 0 && (
            <p className="muted small">Colonnes absentes de l'extrait, donc vides à 100 % : {out.absent_columns.join(", ")}.</p>
          )}
        </div>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ 2. Qualité / ETL */
function Quality({ out, runId }: { out: any; runId: string }) {
  const [q, setQ] = useState<{ total: number; rows: Record<string, unknown>[] } | null>(null);
  useEffect(() => {
    api.quarantine(runId, 8).then(setQ).catch(() => setQ(null));
  }, [runId]);
  const rules = Object.entries(out.rule_counts as Record<string, number>).map(([rule, n]) => ({ rule, n }));
  return (
    <>
      <div className="flow" aria-label="Bilan des lignes">
        <div><span className="flow-n">{num(out.rows_in)}</span><span>lignes lues</span></div>
        <div className="flow-out"><span className="flow-n">{num(out.rows_dropped)}</span><span>écartées, gardées en quarantaine</span></div>
        <div><span className="flow-n">{num(out.rows_out)}</span><span>conservées dans l'instantané</span></div>
      </div>
      <p className="lead">
        Instantané figé <code>{short(out.snapshot_hash)}</code> : tous les agents suivants lisent ce fichier, et
        l'Auditeur recalcule son empreinte. Texte libre retiré, coordonnées arrondies à environ 1 km.
      </p>
      <div className="block">
        <h3>Règles appliquées ({rules.length})</h3>
        <Table
          rows={rules}
          columns={[
            { key: "rule", label: "Règle", render: (r) => <span className={r.rule.startsWith("suppression") ? "rule-drop" : ""}>{r.rule}</span> },
            { key: "n", label: "Lignes concernées", align: "right", render: (r) => num(r.n) },
          ]}
        />
      </div>
      {q && q.total > 0 && (
        <div className="block">
          <h3>Quarantaine : {num(q.total)} lignes, chacune avec sa règle</h3>
          <Table
            rows={q.rows}
            columns={["unique_key", "created_date", "closed_date", "complaint_type", "borough", "motif"].map((k) => ({
              key: k, label: k === "motif" ? "Motif" : k,
              render: (r: any) => (k.endsWith("_date") ? String(r[k] ?? "—").slice(0, 16) : String(r[k] ?? "—")),
            }))}
          />
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ 3. KPI & Semantic */
function Kpi({ out }: { out: any }) {
  const queries: Record<string, QueryRecord> = useMemo(
    () => Object.fromEntries(out.queries.map((q: QueryRecord) => [q.id, q])),
    [out],
  );
  const masked = out.breakdowns.reduce((s: number, b: any) => s + b.rows.filter((r: any) => r.masked).length, 0);
  return (
    <>
      <p className="lead">
        {out.kpis.length} KPI et {out.breakdowns.length} ventilations calculés en SQL (DuckDB, lecture seule) à partir
        du catalogue <code>kpi_catalog.yaml</code>. {out.queries.length} requêtes enregistrées avec leur résultat.{" "}
        {masked ? `${plural(masked, "agrégat masqué", "agrégats masqués")} : moins de ${out.min_group_size} demandes.` : `Aucun agrégat sous le seuil de ${out.min_group_size} demandes.`}{" "}
        Questions : {out.llm_model ? `LLM ${out.llm_model}` : "règles de la baseline (aucun LLM configuré)"}.
      </p>
      <div className="kpi-list">
        {out.kpis.map((k: any) => (
          <div key={k.name} className="kpi-line">
            <div className="kpi-line-main">
              <span className="kpi-line-value">{kpiValue(k.value, k.unit)}</span>
              <span className="kpi-line-def">{k.definition}</span>
            </div>
            <Provenance ids={[k.query_id]} queries={queries} label="Voir le SQL" />
          </div>
        ))}
      </div>
      {out.answers.length > 0 && (
        <div className="block">
          <h3>Questions métier</h3>
          {out.answers.map((a: any, i: number) => <AnswerView key={i} a={a} />)}
        </div>
      )}
    </>
  );
}

export function AnswerView({ a }: { a: any }) {
  const tone = a.mode === "refused" ? "refused" : a.error ? "error" : "ok";
  return (
    <article className={`answer answer-${tone}`}>
      <p className="answer-q">{a.question}</p>
      <p className="answer-mode">
        {MODE_LABEL[a.mode]}
        {a.duration_s ? `, ${num(a.duration_s, 3)} s` : ""}
        {a.cross_check && `, contrôle croisé : ${a.cross_check}`}
      </p>
      {a.personal_data_found?.length > 0 && (
        <Notice tone="warn">
          Données personnelles masquées avant tout envoi ({a.personal_data_found.join(", ")})
          {a.question_sent ? ` : « ${a.question_sent} »` : ""}.
        </Notice>
      )}
      {a.refusal ? (
        <p className="answer-refusal">{a.refusal}</p>
      ) : a.error ? (
        <p className="answer-error">{a.error}</p>
      ) : (
        <>
          <ResultRows rows={a.rows} />
          {a.sql && <Sql sql={a.sql} />}
        </>
      )}
      {a.cross_check === "divergent" && (
        <Notice tone="warn">La baseline trouve un autre résultat : réponse à vérifier.</Notice>
      )}
    </article>
  );
}

export function ResultRows({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows?.length) return <p className="muted">Aucune ligne.</p>;
  if (rows.length === 1) {
    const r = rows[0];
    const label = Object.entries(r).find(([k]) => !["value", "n", "masked"].includes(k));
    const v = r.value as number | null;
    return (
      <p className="answer-value">
        {label && <span className="answer-key">{String(label[1])}</span>}
        <span className="answer-number">{v === null ? "masqué" : typeof v === "number" ? num(v, Number.isInteger(v) ? 0 : 2) : String(v)}</span>
        {typeof r.n === "number" && r.n !== v && <span className="answer-n">sur {num(r.n as number)} demandes</span>}
      </p>
    );
  }
  const cols = Object.keys(rows[0]);
  return (
    <Table rows={rows.slice(0, 20)}
           columns={cols.map((c) => ({ key: c, label: c, align: typeof rows[0][c] === "number" ? "right" : undefined,
             render: (r: any) => (typeof r[c] === "number" ? num(r[c], Number.isInteger(r[c]) ? 0 : 2) : String(r[c] ?? "masqué")) }))} />
  );
}

/* ------------------------------------------------------------------ 4. Analyste BI */
function Analyste({ out }: { out: any }) {
  const llmCount = out.insights.filter((i: any) => i.written_by === "llm").length;
  return (
    <>
      <p className="lead">
        {out.insights.length} insights, chacun avec ses preuves chiffrées. Anomalies : décomposition STL + z-score
        robuste, ville entière et chaque borough. Rédaction :{" "}
        {out.llm_model ? `${llmCount} textes réécrits par ${out.llm_model}, les autres gardent le gabarit` : "gabarits (aucun LLM configuré)"}.
        Un texte du LLM qui cite un nombre absent des preuves est rejeté.
      </p>
      <ol className="insights">
        {out.insights.map((i: any) => <InsightItem key={i.title} i={i} />)}
      </ol>
      {out.anomalies?.length > 0 && (
        <div className="block">
          <h3>Anomalies détectées ({out.anomalies.length})</h3>
          <Table
            rows={out.anomalies.slice(0, 12)}
            columns={[
              { key: "day", label: "Jour", render: (r) => day(r.day) },
              { key: "zone", label: "Zone", render: (r) => (r.zone === "ALL" ? "toute la ville" : r.zone) },
              { key: "value", label: "Observé", align: "right", render: (r) => num(r.value) },
              { key: "expected", label: "Attendu", align: "right", render: (r) => num(r.expected) },
              { key: "z_score", label: "Écart z", align: "right", render: (r) => num(r.z_score, 1) },
            ]}
          />
        </div>
      )}
    </>
  );
}

export function InsightItem({ i }: { i: any }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={`insight cat-${i.category}`}>
      <p className="insight-title">{i.title}</p>
      <p className="insight-text">{i.text}</p>
      <div className="insight-foot">
        <span>{i.written_by === "llm" ? "Rédigé par le LLM, nombres vérifiés" : "Rédigé par gabarit"}</span>
        <button className="link-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Masquer les preuves" : "Voir les preuves"}
        </button>
      </div>
      {open && (
        <dl className="evidence">
          {Object.entries(i.evidence).map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd>{typeof v === "number" ? num(v, Number.isInteger(v) ? 0 : 2) : String(v)}</dd></div>
          ))}
        </dl>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ 5. Dashboard Generator */
function Dashboard({ out, published }: { out: any; published: boolean }) {
  return (
    <>
      <p className="lead">
        {out.charts.length} éléments décidés par règles, écrits dans <code>dashboard_spec.json</code> (empreinte{" "}
        <code>{short(out.spec_hash)}</code>). Chaque graphique chiffré garde la requête qui l'a produit. L'agent ne
        publie rien : la publication dépend de l'audit.
      </p>
      {published ? (
        <Notice tone="good">Run publié : <a href="#/dashboard">ouvrir le tableau de bord</a>.</Notice>
      ) : (
        <Notice tone="warn">Run non publié : le tableau de bord garde le dernier run validé.</Notice>
      )}
      <div className="block">
        <h3>Spécification du tableau de bord</h3>
        <Table
          rows={out.charts}
          columns={[
            { key: "title", label: "Élément" },
            { key: "chart_type", label: "Règle appliquée", render: (r) => CHART_RULE[r.chart_type] ?? r.chart_type },
            { key: "query_ids", label: "Requêtes d'origine", render: (r) => (r.query_ids.length ? r.query_ids.join(", ") : "—") },
          ]}
        />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ 6. Auditeur / XAI */
function Auditor({ out, audits }: { out: any; audits: Audit[] }) {
  const checks: (Check & { target_step: Step })[] = out.checks;
  const passed = checks.filter((c) => c.passed).length;
  const byStep = STEPS.slice(0, -1)
    .map((s) => ({ s, list: checks.filter((c) => c.target_step === s) }))
    .filter((g) => g.list.length);
  const errors = out.findings.filter((f: any) => f.severity === "error");
  const warnings = out.findings.filter((f: any) => f.severity === "warning");
  return (
    <>
      <div className={`verdict ${out.approved ? "v-ok" : "v-ko"}`}>
        <span className="verdict-n">{passed}/{checks.length}</span>
        <div>
          <p className="verdict-title">{out.approved ? "Run approuvé" : "Run non approuvé"}</p>
          <p>{out.approved ? "Chaque chiffre affiché correspond à une requête enregistrée et rejouable." : `${plural(errors.length, "écart bloquant, adressé", "écarts bloquants, adressés")} à l'étape fautive.`}</p>
        </div>
      </div>
      {audits.length > 1 && (
        <div className="block">
          <h3>Passages de l'Auditeur</h3>
          <ol className="audit-rounds">
            {audits.map((a, i) => {
              const blocking = a.findings.filter((f) => f.severity === "error");
              return (
                <li key={i} className={a.approved ? "ok" : "ko"}>
                  <span className="audit-round-n">Audit {i + 1}</span>
                  <span>
                    {a.approved
                      ? `${a.passed}/${a.total} contrôles, aucun écart bloquant : publication autorisée.`
                      : `Écart bloquant : ${blocking.map((f) => `${STEP_LABEL[f.target_step]}, ${f.message}`).join(" ; ")}. `
                        + (i < audits.length - 1 ? "Le run repart de cette étape." : "Toujours présent après correction : run non fiable.")}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      {errors.length > 0 && (
        <Notice tone="critical">
          {errors.map((f: any, i: number) => <p key={i}><strong>{STEP_LABEL[f.target_step as Step]}</strong> : {f.message}</p>)}
        </Notice>
      )}
      {warnings.length > 0 && (
        <Notice tone="warn">
          {warnings.map((f: any, i: number) => <p key={i}>{STEP_LABEL[f.target_step as Step]} : {f.message}</p>)}
        </Notice>
      )}
      <div className="block">
        <h3>Explication</h3>
        <div className="explanation">
          {out.explanation.split("\n").map((l: string, i: number) => <p key={i}>{l}</p>)}
        </div>
      </div>
      <div className="block">
        <h3>Contrôles par étape vérifiée</h3>
        <div className="audit-groups">
          {byStep.map(({ s, list }) => <AuditGroup key={s} step={s} list={list} />)}
        </div>
      </div>
    </>
  );
}

function AuditGroup({ step, list }: { step: Step; list: Check[] }) {
  const [open, setOpen] = useState(false);
  const ok = list.filter((c) => c.passed).length;
  return (
    <div className={`audit-group ${ok === list.length ? "" : "has-ko"}`}>
      <button className="audit-group-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>{STEP_LABEL[step]}</span>
        <span className="audit-count">{ok}/{list.length}</span>
      </button>
      {open && <Checks checks={list} compact />}
      {!open && (
        <p className="audit-sample">
          {[...new Set(list.map((c) => checkParts(c.name)[0]))].slice(0, 4).join(", ")}
        </p>
      )}
    </div>
  );
}

