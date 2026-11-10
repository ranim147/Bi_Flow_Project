import type { RunStatus, Step } from "./api";

export const STEP_LABEL: Record<Step, string> = {
  data_profiler: "Data Profiler",
  quality_etl: "Qualité / ETL",
  kpi_semantic: "KPI & Semantic",
  analyste_bi: "Analyste BI",
  dashboard_generator: "Dashboard Generator",
  auditor_xai: "Auditeur / XAI",
};

export const STEP_ROLE: Record<Step, string> = {
  data_profiler: "Décrit les données brutes et repère les défauts",
  quality_etl: "Nettoie, minimise (RGPD), fige un instantané",
  kpi_semantic: "Calcule les KPI en SQL, répond aux questions",
  analyste_bi: "Tendance, anomalies, insights avec preuves",
  dashboard_generator: "Choisit les graphiques, écrit la spec",
  auditor_xai: "Rejoue et vérifie chaque chiffre",
};

export const STATUS_LABEL: Record<RunStatus, string> = {
  running: "En cours",
  success: "Publié",
  failed: "Échec",
  unreliable: "Non fiable",
};

const nf = (d: number) =>
  new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });

export function num(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return nf(decimals).format(value);
}

export function kpiValue(value: number | null | undefined, unit: string | null | undefined): string {
  if (value === null || value === undefined) return "masqué";
  switch (unit) {
    case "ratio":
      return `${num(100 * value, 1)} %`;
    case "percent":
      return `${num(value, 1)} %`;
    case "hours":
      return `${num(value, 1)} h`;
    case "per_10k":
      return num(value, 1);
    default:
      return num(value, 0);
  }
}

export const UNIT_HINT: Record<string, string> = {
  count: "demandes",
  hours: "heures",
  percent: "%",
  ratio: "part",
  per_10k: "pour 10 000 habitants",
};

export function time(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("fr-FR", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export function day(iso: string): string {
  return new Date(iso + "T00:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

/** Nom de fichier d'un chemin Windows ou POSIX. */
export const basename = (path: string) => path.replace(/\\/g, "/").split("/").pop() ?? path;

export const short =(hash: string | null | undefined, n = 12) => (hash ? hash.slice(0, n) : "");

/** Libellé lisible d'un contrôle d'audit : « kpi_egal_requete:total_requests » -> deux parties. */
export function checkParts(name: string): [string, string | null] {
  const [head, ...rest] = name.split(":");
  return [head.replace(/_/g, " "), rest.length ? rest.join(":") : null];
}
