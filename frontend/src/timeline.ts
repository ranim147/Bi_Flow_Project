// Transforme les événements d'un run (en direct, ou reconstruits depuis run.json) en état
// du schéma de ligne : une station par agent, les renvois de l'Auditeur, le statut final.
import type { Check, Finding, LiveEvent, Run, RunStatus, Step } from "./api";

export const STEPS: Step[] = [
  "data_profiler",
  "quality_etl",
  "kpi_semantic",
  "analyste_bi",
  "dashboard_generator",
  "auditor_xai",
];

export type StationState = "idle" | "running" | "retrying" | "done" | "failed" | "queued";

export interface Station {
  step: Step;
  state: StationState;
  attempt: number;
  passes: number; // exécutions réussies (1, ou 2 après un renvoi)
  round: number;
  message?: string | null;
  inputRows?: number | null;
  outputRows?: number | null;
  checks: Check[];
  durationS?: number | null;
  correction?: string | null;
}

export interface Feedback {
  round: number;
  target: Step;
  findings: Finding[];
}

export interface Audit {
  round: number;
  approved: boolean;
  passed?: number;
  total?: number;
  findings: Finding[];
}

export interface Timeline {
  stations: Record<Step, Station>;
  feedbacks: Feedback[];
  audits: Audit[];
  final: RunStatus | null;
  current: Step | null;
}

export function buildTimeline(events: LiveEvent[], maxRounds = 1): Timeline {
  const stations = Object.fromEntries(
    STEPS.map((s) => [s, { step: s, state: "idle", attempt: 0, passes: 0, round: 0, checks: [] } as Station]),
  ) as Record<Step, Station>;
  const feedbacks: Feedback[] = [];
  const audits: Audit[] = [];
  let final: RunStatus | null = null;
  let current: Step | null = null;

  for (const e of events) {
    const st = e.step ? stations[e.step] : undefined;
    switch (e.kind) {
      case "started":
        if (!st) break;
        st.state = "running";
        st.attempt = e.attempt ?? 1;
        st.round = e.feedback_round ?? 0;
        st.correction = e.correction;
        current = st.step;
        break;
      case "retry":
        if (!st) break;
        st.state = "retrying";
        st.message = e.message;
        break;
      case "failed":
        if (!st) break;
        st.state = "failed";
        st.message = e.message;
        st.checks = e.validation ?? [];
        current = null;
        break;
      case "success":
        if (!st) break;
        st.state = "done";
        st.passes += 1;
        st.message = e.message;
        st.inputRows = e.input_rows;
        st.outputRows = e.output_rows;
        st.checks = e.validation ?? [];
        st.durationS = e.duration_s;
        // un audit qui vient de renvoyer le run sera lui aussi refait
        if (st.step === "auditor_xai" && feedbacks.some((f) => f.round === (e.feedback_round ?? 0) + 1))
          st.state = "queued";
        current = null;
        break;
      case "audit": {
        const round = e.feedback_round ?? 0;
        const findings = e.findings ?? [];
        audits.push({ round, approved: !!e.approved, passed: e.passed, total: e.total, findings });
        const blocking = findings.filter((f) => f.severity === "error");
        if (blocking.length && round < maxRounds) {
          const target = blocking
            .map((f) => f.target_step)
            .sort((a, b) => STEPS.indexOf(a) - STEPS.indexOf(b))[0];
          feedbacks.push({ round: round + 1, target, findings: blocking });
          // l'étape fautive et tout l'aval seront réexécutés
          for (const s of STEPS.slice(STEPS.indexOf(target), -1)) stations[s].state = "queued";
        }
        break;
      }
      case "run":
        final = e.status ?? null;
        current = null;
        break;
    }
  }
  return { stations, feedbacks, audits, final, current };
}

/** Événements équivalents pour un run terminé (lu depuis run.json). */
export function eventsFromRun(run: Run): LiveEvent[] {
  const events: LiveEvent[] = [];
  let seq = 0;
  const push = (e: Omit<LiveEvent, "seq" | "at">, at: string) => events.push({ seq: seq++, at, ...e });
  const audit = run.outputs.auditor_xai;
  const errors = (run.findings || []).filter((f) => f.severity === "error");
  run.history.forEach((h, idx) => {
    for (let a = 1; a <= h.attempts; a++) {
      push({ kind: "started", step: h.step, attempt: a, feedback_round: h.feedback_round }, h.started_at);
      if (h.step === "auditor_xai" && a === h.attempts && h.status === "success") {
        const rerun = run.history.slice(idx + 1).some((x) => x.feedback_round > h.feedback_round);
        const approved = !rerun && run.status === "success";
        push({
          kind: "audit", step: h.step, feedback_round: h.feedback_round, approved,
          passed: audit?.checks?.filter((c: Check) => c.passed).length,
          total: audit?.checks?.length,
          findings: approved ? [] : errors,
        }, h.ended_at);
      }
      const kind = a < h.attempts ? "retry" : h.status === "success" ? "success" : "failed";
      push({
        kind, step: h.step, attempt: a, feedback_round: h.feedback_round,
        message: kind === "success" ? (run.outputs[h.step]?.notes || []).join("; ") : h.message,
        validation: kind === "success" ? run.validations[h.step]?.checks : [],
        input_rows: run.outputs[h.step]?.input_rows, output_rows: run.outputs[h.step]?.output_rows,
      }, h.ended_at);
    }
  });
  const last = run.history[run.history.length - 1];
  push({ kind: "run", status: run.status }, last?.ended_at || "");
  return events;
}
