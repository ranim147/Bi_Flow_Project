import { useEffect, useState } from "react";
import { api, type RunSummary } from "../api";
import { Loading, Notice, StatusPill } from "../components/ui";
import { dateTime } from "../format";

export default function Runs({ refreshKey }: { refreshKey: number }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.runs().then(setRuns).catch((e) => setError(e.message));
  }, [refreshKey]);

  return (
    <div className="runs">
      <header className="page-head">
        <div>
          <h1>Historique des runs</h1>
          <p className="muted">
            Chaque run garde son journal complet (run.json). Seul le dernier run publié alimente le tableau de bord.
          </p>
        </div>
      </header>
      {error && <Notice tone="critical">{error}</Notice>}
      {!runs && !error && <Loading what="des runs" />}
      {runs && runs.length === 0 && <p className="muted empty">Aucun run pour l'instant. Lancez un scénario.</p>}
      {runs && runs.length > 0 && (
        <div className="table-wrap">
          <table className="runs-table">
            <thead>
              <tr>
                <th>Run</th><th>Statut</th><th>Lancé</th><th className="right">Étapes réussies</th>
                <th className="right">Nouveaux essais</th><th className="right">Renvois</th><th className="right">Audit</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td><a href={`#/demo/${r.run_id}`}><code>{r.run_id}</code></a>{r.is_current && <span className="tag">affiché</span>}</td>
                  <td><StatusPill status={r.status} published={r.published} /></td>
                  <td>{dateTime(r.started_at)}</td>
                  <td className="right">{r.steps_done}/6</td>
                  <td className="right">{r.retries}</td>
                  <td className="right">{r.feedback_rounds}</td>
                  <td className="right">{r.audit ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
