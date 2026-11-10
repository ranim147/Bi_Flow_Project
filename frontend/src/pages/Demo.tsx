import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type LiveEvent, type LiveRun, type Meta, type Run, type Step } from "../api";
import AgentPanel from "../components/AgentPanel";
import LineDiagram from "../components/LineDiagram";
import { Notice, StatusPill } from "../components/ui";
import { STATUS_LABEL, STEP_LABEL, basename, num, time } from "../format";
import { navigate } from "../router";
import { STEPS, buildTimeline, eventsFromRun } from "../timeline";

const POLL_MS = 350;

export default function Demo({ meta, runId, onRunFinished }: {
  meta: Meta; runId: string | null; onRunFinished: () => void;
}) {
  const [scenario, setScenario] = useState("nominal");
  const [step, setStep] = useState<Step | null>(null);
  const [dataset, setDataset] = useState(
    () => meta.datasets.find((d) => d.preferred)?.path ?? meta.datasets[meta.datasets.length - 1]?.path ?? "",
  );
  const [questions, setQuestions] = useState(meta.default_questions.join("\n"));
  const [slow, setSlow] = useState(true);
  const [startError, setStartError] = useState<string | null>(null);

  const [live, setLive] = useState<LiveRun | null>(null);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Step | null>(null);

  const spec = meta.scenarios.find((s) => s.id === scenario)!;
  const maxAttempts = meta.max_retries + 1;

  // Charger le run de l'URL : en direct s'il est suivi par l'API, sinon depuis run.json
  useEffect(() => {
    setLive(null); setEvents([]); setRun(null); setLoadError(null); setPicked(null);
    if (!runId) return;
    let stopped = false;
    let since = 0;
    let timer: number | undefined;
    const finish = () =>
      api.run(runId).then((r) => !stopped && setRun(r)).catch((e) => !stopped && setLoadError(e.message));
    const poll = async () => {
      try {
        const snap = await api.live(runId, since);
        if (stopped) return;
        since = snap.next_seq;
        setLive(snap);
        if (snap.events.length) setEvents((prev) => [...prev, ...snap.events]);
        if (snap.ended_at) {
          finish();
          onRunFinished();
          return;
        }
        timer = window.setTimeout(poll, POLL_MS);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && e.status === 404) {
          api.run(runId)
            .then((r) => { if (!stopped) { setRun(r); setEvents(eventsFromRun(r)); } })
            .catch((err) => !stopped && setLoadError(err.message));
        } else setLoadError((e as Error).message);
      }
    };
    poll();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [runId, onRunFinished]);

  const timeline = useMemo(() => buildTimeline(events, meta.max_feedback_rounds), [events, meta.max_feedback_rounds]);
  const running = !!live && !live.ended_at;
  const status = live?.status ?? run?.status ?? null;
  const selected: Step | null = picked ?? timeline.current ?? (events.length ? lastTouched(events) : null);

  async function start() {
    setStartError(null);
    try {
      const snap = await api.start({
        scenario, step: spec.step_choices.length ? step ?? spec.default_step : null, dataset,
        questions: questions.split("\n").map((q) => q.trim()).filter(Boolean), pace_s: slow ? 0.6 : 0,
      });
      navigate(`/demo/${snap.run_id}`);
      window.scrollTo({ top: 0 });  // le schéma de ligne doit être visible pendant le run
    } catch (e) {
      setStartError((e as Error).message);
    }
  }

  const scenarioTitle = meta.scenarios.find((s) => s.id === live?.scenario)?.title;

  return (
    <div className="demo">
      <aside className="scenario-form" aria-label="Choisir un scénario">
        <h1>Scénarios</h1>
        <p className="muted">
          Chaque scénario lance le vrai pipeline sur l'extrait choisi. Les pannes et les écarts sont injectés avec
          les mêmes options que la ligne de commande.
        </p>
        <fieldset className="scenarios">
          <legend className="sr-only">Scénario</legend>
          {meta.scenarios.map((s) => (
            <label key={s.id} className={`scenario ${scenario === s.id ? "is-on" : ""}`}>
              <input type="radio" name="scenario" value={s.id} checked={scenario === s.id}
                     onChange={() => { setScenario(s.id); setStep(null); }} />
              <span className="scenario-title">{s.title}</span>
              <span className="scenario-summary">{s.summary}</span>
              <span className="scenario-expected">Résultat attendu : {STATUS_LABEL[s.expected].toLowerCase()}</span>
            </label>
          ))}
        </fieldset>

        {spec.step_choices.length > 0 && (
          <label className="field">
            <span>{scenario === "panne" ? "Agent en panne" : "Étape signalée par l'Auditeur"}</span>
            <select value={step ?? spec.default_step} onChange={(e) => setStep(e.target.value as Step)}>
              {spec.step_choices.map((s) => <option key={s} value={s}>{STEP_LABEL[s]}</option>)}
            </select>
          </label>
        )}

        <label className="field">
          <span>Questions métier, une par ligne</span>
          <textarea rows={4} value={questions} onChange={(e) => setQuestions(e.target.value)} />
          <small>Traitées par l'agent KPI & Semantic. Une question sur une cause ou des effectifs est refusée.</small>
        </label>

        <label className="field">
          <span>Extrait de données</span>
          <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
            {meta.datasets.map((d) => (
              <option key={d.path} value={d.path}>
                {d.name}{d.rows ? `, ${num(d.rows)} lignes` : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="check-field">
          <input type="checkbox" checked={slow} onChange={(e) => setSlow(e.target.checked)} />
          <span>Ralentir pour la présentation (0,6 s de pause avant chaque agent)</span>
        </label>

        <button className="btn-primary" onClick={start} disabled={running || meta.datasets.length === 0}>
          {running ? "Run en cours…" : "Lancer le scénario"}
        </button>
        {startError && <Notice tone="critical">{startError}</Notice>}
        {meta.datasets.length === 0 && (
          <Notice tone="warn">Aucun extrait dans data/raw. Lancez d'abord l'ingestion.</Notice>
        )}
      </aside>

      <div className="demo-main">
        {!runId && (
          <div className="run-empty">
            <LineDiagram timeline={timeline} maxAttempts={maxAttempts} selected={null} onSelect={() => {}} />
            <p>Choisissez un scénario puis lancez-le : chaque agent s'allume quand il travaille.</p>
          </div>
        )}

        {runId && (
          <>
            <header className="run-head">
              <div>
                <h2>{scenarioTitle ?? "Run"}{live?.step ? ` sur ${STEP_LABEL[live.step]}` : ""}</h2>
                <p className="muted">
                  Run <code>{runId}</code>
                  {live?.started_at && `, lancé à ${time(live.started_at)}`}
                  {live && `, ${basename(live.dataset)}`}
                </p>
              </div>
              {status && <StatusPill status={status} published={live?.published ?? run?.published} />}
            </header>
            {loadError && <Notice tone="critical">{loadError}</Notice>}
            {live?.error && <Notice tone="critical">Le run s'est arrêté : {live.error}</Notice>}

            <div className="line-card">
              <LineDiagram timeline={timeline} maxAttempts={maxAttempts} selected={selected} onSelect={setPicked} />
              <Outcome timeline={timeline} published={live?.published ?? run?.published ?? false} />
            </div>

            <Journal events={events} />

            {selected && (
              <>
                <nav className="agent-tabs" aria-label="Agents">
                  {STEPS.map((s, i) => (
                    <button key={s} aria-current={selected === s ? "true" : undefined}
                            className={`agent-tab s-${timeline.stations[s].state}`} onClick={() => setPicked(s)}>
                      <span>{i + 1}</span>{STEP_LABEL[s]}
                    </button>
                  ))}
                </nav>
                <AgentPanel step={selected} station={timeline.stations[selected]} run={run} maxAttempts={maxAttempts}
                            audits={timeline.audits} />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function lastTouched(events: LiveEvent[]): Step | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.kind === "failed" && e.step) return e.step;
  }
  return "auditor_xai";
}

function Outcome({ timeline, published }: { timeline: ReturnType<typeof buildTimeline>; published: boolean }) {
  const { final, audits, feedbacks } = timeline;
  if (!final) return null;
  const last = audits[audits.length - 1];
  let text: string;
  if (final === "success")
    text = feedbacks.length
      ? `Le second audit passe (${last?.passed}/${last?.total} contrôles) : le run est publié.`
      : `L'Auditeur valide ${last?.passed}/${last?.total} contrôles : le run est publié${published ? "" : " (non publié)"}.`;
  else if (final === "unreliable")
    text = "L'écart est toujours là après la correction : run déclaré non fiable. Le tableau de bord garde le dernier run validé.";
  else text = "Un agent a échoué à tous ses essais : l'orchestrateur arrête le run. Les agents suivants ne tournent pas, rien n'est publié.";
  return <p className={`outcome o-${final}`}>{text}</p>;
}

function describe(e: LiveEvent): string {
  const again = e.feedback_round ? " (2e passage, après renvoi)" : "";
  switch (e.kind) {
    case "started":
      return `Essai ${e.attempt} lancé${again}.`;
    case "success":
      return e.message ? `Terminé : ${e.message}.` : "Terminé.";
    case "retry":
      return `Essai ${e.attempt} échoué : ${e.message}. L'orchestrateur relance l'étape.`;
    case "failed":
      return `Échec définitif à l'essai ${e.attempt} : ${e.message}. Run arrêté.`;
    case "audit": {
      const blocking = (e.findings || []).filter((f) => f.severity === "error");
      if (e.approved) return `Audit : ${e.passed}/${e.total} contrôles, aucun écart bloquant.`;
      return `Audit : ${blocking.length} écart(s) bloquant(s). ${blocking.map((f) => `${STEP_LABEL[f.target_step]} : ${f.message}`).join(" ; ")}`;
    }
    case "run":
      return `Run terminé : ${STATUS_LABEL[e.status ?? "failed"].toLowerCase()}.`;
  }
}

function Journal({ events }: { events: LiveEvent[] }) {
  const [open, setOpen] = useState(true);
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    // fait défiler la liste elle-même, pas la page
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [events.length, open]);
  return (
    <section className="journal">
      <button className="journal-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <h3>Journal de l'orchestrateur</h3>
        <span className="muted">{events.length} événements</span>
      </button>
      {open && (
        <ol className="journal-list" ref={list}>
          {events.map((e) => (
            <li key={e.seq} className={`j-${e.kind}${e.kind === "audit" ? (e.approved ? " j-audit-ok" : " j-audit-ko") : ""}`}>
              <time>{time(e.at)}</time>
              <span className="j-step">{e.step ? STEP_LABEL[e.step] : "Orchestrateur"}</span>
              <span className="j-text">{describe(e)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
