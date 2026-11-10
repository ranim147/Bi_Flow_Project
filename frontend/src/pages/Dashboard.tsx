import { useMemo } from "react";
import type { ChartSpec, Published, QueryRecord } from "../api";
import { InsightItem } from "../components/AgentPanel";
import { Bars, DailyLine } from "../components/charts";
import { Notice, Provenance } from "../components/ui";
import { day, dateTime, kpiValue, num, plural, short } from "../format";

// Libellés courts des cartes KPI (le titre long reste la définition du catalogue)
const KPI_SHORT: Record<string, string> = {
  total_requests: "Demandes",
  closure_rate: "Clôturées",
  median_resolution_hours: "Délai médian de résolution",
  sla_compliance_rate: "Sous le délai cible",
  requests_per_10k: "Pour 10 000 habitants",
};

export default function Dashboard({ data }: { data: Published }) {
  const { spec, run, pointer } = data;
  const queries: Record<string, QueryRecord> = useMemo(
    () => Object.fromEntries((run.outputs.kpi_semantic.queries as QueryRecord[]).map((q) => [q.id, q])),
    [run],
  );
  const charts = spec.charts;
  const cards = charts.filter((c) => c.chart_type === "kpi_card");
  const daily = charts.find((c) => c.chart_type === "line");
  const insights = charts.find((c) => c.chart_type === "text");
  const fullInsights = run.outputs.analyste_bi.insights as any[];
  const anomalies = fullInsights.filter((i) => i.category === "anomalie");
  const findings = fullInsights.filter((i) => i.category !== "anomalie");
  const bars = charts.filter((c) => c.chart_type === "bar" || c.chart_type === "bar_h");
  const sample = run.outputs.data_profiler.source?.notes?.length > 0;
  const profile = run.outputs.data_profiler;
  const flagged = daily?.data.filter((d) => d.anomaly).length ?? 0;

  return (
    <div className="dashboard">
      <header className="page-head">
        <div>
          <h1>Demandes NYC 311</h1>
          <p className="muted">
            Du {day(profile.date_min.slice(0, 10))} au {day(profile.date_max.slice(0, 10))}. Run{" "}
            <code>{spec.run_id}</code>, publié le {dateTime(pointer.published_at)} après{" "}
            {run.outputs.auditor_xai.checks.length} contrôles d'audit réussis.
          </p>
        </div>
        <p className="seal" title={`Empreinte de la spec : ${pointer.spec_hash}`}>
          Spec vérifiée <code>{short(pointer.spec_hash, 8)}</code>
        </p>
      </header>

      {sample && (
        <Notice tone="warn">
          Échantillon d'environ 2 % des demandes, non probabiliste : comparez les boroughs et les périodes entre eux,
          pas les volumes à la ville. Détail dans <a href="#/audit">Audit et provenance</a>.
        </Notice>
      )}

      <section className="kpis" aria-label="Indicateurs">
        {cards.map((c) => (
          <div key={c.id} className="kpi">
            <p className="kpi-label">{KPI_SHORT[c.id] ?? c.title}</p>
            <p className="kpi-value">{kpiValue(c.value, c.unit)}</p>
            <p className="kpi-def">{c.title}</p>
            <Provenance ids={c.query_ids} queries={queries} label="Voir le SQL" />
          </div>
        ))}
      </section>

      {daily && (
        <section className="panel">
          <div className="panel-head">
            <h2>Demandes par jour</h2>
            <p className="muted">{num(daily.data.length)} jours, {plural(flagged, "jour anormal", "jours anormaux")} pour toute la ville</p>
          </div>
          <DailyLine data={daily.data} />
          <Provenance ids={daily.query_ids} queries={queries} />
        </section>
      )}

      <div className="dash-split">
        <section className="panel">
          <div className="panel-head"><h2>Ce que montrent les données</h2></div>
          <ol className="insights">
            {findings.map((i) => <InsightItem key={i.title} i={i} />)}
          </ol>
        </section>
        <section className="panel">
          <div className="panel-head">
            <h2>Pics inhabituels</h2>
            <p className="muted">Le type de plainte en hausse est une piste à vérifier, pas une cause.</p>
          </div>
          <ol className="insights">
            {anomalies.map((i) => <InsightItem key={i.title} i={i} />)}
          </ol>
        </section>
      </div>

      <div className="chart-grid">
        {bars.map((c) => <BarPanel key={c.id} c={c} queries={queries} minN={run.outputs.kpi_semantic.min_group_size} />)}
      </div>

      {insights && insights.data.some((d: any) => d.written_by === "llm") && (
        <p className="muted small">Certains textes ont été reformulés par le LLM ; leurs nombres ont été vérifiés contre les preuves.</p>
      )}
    </div>
  );
}

function BarPanel({ c, queries, minN }: { c: ChartSpec; queries: Record<string, QueryRecord>; minN: number }) {
  const masked = c.data.filter((d: any) => d.masked).length;
  const horizontal = c.chart_type === "bar_h" || c.data.some((d: any) => d.key.length > 16);
  return (
    <section className={`panel ${c.chart_type === "bar_h" ? "wide" : ""}`}>
      <div className="panel-head">
        <h2>{c.title}</h2>
        {masked > 0 && <p className="muted">{plural(masked, "valeur masquée", "valeurs masquées")} : moins de {minN} demandes.</p>}
      </div>
      <Bars data={c.data} unit={c.unit} horizontal={horizontal} />
      <Provenance ids={c.query_ids} queries={queries} />
    </section>
  );
}
