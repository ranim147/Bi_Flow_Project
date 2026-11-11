// Graphiques en SVG : une seule série par graphique (une seule couleur), anomalies en rouge
// réservé au statut « critique », toujours accompagnées de leur légende. Survol = infobulle.
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { day, kpiValue, num } from "../format";

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** Graduations « rondes » : 0, 100, 200… */
function ticks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = 0; v <= max + 1e-9; v += step) out.push(v);
  if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
  return out;
}

const compact = (v: number) =>
  v >= 10000 ? `${num(v / 1000, 0)} k` : num(v, v < 10 && v % 1 ? 1 : 0);

export interface Point {
  key: string;
  value: number | null;
  anomaly?: boolean;
}

export function DailyLine({ data, height = 280 }: { data: Point[]; height?: number }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 44, r: 12, t: 12, b: 28 };
  const pts = useMemo(() => data.filter((d) => d.value !== null) as (Point & { value: number })[], [data]);
  const max = Math.max(...pts.map((d) => d.value), 1);
  const yt = ticks(max);
  const yMax = yt[yt.length - 1];
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (pts.length > 1 ? (i / (pts.length - 1)) * iw : iw / 2);
  const y = (v: number) => pad.t + ih - (v / yMax) * ih;
  const path = pts.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(d.value).toFixed(1)}`).join("");
  const years = pts
    .map((d, i) => ({ i, d }))
    .filter(({ d, i }) => i === 0 || d.key.slice(0, 4) !== pts[i - 1].key.slice(0, 4))
    .filter(({ d }) => d.key.slice(5) <= "01-31");
  const anomalies = pts.map((d, i) => ({ d, i })).filter(({ d }) => d.anomaly);

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = (e.clientX - rect.left) / rect.width;
    setHover(Math.max(0, Math.min(pts.length - 1, Math.round(rel * (pts.length - 1)))));
  }
  const h = hover !== null ? pts[hover] : null;

  return (
    <div ref={ref} className="chart">
      <div className="legend">
        <span><i className="sw sw-line" /> demandes par jour</span>
        <span><i className="sw sw-dot" /> anomalie détectée</span>
      </div>
      <svg width={width} height={height} role="img"
           aria-label={`Demandes par jour, ${pts.length} jours, ${anomalies.length} anomalies marquées`}>
        {yt.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 8} y={y(t) + 4} className="axis" textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        {years.map(({ i, d }) => (
          <text key={d.key} x={x(i)} y={height - 8} className="axis" textAnchor="start">{d.key.slice(0, 4)}</text>
        ))}
        <path d={path} className="series-line" />
        {anomalies.map(({ d, i }) => (
          <circle key={d.key} cx={x(i)} cy={y(d.value)} r={5} className="anomaly-dot" />
        ))}
        {h && hover !== null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} className="crosshair" />
            <circle cx={x(hover)} cy={y(h.value)} r={4.5} className={h.anomaly ? "anomaly-dot" : "hover-dot"} />
          </g>
        )}
        <rect x={pad.l} y={pad.t} width={iw} height={ih} fill="transparent"
              onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
      </svg>
      {h && hover !== null && (
        <div className="tooltip" style={{ left: Math.min(x(hover) + 12, width - 190), top: 30 }}>
          <strong>{day(h.key)}</strong>
          <span>{num(h.value)} demandes</span>
          {h.anomaly && <span className="tt-anomaly">anomalie détectée</span>}
        </div>
      )}
    </div>
  );
}

export interface Bar {
  key: string;
  value: number | null;
  masked?: boolean;
}

export function Bars({ data, unit, horizontal = false, highlight }: {
  data: Bar[]; unit: string | null; horizontal?: boolean; highlight?: string;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<string | null>(null);
  const values = data.map((d) => d.value ?? 0);
  const max = unit === "percent" ? 100 : Math.max(...values, 1);
  const yt = ticks(max);
  const vMax = unit === "percent" ? 100 : yt[yt.length - 1];
  const label = (v: number | null) => kpiValue(v, unit);

  if (horizontal) {
    const rowH = 30;
    const labelW = Math.min(220, width * 0.42);
    const valueW = 72;
    const iw = Math.max(40, width - labelW - valueW);
    return (
      <div ref={ref} className="chart">
        <svg width={width} height={data.length * rowH + 4} role="img" aria-label="Diagramme en barres">
          {data.map((d, i) => {
            const w = d.value === null ? 0 : (d.value / vMax) * iw;
            const yy = i * rowH;
            return (
              <g key={d.key} onMouseEnter={() => setHover(d.key)} onMouseLeave={() => setHover(null)}>
                <rect x={0} y={yy} width={width} height={rowH} fill="transparent" />
                <text x={labelW - 10} y={yy + 19} className="axis-cat" textAnchor="end">
                  {d.key.length > 30 ? d.key.slice(0, 29) + "…" : d.key}
                </text>
                {d.value !== null && (
                  <path d={barH(labelW, yy + 6, Math.max(w, 2), 18)}
                        className={`bar ${hover === d.key || highlight === d.key ? "is-hover" : ""}`} />
                )}
                <text x={labelW + w + 8} y={yy + 19} className="bar-value">
                  {d.masked ? "masqué" : label(d.value)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    );
  }

  const height = 240;
  const pad = { l: 44, r: 8, t: 22, b: 40 };
  const iw = width - pad.l - pad.r;
  const ih = height - pad.t - pad.b;
  const band = iw / data.length;
  const bw = Math.min(40, band * 0.56);
  const y = (v: number) => pad.t + ih - (v / vMax) * ih;
  return (
    <div ref={ref} className="chart">
      <svg width={width} height={height} role="img" aria-label="Diagramme en colonnes">
        {yt.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 8} y={y(t) + 4} className="axis" textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const cx = pad.l + band * i + band / 2;
          const v = d.value ?? 0;
          return (
            <g key={d.key} onMouseEnter={() => setHover(d.key)} onMouseLeave={() => setHover(null)}>
              <rect x={cx - band / 2} y={pad.t} width={band} height={ih + pad.b} fill="transparent" />
              {d.value !== null && (
                <path d={barV(cx - bw / 2, y(v), bw, pad.t + ih - y(v))}
                      className={`bar ${hover === d.key || highlight === d.key ? "is-hover" : ""}`} />
              )}
              <text x={cx} y={(d.value !== null ? y(v) : pad.t + ih) - 7} className="bar-value" textAnchor="middle">
                {d.masked ? "masqué" : label(d.value)}
              </text>
              {wrapCat(d.key, band).map((line, k) => (
                <text key={k} x={cx} y={pad.t + ih + 16 + k * 13} className="axis-cat" textAnchor="middle">{line}</text>
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function wrapCat(text: string, band: number): string[] {
  const max = Math.max(6, Math.floor(band / 6.5));
  if (text.length <= max) return [text];
  const words = text.split(" ");
  if (words.length > 1) {
    const mid = Math.ceil(words.length / 2);
    return [words.slice(0, mid).join(" "), words.slice(mid).join(" ")].map((l) =>
      l.length > max ? l.slice(0, max - 1) + "…" : l,
    );
  }
  return [text.slice(0, max - 1) + "…"];
}

/** Colonne : extrémité arrondie (4 px) en haut, carrée sur la ligne de base. */
function barV(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, h, w / 2);
  return `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`;
}

function barH(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w, h / 2);
  return `M${x} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h - r}Q${x + w} ${y + h} ${x + w - r} ${y + h}H${x}Z`;
}
