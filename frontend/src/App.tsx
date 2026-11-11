import { useCallback, useEffect, useState } from "react";
import { api, type Meta, type Published } from "./api";
import { Loading, Notice } from "./components/ui";
import Ask from "./pages/Ask";
import Audit from "./pages/Audit";
import Dashboard from "./pages/Dashboard";
import Demo from "./pages/Demo";
import Runs from "./pages/Runs";
import { useRoute } from "./router";

const NAV = [
  { id: "demo", label: "Scénarios" },
  { id: "dashboard", label: "Tableau de bord" },
  { id: "ask", label: "Poser une question" },
  { id: "audit", label: "Audit et provenance" },
  { id: "runs", label: "Historique" },
];

export default function App() {
  const [page, arg] = useRoute();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [published, setPublished] = useState<Published | null>(null);
  const [pubError, setPubError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setMetaError(e.message));
  }, []);
  useEffect(() => {
    api.published().then((p) => { setPublished(p); setPubError(null); })
      .catch((e) => { setPublished(null); setPubError(e.message); });
  }, [refresh]);
  const onRunFinished = useCallback(() => setRefresh((r) => r + 1), []);

  useEffect(() => { window.scrollTo({ top: 0 }); }, [page]);

  const needsPublished = page === "dashboard" || page === "audit";

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/demo" aria-label="BIFlow, accueil">
          <svg width="34" height="14" viewBox="0 0 34 14" aria-hidden="true">
            <line x1="4" y1="7" x2="30" y2="7" stroke="currentColor" strokeWidth="3" />
            <circle cx="5" cy="7" r="4.5" fill="currentColor" />
            <circle cx="17" cy="7" r="4.5" fill="var(--surface)" stroke="currentColor" strokeWidth="2.5" />
            <circle cx="29" cy="7" r="4.5" fill="currentColor" />
          </svg>
          <span className="brand-name">BIFlow</span>
          <span className="brand-sub">Pipeline BI multi-agents, demandes NYC 311</span>
        </a>
        <nav className="nav" aria-label="Navigation principale">
          {NAV.map((n) => (
            <a key={n.id} href={`#/${n.id}`} aria-current={page === n.id ? "page" : undefined}>{n.label}</a>
          ))}
        </nav>
        {meta && (
          <p className="llm-status" title="Configuré dans .env (BIFLOW_LLM_PROVIDER)">
            {meta.llm ? `LLM ${meta.llm}` : "Sans LLM, baseline à règles"}
          </p>
        )}
      </header>

      <main className="content">
        {metaError && (
          <Notice tone="critical">
            {metaError}
          </Notice>
        )}
        {!meta && !metaError && <Loading what="de l'application" />}
        {meta && page === "demo" && <Demo meta={meta} runId={arg ?? null} onRunFinished={onRunFinished} />}
        {meta && page === "ask" && <Ask meta={meta} />}
        {page === "runs" && <Runs refreshKey={refresh} />}
        {needsPublished && !published && pubError && (
          <div className="empty-state">
            <h1>Aucun tableau de bord publié</h1>
            <p>{pubError}</p>
            <a className="btn-primary" href="#/demo">Aller aux scénarios</a>
          </div>
        )}
        {needsPublished && !published && !pubError && <Loading what="du run publié" />}
        {page === "dashboard" && published && <Dashboard data={published} />}
        {page === "audit" && published && <Audit data={published} />}
      </main>
    </div>
  );
}
