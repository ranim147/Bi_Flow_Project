// Client de l'API BIFlow (src/biflow/api/app.py). Les sorties d'agents restent en `any` :
// leur schéma est défini côté Python (pydantic) et affiché tel quel.

export type Step =
  | "data_profiler"
  | "quality_etl"
  | "kpi_semantic"
  | "analyste_bi"
  | "dashboard_generator"
  | "auditor_xai";

export type RunStatus = "running" | "success" | "failed" | "unreliable";

export interface Scenario {
  id: string;
  title: string;
  summary: string;
  expected: RunStatus;
  step_choices: Step[];
  default_step?: Step;
}

export interface Dataset {
  path: string;
  name: string;
  rows: number | null;
  notes: string[];
  preferred: boolean;
}

export interface Meta {
  steps: Step[];
  scenarios: Scenario[];
  datasets: Dataset[];
  default_questions: string[];
  example_questions: string[];
  llm: string | null;
  min_group_size: number;
  max_retries: number;
  max_feedback_rounds: number;
  busy: boolean;
}

export interface Check {
  name: string;
  passed: boolean;
  message: string;
  severity?: "error" | "warning";
  target_step?: string;
}

export interface Finding {
  target_step: Step;
  message: string;
  severity: "error" | "warning";
}

export interface LiveEvent {
  seq: number;
  at: string;
  kind: "started" | "success" | "retry" | "failed" | "audit" | "run";
  step?: Step;
  attempt?: number;
  feedback_round?: number;
  message?: string | null;
  input_rows?: number | null;
  output_rows?: number | null;
  validation?: Check[];
  error_type?: string | null;
  correction?: string | null;
  duration_s?: number | null;
  status?: RunStatus;
  approved?: boolean;
  passed?: number;
  total?: number;
  findings?: Finding[];
}

export interface LiveRun {
  run_id: string;
  scenario: string;
  step: Step | null;
  dataset: string;
  questions: string[];
  pace_s: number;
  started_at: string;
  ended_at: string | null;
  status: RunStatus;
  published: boolean;
  error: string | null;
  events: LiveEvent[];
  next_seq: number;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  published: boolean;
  is_current: boolean;
  dataset: string;
  questions: number;
  started_at: string | null;
  ended_at: string | null;
  feedback_rounds: number;
  retries: number;
  steps_done: number;
  audit: string | null;
}

export interface QueryRecord {
  id: string;
  purpose: string;
  sql: string;
  snapshot_hash: string;
  executed_at: string;
  rows: Record<string, unknown>[];
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Run = {
  run_id: string;
  status: RunStatus;
  published: boolean;
  dataset: { table: string; file_path: string | null };
  questions: string[];
  outputs: Record<string, any>;
  validations: Record<string, { step: string; checks: Check[] }>;
  errors: { step: string; attempt: number; error_type: string; message: string }[];
  history: { step: Step; status: "success" | "failed"; attempts: number; feedback_round: number;
             started_at: string; ended_at: string; message: string }[];
  corrections: Record<string, string>;
  feedback_rounds: number;
  findings: Finding[];
};

export interface ChartSpec {
  id: string;
  title: string;
  chart_type: "bar" | "bar_h" | "line" | "kpi_card" | "table" | "text";
  kpi_refs: string[];
  query_ids: string[];
  unit: string | null;
  value: number | null;
  data: any[];
}

export interface Published {
  pointer: { run_id: string; spec_hash: string; published_at: string };
  spec: { run_id: string; title: string; snapshot_hash: string; charts: ChartSpec[] };
  run: Run;
}

export interface Answer {
  question: string;
  question_sent: string | null;
  personal_data_found: string[];
  mode: "llm" | "baseline" | "refused" | "none";
  sql: string | null;
  rows: Record<string, unknown>[];
  query_id: string | null;
  error: string | null;
  refusal: string | null;
  duration_s: number;
  cross_check: "concordant" | "divergent" | "non vérifiable" | null;
  baseline_sql: string | null;
}

export interface AskResult {
  answer: Answer;
  queries: QueryRecord[];
  llm: string | null;
  run_id: string;
  snapshot_hash: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch {
    throw new ApiError(0, "L'API ne répond pas. Lancez-la avec : python -m biflow.api");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* corps non JSON */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  meta: () => call<Meta>("/api/meta"),
  runs: () => call<RunSummary[]>("/api/runs"),
  run: (id: string) => call<Run>(`/api/runs/${id}`),
  live: (id: string, since = 0) => call<LiveRun>(`/api/runs/${id}/live?since=${since}`),
  start: (body: { scenario: string; step?: string | null; dataset?: string; questions: string[]; pace_s: number }) =>
    call<LiveRun>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  quarantine: (id: string, limit = 25) =>
    call<{ total: number; by_rule: Record<string, number>; rows: Record<string, unknown>[] }>(
      `/api/runs/${id}/quarantine?limit=${limit}`,
    ),
  published: () => call<Published>("/api/published"),
  ask: (question: string) => call<AskResult>("/api/ask", { method: "POST", body: JSON.stringify({ question }) }),
};
