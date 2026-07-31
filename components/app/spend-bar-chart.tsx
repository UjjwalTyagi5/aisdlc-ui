"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import type { SpendSeriesEntry } from "@/lib/schemas/spend-series";

/**
 * Monthly spend as grouped bars — one bar per series within each month.
 *
 * Hand-rolled SVG rather than a charting dependency: this repo has none (every
 * numeric visual here is inline SVG or a CSS-width div — see
 * components/app/cost-meter.tsx::Sparkline), and this is not worth the first
 * one.
 *
 * PALETTE — the brand's, and only three of them. The values live as
 * `--series-1..3` in app/globals.css with separate light and dark steps; they
 * are referenced rather than inlined so the chart follows the theme without
 * this component knowing which one is active. That file carries the rationale
 * and the validator results — read it before changing a colour, and do not
 * eyeball a replacement.
 *
 * Three is a real ceiling, not a placeholder: the brand allows one warm ramp
 * plus a neutral for data visualisation, and no fourth colour drawn from those
 * stays distinguishable. Series beyond the third fold into a single "Other"
 * bar, which is the honest failure mode — inventing a fourth hue would be
 * off-brand, and recycling one would silently merge two units.
 *
 * Status colours (success/warning/destructive/info) are deliberately absent:
 * they mean a state, and a green bar would read as good news.
 */
const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"] as const;
const MAX_SERIES = SERIES_COLORS.length;

const W = 720;
const H = 264;
const PAD = { top: 16, right: 16, bottom: 30, left: 62 };
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
 * just above `max / count`, extended to cover the data. Scaling the max by a
 * padding factor and slicing it into equal parts gives "$3.4k / $6.9k" —
 * gridlines nobody can read a value off.
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

export function SpendBarChart({
  months,
  series,
  className,
}: {
  months: string[];
  series: SpendSeriesEntry[];
  className?: string;
}) {
  const [hover, setHover] = React.useState<number | null>(null);
  const svgRef = React.useRef<SVGSVGElement>(null);

  const plotted = React.useMemo(() => {
    // WHICH series survive the fold is decided in the order the server sent —
    // largest spend first. Re-sorting before slicing (an earlier version sorted
    // by id here) silently turned "top 2 and the rest" into "alphabetically
    // first 2 and the rest", so the chart could bury its biggest spender inside
    // "Other" while showing two small ones by name.
    const kept = series.slice(0, MAX_SERIES - (series.length > MAX_SERIES ? 1 : 0));
    const rest = series.slice(kept.length);

    // Colour follows the entity, not its rank: among the survivors the index
    // comes from a stable sort on id, so a month that reshuffles the spend
    // order doesn't repaint bars that are still on screen.
    const byId = [...kept].sort((a, b) => a.id.localeCompare(b.id));

    if (rest.length === 0) return byId;
    return [
      ...byId,
      {
        id: "__other",
        name: `Other (${rest.length})`,
        points: months.map((_, i) => rest.reduce((sum, s) => sum + (s.points[i] ?? 0), 0)),
      },
    ];
  }, [series, months]);

  const max = Math.max(1, ...plotted.flatMap((s) => s.points));
  const gridValues = niceTicks(max);
  const yMax = gridValues[gridValues.length - 1]!;

  const y = (v: number) => PAD.top + PLOT_H - (v / yMax) * PLOT_H;

  // Geometry: each month owns a slot; bars sit inside it with a gutter either
  // side, and a 2px gap between adjacent bars so two segments never fuse into
  // one shape.
  const slot = months.length > 0 ? PLOT_W / months.length : PLOT_W;
  const slotPad = Math.min(10, slot * 0.12);
  const bandWidth = slot - slotPad * 2;
  const gap = 2;
  const barWidth = Math.max(
    2,
    (bandWidth - gap * (plotted.length - 1)) / Math.max(1, plotted.length),
  );
  const slotX = (i: number) => PAD.left + i * slot;
  const barX = (i: number, s: number) => slotX(i) + slotPad + s * (barWidth + gap);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || months.length === 0) return;
    // Map the pointer through the rendered box into viewBox units — the SVG
    // scales to its container, so clientX alone is meaningless here.
    const vx = ((e.clientX - rect.left) / rect.width) * W;
    const idx = Math.floor((vx - PAD.left) / slot);
    setHover(idx >= 0 && idx < months.length ? idx : null);
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
          aria-label="Monthly spend, grouped bars"
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

          {/* Hovered month band, behind the bars so it never tints them. */}
          {hover !== null && (
            <rect
              x={slotX(hover)}
              y={PAD.top}
              width={slot}
              height={PLOT_H}
              className="fill-muted/40"
            />
          )}

          {/* Bars */}
          {plotted.map((s, si) => (
            <g key={s.id}>
              {s.points.map((v, i) => {
                const h = Math.max(0, PAD.top + PLOT_H - y(v));
                return (
                  <rect
                    key={months[i] ?? i}
                    x={barX(i, si)}
                    y={y(v)}
                    width={barWidth}
                    height={h}
                    // Rounded data-end only, anchored to the baseline — a fully
                    // rounded bar would misread its own value at both ends.
                    rx={Math.min(3, barWidth / 2)}
                    fill={SERIES_COLORS[si]}
                  />
                );
              })}
            </g>
          ))}

          {/* Month labels */}
          {months.map((m, i) => (
            <text
              key={m}
              x={slotX(i) + slot / 2}
              y={H - 10}
              textAnchor="middle"
              className="fill-muted-foreground font-mono text-[11px]"
            >
              {monthLabel(m)}
            </text>
          ))}
        </svg>

        {/* Tooltip */}
        {hover !== null && (
          <div
            className="border-line-soft bg-panel-elevated pointer-events-none absolute top-2 rounded-lg border px-3 py-2 shadow-lg"
            style={{
              left: `${((slotX(hover) + slot / 2) / W) * 100}%`,
              transform: hover > months.length / 2 ? "translateX(-105%)" : "translateX(5%)",
            }}
          >
            <p className="font-mono text-[10px] tracking-wider uppercase">{months[hover]}</p>
            <ul className="mt-1 space-y-0.5">
              {plotted.map((s, idx) => (
                <li key={s.id} className="flex items-center gap-2 font-mono text-[11px]">
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

      {/* Legend — always present for ≥ 2 series, so identity is never colour
          alone. Bars carry no direct labels: a number on every bar in a
          6×3 grid is 18 numbers, which is noise, not information. */}
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {plotted.map((s, idx) => (
          <li key={s.id} className="flex items-center gap-1.5">
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: SERIES_COLORS[idx] }}
              aria-hidden
            />
            <span className="text-muted-foreground font-mono text-[11px]">{s.name}</span>
          </li>
        ))}
      </ul>

      {/* The same numbers as a table — the non-visual route to this chart. */}
      <details className="group">
        <summary className="text-muted-foreground hover:text-foreground cursor-pointer font-mono text-[11px]">
          View as table
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[28rem] text-left font-mono text-[11px]">
            <thead>
              <tr className="text-muted-foreground border-line-soft border-b">
                <th scope="col" className="py-1.5 pr-3 font-medium">
                  Series
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
                <tr key={s.id} className="border-line-soft border-b last:border-b-0">
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

/**
 * Spend ranked highest-first as horizontal bars — for "which of these cost the
 * most", where the categories are the subject rather than a second dimension.
 *
 * One colour throughout, deliberately. Identity here is carried by the row
 * label sitting beside each bar, so colour has no work to do; giving each row
 * its own hue would imply a grouping that doesn't exist, and would run into the
 * three-colour brand ceiling for no benefit. That also means this form scales
 * to as many rows as you have, unlike the grouped chart above.
 */
export function SpendRankedBars({
  rows,
  emptyLabel = "No spend recorded yet.",
  className,
}: {
  rows: Array<{ id: string; name: string; value: number; href?: string }>;
  emptyLabel?: string;
  className?: string;
}) {
  const ranked = React.useMemo(() => [...rows].sort((a, b) => b.value - a.value), [rows]);
  const max = Math.max(1, ...ranked.map((r) => r.value));
  const total = ranked.reduce((a, r) => a + r.value, 0);

  if (ranked.length === 0) {
    return (
      <p className={cn("text-muted-foreground py-6 text-center font-mono text-[12px]", className)}>
        {emptyLabel}
      </p>
    );
  }

  return (
    <ul className={cn("space-y-2.5", className)}>
      {ranked.map((r) => {
        const pct = total > 0 ? (r.value / total) * 100 : 0;
        return (
          <li key={r.id} className="space-y-1">
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-[12.5px]">{r.name}</span>
              <span className="text-muted-foreground shrink-0 font-mono text-[11.5px]">
                {usdFull(r.value)}
                <span className="ml-1.5 opacity-60">{pct.toFixed(0)}%</span>
              </span>
            </div>
            <div className="bg-surface-2 h-2 w-full overflow-hidden rounded-full">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(r.value / max) * 100}%`,
                  background: SERIES_COLORS[0],
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
