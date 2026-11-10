// Schéma de ligne du pipeline : les 6 agents sont des stations, le run avance de l'une à
// l'autre. Un essai raté reste sur sa station (compteur d'essais), un renvoi de l'Auditeur
// est tracé comme une bretelle qui remonte la ligne jusqu'à l'étape fautive.
import { useLayoutEffect, useRef, useState } from "react";
import type { Step } from "../api";
import { STEP_LABEL, STEP_ROLE } from "../format";
import { STEPS, type Station, type Timeline } from "../timeline";

interface Props {
  timeline: Timeline;
  maxAttempts: number;
  selected: Step | null;
  onSelect: (step: Step) => void;
}

const STATE_TEXT: Record<Station["state"], string> = {
  idle: "en attente",
  running: "en cours",
  retrying: "nouvel essai",
  done: "terminé",
  failed: "échec",
  queued: "à refaire",
};

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(960);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Coupe un texte en lignes d'au plus `max` caractères (sans couper les mots). */
function wrap(text: string, max: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    if ((line + " " + word).trim().length > max && line) {
      lines.push(line);
      line = word;
    } else line = (line + " " + word).trim();
  }
  if (line) lines.push(line);
  return lines.slice(0, 3);
}

function StationMark({ s, index, x, y }: { s: Station; index: number; x: number; y: number }) {
  const r = 17;
  switch (s.state) {
    case "done":
      return (
        <g>
          <circle cx={x} cy={y} r={r} className="st-fill-line" />
          <path d={`M${x - 7} ${y} l5 5 l9 -10`} className="st-tick" />
        </g>
      );
    case "failed":
      return (
        <g>
          <circle cx={x} cy={y} r={r} className="st-fill-critical" />
          <path d={`M${x - 6} ${y - 6} l12 12 M${x + 6} ${y - 6} l-12 12`} className="st-tick" />
        </g>
      );
    case "running":
    case "retrying":
      return (
        <g>
          <circle cx={x} cy={y} r={r + 7} className={s.state === "running" ? "st-pulse" : "st-pulse warn"} />
          <circle cx={x} cy={y} r={r} className={s.state === "running" ? "st-ring" : "st-ring warn"} />
          <text x={x} y={y + 5} className="st-num">{index + 1}</text>
        </g>
      );
    case "queued":
      return (
        <g>
          <circle cx={x} cy={y} r={r} className="st-ring queued" />
          <text x={x} y={y + 5} className="st-num">{index + 1}</text>
        </g>
      );
    default:
      return (
        <g>
          <circle cx={x} cy={y} r={r} className="st-ring idle" />
          <text x={x} y={y + 5} className="st-num idle">{index + 1}</text>
        </g>
      );
  }
}

export default function LineDiagram({ timeline, maxAttempts, selected, onSelect }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const vertical = width < 620;
  const { stations, feedbacks, final } = timeline;
  const reached = (i: number) => i < STEPS.length && stations[STEPS[i]].state !== "idle";

  if (vertical) {
    return (
      <div ref={ref} className="line-vertical">
        {STEPS.map((step, i) => {
          const s = stations[step];
          return (
            <button key={step} className={`lv-row ${selected === step ? "is-selected" : ""}`}
                    onClick={() => onSelect(step)}>
              <span className={`lv-dot lv-${s.state}`}>{s.state === "done" ? "" : i + 1}</span>
              <span className="lv-name">{STEP_LABEL[step]}</span>
              <span className="lv-state">{STATE_TEXT[s.state]}{s.attempt > 1 ? `, essai ${s.attempt}` : ""}</span>
            </button>
          );
        })}
        <div className={`lv-terminal t-${final ?? "pending"}`}>{terminalText(final)}</div>
      </div>
    );
  }

  const cols = STEPS.length + 1;
  const colW = width / cols;
  const cx = (i: number) => colW * i + colW / 2;
  const y = 112;
  const height = 236;
  const charsPerLine = Math.max(14, Math.floor(colW / 7.2));

  return (
    <div ref={ref} className="line-diagram">
      <svg width={width} height={height} role="group" aria-label="Avancement du pipeline">
        {/* voie : gris tant que la station suivante n'est pas atteinte */}
        {STEPS.map((_, i) => (
          <line key={i} x1={cx(i)} x2={cx(i + 1)} y1={y} y2={y}
                className={reached(i + 1) || (i === STEPS.length - 1 && final === "success") ? "track on" : "track"} />
        ))}

        {/* bretelles de renvoi de l'Auditeur */}
        {feedbacks.map((f) => {
          const from = cx(STEPS.length - 1);
          const to = cx(STEPS.indexOf(f.target));
          const top = 34 + (f.round - 1) * 10;
          return (
            <g key={f.round} className="feedback">
              <path d={`M${from} ${y - 20} C${from} ${top}, ${to} ${top}, ${to} ${y - 20}`} />
              <path d={`M${to - 5} ${y - 29} L${to} ${y - 20} L${to + 5} ${y - 29}`} />
              <text x={(from + to) / 2} y={top - 2} className="feedback-label">
                Renvoi de l'Auditeur vers {STEP_LABEL[f.target]}
              </text>
            </g>
          );
        })}

        {STEPS.map((step, i) => {
          const s = stations[step];
          const x = cx(i);
          const role = wrap(STEP_ROLE[step], charsPerLine);
          const status =
            s.state === "retrying" || (s.state === "running" && s.attempt > 1)
              ? `essai ${s.attempt} sur ${maxAttempts}`
              : s.state === "failed"
                ? `échec après ${s.attempt} essais`
                : s.passes > 1
                  ? "2 passages"
                  : STATE_TEXT[s.state];
          return (
            <g key={step} className={`station ${selected === step ? "is-selected" : ""} s-${s.state}`}
               role="button" tabIndex={0} aria-label={`${STEP_LABEL[step]} : ${status}`}
               onClick={() => onSelect(step)}
               onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect(step))}>
              <rect x={x - colW / 2 + 4} y={y - 34} width={colW - 8} height={height - y + 30} rx={10}
                    className="station-hit" />
              <StationMark s={s} index={i} x={x} y={y} />
              <text x={x} y={y + 44} className="st-name">{STEP_LABEL[step]}</text>
              {role.map((line, k) => (
                <text key={k} x={x} y={y + 62 + k * 15} className="st-role">{line}</text>
              ))}
              <text x={x} y={y + 64 + role.length * 15 + 6} className={`st-status s-${s.state}`}>{status}</text>
            </g>
          );
        })}

        {/* terminus : publication */}
        <g className={`terminal t-${final ?? "pending"}`}>
          <rect x={cx(STEPS.length) - 46} y={y - 20} width={92} height={40} rx={20} />
          <text x={cx(STEPS.length)} y={y + 5}>{final === "success" ? "Publié" : final ? "Bloqué" : "Publication"}</text>
          <text x={cx(STEPS.length)} y={y + 44} className="st-role">{terminalText(final)}</text>
        </g>
      </svg>
    </div>
  );
}

function terminalText(final: Timeline["final"]): string {
  switch (final) {
    case "success":
      return "dashboard mis à jour";
    case "failed":
      return "rien n'est publié";
    case "unreliable":
      return "rien n'est publié";
    default:
      return "si l'audit passe";
  }
}
