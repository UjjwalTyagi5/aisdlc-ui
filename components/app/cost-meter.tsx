"use client";

import * as React from "react";
import { TrendingDown, TrendingUp } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Cost } from "@/lib/schemas";

export interface CostMeterProps {
  cost: Cost;
  /** Optional time series for the sparkline — newest last. */
  series?: number[];
  /** Budget cap USD; renders a "x% of cap" bar when provided. */
  budgetUsd?: number | null;
  className?: string;
}

function formatUsd(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(3)}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/**
 * Elevated metric-tile cost display with optional sparkline + budget bar.
 * Binds to the real `Cost` shape `{ usd, inputTokens, outputTokens }`.
 * For the inline pill variant, use `<CostBadge />` from Chunk 2.
 */
export function CostMeter({ cost, series, budgetUsd, className }: CostMeterProps) {
  const total = cost.inputTokens + cost.outputTokens;
  const trend =
    series && series.length >= 2
      ? (series[series.length - 1]! - series[0]!) / Math.max(series[0]!, 0.0001)
      : 0;
  const pctOfCap =
    budgetUsd && budgetUsd > 0
      ? Math.min(100, Math.round((cost.usd / budgetUsd) * 100))
      : null;
  const capTone =
    pctOfCap === null
      ? null
      : pctOfCap >= 100
        ? "destructive"
        : pctOfCap >= 80
          ? "warning"
          : "success";

  return (
    <div
      className={cn(
        "bg-panel-elevated relative overflow-hidden rounded-[var(--radius)] border border-line-soft p-[18px] shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_12px_30px_-12px_oklch(0_0_0_/_0.35)] animate-[rise_0.6s_cubic-bezier(0.2,0.7,0.2,1)_both]",
        className,
      )}
      role="group"
      aria-label="Cost meter"
    >
      {/* Label row */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground text-[11.5px] font-semibold uppercase tracking-[0.08em]">
          Cost
        </span>
        {series && series.length >= 2 && (
          <span
            className={cn(
              "inline-flex items-center gap-1 font-mono text-xs",
              trend >= 0 ? "text-warning" : "text-success",
            )}
            aria-label={`Trend ${trend >= 0 ? "up" : "down"} ${Math.abs(trend * 100).toFixed(0)} percent`}
          >
            {trend >= 0 ? (
              <TrendingUp className="size-3" />
            ) : (
              <TrendingDown className="size-3" />
            )}
            {(Math.abs(trend) * 100).toFixed(0)}%
          </span>
        )}
        {/* Sparkline floated top-right */}
        {series && series.length >= 2 && (
          <div className="absolute right-[14px] top-4 h-[26px] w-16 opacity-80">
            <Sparkline values={series} className="h-full w-full" brand />
          </div>
        )}
      </div>

      {/* Primary value: usd — font-display for impact */}
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="font-display text-[30px] font-bold leading-none tracking-tight">
          {formatUsd(cost.usd)}
        </span>
      </div>

      {/* Secondary: total tokens + in/out breakdown */}
      <div className="mt-2 font-mono text-[11.5px] text-muted-foreground">
        <span>{formatTokens(total)} tok</span>
        <span className="mx-1.5 opacity-40">·</span>
        <span>in: {formatTokens(cost.inputTokens)}</span>
        <span className="mx-1.5 opacity-40">·</span>
        <span>out: {formatTokens(cost.outputTokens)}</span>
      </div>

      {/* Budget bar — only shown when budgetUsd is provided (ED-5: no fake metric) */}
      {pctOfCap !== null && (
        <div className="mt-3 space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">Budget</span>
            <span className="font-mono">
              {pctOfCap}% of {formatUsd(budgetUsd!)}
            </span>
          </div>
          <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
            <div
              className={cn(
                "h-full transition-all",
                capTone === "destructive" && "bg-destructive",
                capTone === "warning" && "bg-warning",
                capTone === "success" && "bg-success",
              )}
              style={{ width: `${pctOfCap}%` }}
              aria-hidden
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Sparkline({
  values,
  className,
  brand,
}: {
  values: number[];
  className?: string;
  brand?: boolean;
}) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 0.0001);
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * 100;
      const y = 100 - ((v - min) / range) * 100;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg
      className={className}
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
        points={pts}
        className={brand ? "text-brand-bright" : "text-primary"}
      />
    </svg>
  );
}
