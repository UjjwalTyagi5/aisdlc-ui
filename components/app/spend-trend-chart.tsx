"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Monthly spend over time, one line per Business Unit.
 *
 * Hand-rolled SVG rather than a charting dependency: this repo has none (every
 * numeric visual here is inline SVG or a CSS-width div — see
 * components/app/cost-meter.tsx::Sparkline), and one 6×6-point line chart is
 * not worth the first one.
 *
 * PALETTE — the brand's, and only three of them. The values live as
 * `--series-1..3` in app/globals.css with separate light and dark steps; they
 * are referenced rather than inlined so the chart follows the theme without
 * this component knowing which one is active. That file carries the rationale
 * and the validator results — read it before changing a colour, and do not
 * eyeball a replacement.
 *
 * Three is a real ceiling, not a placeholder: the guideline allows one warm
 * ramp plus a neutral for data visualisation, and no fourth colour drawn from
 * those can stay distinguishable from the orange or the tangerine. Series
 * beyond the third therefore fold into a single "Other" line, which is the
 * honest failure mode — inventing a fourth hue would be off-brand, and
 * recycling one would silently merge two units.
 *
 * Status colours (success/warning/destructive/info) are deliberately absent:
 * they mean a state, and a green line would read as good news.
 */
const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"] as const;

/** Beyond this the rest folds into one "Other" line — hues are never cycled. */
const MAX_SERIES = SERIES_COLORS.length;

export interface SpendSeries {
  workspaceId: string;
  name: string;
  /** One point per month, oldest first. */
  points: number[];
}

const W = 720;
const H = 264;
const PAD = { top: 16, right: 116, bottom: 30, left: 62 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

/** Axis-tick money: "$0" / "$500" / "$5k" / "$12.5k" — a trailing ".0" is noise. */
const usdShort = (n: number) => {
  if (n < 1000) return `$${Math.round(n)}`;
  const k = n / 1000;
  return `$${Number.isInteger(k) ? k : k.toFixed(1)}k`;
};

const usdFull = (n: number) =>
  n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/**
 * Axis ticks a person would have chosen: a round step (1/2/2.5/5 × 10ⁿ) at or
 * just above `max / count`, extended to cover the data.
 *
 * Scaling the max by a padding factor and slicing it into equal parts is the
 * obvious approach and produces "$3.4k / $6.9k / $10k" — technically correct
 * gridlines that nobody can read a value off. The axis exists to be estimated
 * against, which only works when the numbers are round.
 */
function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rough) ?? 10 * mag;
  const ticks: number[] = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Number(v.toFixed(6)));
  return ticks;
}

/** "2026-07" → "Jul". */
function monthLabel(ym: string): string {
  const [y, m] = ym.split("-");
  if (!y || !m) return ym;
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString(undefined, { month: "short" });
}

export function SpendTrendChart({
  months,
  series,
  className,
}: {
  months: string[];
  series: SpendSeries[];
  className?: string;
}) {
  const [hover, setHover] = React.useState<number | null>(null);
  const svgRef = React.useRef<SVGSVGElement>(null);

  // Colour follows the entity, not its rank: the index is taken from a stable
  // sort on workspaceId, so adding or removing a unit never repaints the ones
  // that stayed. A 7th unit folds into "Other" rather than minting a hue.
  const plotted = React.useMemo(() => {
    const ordered = [...series].sort((a, b) => a.workspaceId.localeCompare(b.workspaceId));
    if (ordered.length <= MAX_SERIES) return ordered;
    const kept = ordered.slice(0, MAX_SERIES - 1);
    const rest = ordered.slice(MAX_SERIES - 1);
    return [
      ...kept,
      {
        workspaceId: "__other",
        name: `Other (${rest.length})`,
        points: months.map((_, i) => rest.reduce((sum, s) => sum + (s.points[i] ?? 0), 0)),
      },
    ];
  }, [series, months]);

  const max = Math.max(1, ...plotted.flatMap((s) => s.points));
  // The top gridline IS the top of the plot, so the axis ends on a round
  // number instead of on an arbitrary headroom multiple.
  const gridValues = niceTicks(max);
  const yMax = gridValues[gridValues.length - 1]!;

  const x = (i: number) =>
    PAD.left + (months.length <= 1 ? PLOT_W / 2 : (i / (months.length - 1)) * PLOT_W);
  const y = (v: number) => PAD.top + PLOT_H - (v / yMax) * PLOT_H;

  // Direct labels for ≤ 4 series (identity without a round trip to the legend),
  // nudged apart so two lines ending close together stay legible.
  const showDirectLabels = plotted.length <= 4;
  const endLabels = React.useMemo(() => {
    const raw = plotted.map((s, idx) => ({
      name: s.name,
      color: SERIES_COLORS[idx]!,
      y: y(s.points[s.points.length - 1] ?? 0),
    }));
    const sorted = [...raw].sort((a, b) => a.y - b.y);
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]!;
      const cur = sorted[i]!;
      if (cur.y - prev.y < 14) cur.y = prev.y + 14;
    }
    return sorted;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plotted, yMax]);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || months.length === 0) return;
    // Map the pointer through the rendered box into the viewBox's own units —
    // the SVG scales to its container, so clientX alone is meaningless here.
    const vx = ((e.clientX - rect.left) / rect.width) * W;
    const ratio = (vx - PAD.left) / PLOT_W;
    const idx = Math.round(ratio * (months.length - 1));
    setHover(Math.min(months.length - 1, Math.max(0, idx)));
  };

  if (months.length === 0 || plotted.length === 0) {
    return (
      <p className="text-muted-foreground py-8 text-center font-mono text-[12px]">
        No spend recorded yet.
      </p>
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      <div className="relative">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full"
          role="img"
          aria-label="Monthly spend by business unit"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          {/* Grid — recessive on purpose; it is scaffolding, not data. */}
          {gridValues.map((v) => (
            <g key={v}>
              <line
                x1={PAD.left}
                x2={PAD.left + PLOT_W}
                y1={y(v)}
                y2={y(v)}
                className="stroke-line-soft"
                strokeWidth={1}
              />
              <text
                x={PAD.left - 10}
                y={y(v) + 4}
                textAnchor="end"
                className="fill-muted-foreground font-mono text-[11px]"
              >
                {usdShort(v)}
              </text>
            </g>
          ))}

          {/* Month labels */}
          {months.map((m, i) => (
            <text
              key={m}
              x={x(i)}
              y={H - 10}
              textAnchor="middle"
              className="fill-muted-foreground font-mono text-[11px]"
            >
              {monthLabel(m)}
            </text>
          ))}

          {/* Crosshair sits under the marks so it never cuts across them. */}
          {hover !== null && (
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={PAD.top}
              y2={PAD.top + PLOT_H}
              className="stroke-muted-foreground"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
          )}

          {/* Series */}
          {plotted.map((s, idx) => {
            const color = SERIES_COLORS[idx]!;
            const d = s.points
              .map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
              .join(" ");
            return (
              <g key={s.workspaceId}>
                <path
                  d={d}
                  fill="none"
                  stroke={color}
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {hover !== null && s.points[hover] !== undefined && (
                  // 2px surface ring keeps overlapping markers separable.
                  <circle
                    cx={x(hover)}
                    cy={y(s.points[hover]!)}
                    r={5}
                    fill={color}
                    className="stroke-panel-elevated"
                    strokeWidth={2}
                  />
                )}
              </g>
            );
          })}

          {/* Direct labels — identity without a trip to the legend. */}
          {showDirectLabels &&
            endLabels.map((l) => (
              <text
                key={l.name}
                x={PAD.left + PLOT_W + 10}
                y={l.y + 4}
                className="fill-muted-foreground font-mono text-[11px]"
              >
                {l.name.length > 16 ? `${l.name.slice(0, 15)}…` : l.name}
              </text>
            ))}
        </svg>

        {/* Tooltip */}
        {hover !== null && (
          <div
            className="border-line-soft bg-panel-elevated pointer-events-none absolute top-2 rounded-lg border px-3 py-2 shadow-lg"
            style={{
              left: `${((x(hover) + 12) / W) * 100}%`,
              transform: hover > months.length / 2 ? "translateX(-110%)" : undefined,
            }}
          >
            <p className="font-mono text-[10px] tracking-wider uppercase">{months[hover]}</p>
            <ul className="mt-1 space-y-0.5">
              {plotted.map((s, idx) => (
                <li key={s.workspaceId} className="flex items-center gap-2 font-mono text-[11px]">
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{ background: SERIES_COLORS[idx] }}
                    aria-hidden
                  />
                  <span className="text-muted-foreground">{s.name}</span>
                  <span className="ml-auto font-semibold">{usdFull(s.points[hover] ?? 0)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Legend — always present for ≥ 2 series, so identity is never colour alone. */}
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {plotted.map((s, idx) => (
          <li key={s.workspaceId} className="flex items-center gap-1.5">
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: SERIES_COLORS[idx] }}
              aria-hidden
            />
            <span className="text-muted-foreground font-mono text-[11px]">{s.name}</span>
          </li>
        ))}
      </ul>

      {/* The same numbers as a table — the non-visual route to this chart, and
          the relief the contrast check obliges for any thin coloured mark. */}
      <details className="group">
        <summary className="text-muted-foreground hover:text-foreground cursor-pointer font-mono text-[11px]">
          View as table
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[28rem] text-left font-mono text-[11px]">
            <thead>
              <tr className="text-muted-foreground border-line-soft border-b">
                <th scope="col" className="py-1.5 pr-3 font-medium">
                  Business Unit
                </th>
                {months.map((m) => (
                  <th key={m} scope="col" className="py-1.5 pr-3 text-right font-medium">
                    {monthLabel(m)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {plotted.map((s) => (
                <tr key={s.workspaceId} className="border-line-soft border-b last:border-b-0">
                  <th scope="row" className="py-1.5 pr-3 font-medium">
                    {s.name}
                  </th>
                  {s.points.map((v, i) => (
                    <td key={months[i] ?? i} className="py-1.5 pr-3 text-right">
                      {usdFull(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
