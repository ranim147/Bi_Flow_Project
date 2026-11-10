import type { Published } from "../api";
import { Checks, Notice, Table } from "../components/ui";
import { STEP_LABEL, basename, day, num, short } from "../format";
import { STEPS } from "../timeline";

export default function Audit({ data }: { data: Published }) {
  const { run } = data;
  const profile = run.outputs.data_profiler;
  const etl = run.outputs.quality_etl;
  const audit = run.outputs.auditor_xai;
  const source = profile.source || {};
  const passed = audit.checks.filter((c: any) => c.passed).length;

  return (
    <div className="audit-page">
      <header className="page-head">
        <div>
          <h1>Audit et provenance</h1>
          <p className="muted">D'où viennent les chiffres du tableau de bord, et comment ils ont été vérifiés.</p>
        </div>
      </header>

      <section className="panel">
        <div className="panel-head"><h2>Les données</h2></div>
        <dl className="facts wide">
          <div><dt>Extrait</dt><dd>{run.dataset.file_path ? basename(run.dataset.file_path) : run.dataset.table}</dd></div>
          <div><dt>Demandes lues</dt><dd>{num(profile.row_count)}</dd></div>
          <div><dt>Période</dt><dd>{day(profile.date_min.slice(0, 10))} au {day(profile.date_max.slice(0, 10))}</dd></div>
          <div><dt>Instantané audité</dt><dd><code>{short(etl.snapshot_hash)}</code></dd></div>
        </dl>
        {source.source && <p>{source.source}</p>}
        {source.notes?.length > 0 && (
          <Notice tone="warn">{source.notes.map((n: string) => <p key={n}>{n}</p>)}</Notice>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Explication de l'Auditeur</h2>
          <p className="muted">{passed}/{audit.checks.length} contrôles réussis</p>
        </div>
        <div className="explanation">
          {audit.explanation.split("\n").map((l: string, i: number) => <p key={i}>{l}</p>)}
        </div>
      </section>

      <div className="dash-split">
        <section className="panel">
          <div className="panel-head"><h2>Défauts trouvés par le Data Profiler</h2></div>
          <Table rows={profile.issues} columns={[
            { key: "column", label: "Colonne", render: (r) => <code>{r.column}</code> },
            { key: "description", label: "Constat" },
          ]} />
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Règles de Qualité / ETL</h2></div>
          <Table rows={Object.entries(etl.rule_counts).filter(([, n]) => (n as number) > 0).map(([rule, n]) => ({ rule, n }))}
                 columns={[
                   { key: "rule", label: "Règle" },
                   { key: "n", label: "Lignes", align: "right", render: (r) => num(r.n) },
                 ]} />
          <p className="muted small">{num(etl.quarantine_rows)} ligne(s) écartée(s), gardée(s) en quarantaine avec leur motif.</p>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head"><h2>Tous les contrôles d'audit</h2></div>
        <div className="audit-columns">
          {STEPS.slice(0, -1).map((s) => {
            const list = audit.checks.filter((c: any) => c.target_step === s);
            if (!list.length) return null;
            return (
              <div key={s}>
                <h3>{STEP_LABEL[s]}</h3>
                <Checks checks={list} compact />
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
