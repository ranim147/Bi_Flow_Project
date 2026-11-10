import { useState, type ReactNode } from "react";
import type { Check, QueryRecord, RunStatus } from "../api";
import { STATUS_LABEL, checkParts, dateTime, short } from "../format";

export function StatusPill({ status, published }: { status: RunStatus; published?: boolean }) {
  return (
    <span className={`pill pill-${status}`}>
      <StatusIcon status={status} />
      {status === "success" && published === false ? "Réussi" : STATUS_LABEL[status]}
    </span>
  );
}

function StatusIcon({ status }: { status: RunStatus }) {
  const common = { width: 12, height: 12, viewBox: "0 0 12 12", "aria-hidden": true } as const;
  if (status === "success")
    return <svg {...common}><path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="currentColor" strokeWidth="1.8" /></svg>;
  if (status === "failed")
    return <svg {...common}><path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.8" /></svg>;
  if (status === "unreliable")
    return <svg {...common}><path d="M6 2v5M6 9.2v.6" stroke="currentColor" strokeWidth="1.8" /></svg>;
  return <svg {...common}><circle cx="6" cy="6" r="3" fill="currentColor" /></svg>;
}

export function Sql({ sql }: { sql: string }) {
  return <pre className="sql"><code>{sql.trim()}</code></pre>;
}

/** « Origine des chiffres » : la requête enregistrée, l'instantané et l'heure d'exécution. */
export function Provenance({ ids, queries, label = "Origine des chiffres" }: {
  ids: string[]; queries: Record<string, QueryRecord>; label?: string;
}) {
  const [open, setOpen] = useState(false);
  const found = ids.map((id) => queries[id]).filter(Boolean);
  if (!found.length) return null;
  return (
    <div className="provenance">
      <button className="link-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Masquer l'origine" : label}
      </button>
      {open &&
        found.map((q) => (
          <div key={q.id} className="prov-item">
            <p className="prov-meta">
              Requête <code>{q.id}</code> exécutée le {dateTime(q.executed_at)} sur l'instantané{" "}
              <code>{short(q.snapshot_hash)}</code>
            </p>
            <Sql sql={q.sql} />
          </div>
        ))}
    </div>
  );
}

export function Checks({ checks, compact = false }: { checks: Check[]; compact?: boolean }) {
  if (!checks?.length) return <p className="muted">Aucun contrôle enregistré.</p>;
  return (
    <ul className={`checks ${compact ? "compact" : ""}`}>
      {checks.map((c, i) => {
        const [head, target] = checkParts(c.name);
        const warn = !c.passed && c.severity === "warning";
        return (
          <li key={i} className={c.passed ? "ok" : warn ? "warn" : "ko"}>
            <span className="check-mark" aria-label={c.passed ? "réussi" : warn ? "avertissement" : "échec"}>
              {c.passed ? "✓" : warn ? "!" : "✕"}
            </span>
            <span className="check-name">
              {head}
              {target && <span className="check-target"> {target}</span>}
            </span>
            {c.message && c.message !== "ok" && <span className="check-msg">{c.message}</span>}
          </li>
        );
      })}
    </ul>
  );
}

export function Table({ columns, rows, empty = "Aucune ligne." }: {
  columns: { key: string; label: string; align?: "right"; render?: (row: any) => ReactNode }[];
  rows: any[];
  empty?: string;
}) {
  if (!rows.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c.key} className={c.align}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.key} className={c.align}>{c.render ? c.render(r) : String(r[c.key] ?? "—")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "critical" | "good"; children: ReactNode }) {
  return <div className={`notice notice-${tone}`} role={tone === "critical" ? "alert" : undefined}>{children}</div>;
}

export function Loading({ what }: { what: string }) {
  return <p className="muted loading">Chargement {what}…</p>;
}
