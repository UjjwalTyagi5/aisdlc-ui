"use client";

import { Activity, AlertTriangle, Coins, Timer } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatUsd } from "@/components/app/cost-dashboard";
import type { TraceMetrics } from "@/lib/schemas";

export function TraceMetricsStrip({ metrics }: { metrics: TraceMetrics }) {
  const errPct = (metrics.errorRate * 100).toFixed(1);
  const errBad = metrics.errorRate > 0.05;
  const tiles = [
    {
      icon: Activity,
      label: "Traces",
      value: String(metrics.totalTraces),
      tone: "text-info bg-info/12",
    },
    {
      icon: AlertTriangle,
      label: "Error rate",
      value: `${errPct}%`,
      tone: errBad ? "text-destructive bg-destructive/12" : "text-success bg-success/12",
    },
    {
      icon: Timer,
      label: "Latency p50 · p95",
      value: `${(metrics.latencyP50Ms / 1000).toFixed(1)}s · ${(metrics.latencyP95Ms / 1000).toFixed(1)}s`,
      tone: "text-warning bg-warning/15",
    },
    {
      icon: Coins,
      label: "Cost",
      value: formatUsd(metrics.totalCostUsd),
      tone: "text-brand-bright bg-brand-bright/12",
    },
  ];
  return (
    <div className="border-line-soft bg-panel-elevated grid grid-cols-2 gap-px overflow-hidden rounded-2xl border lg:grid-cols-4">
      {tiles.map((t, i) => (
        <div
          key={t.label}
          className={cn("flex items-center gap-3.5 px-5 py-4", i > 0 && "lg:border-line-soft lg:border-l")}
        >
          <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl", t.tone)}>
            <t.icon className="size-4.5" aria-hidden />
          </span>
          <div className="flex min-w-0 flex-col">
            <span className="font-display text-xl leading-none font-bold tabular-nums">
              {t.value}
            </span>
            <span className="text-muted-foreground mt-1 text-[12px]">{t.label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
