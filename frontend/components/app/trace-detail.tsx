"use client";

import { ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { formatUsd, formatTokens } from "@/components/app/cost-dashboard";
import { TraceSpanTree } from "@/components/app/trace-span-tree";
import type { Trace } from "@/lib/schemas";

function fmtLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

export function TraceDetail({ trace }: { trace: Trace }) {
  const meta: [string, string][] = [
    ["Agent", trace.agentType],
    ["Project", trace.projectName],
    ["Environment", trace.environment],
    ["Release", trace.release ?? "—"],
    ["Latency", fmtLatency(trace.latencyMs)],
    ["Cost", `${formatUsd(trace.cost.usd)} · ${formatTokens(trace.cost.inputTokens + trace.cost.outputTokens)} tok`],
    ["Spans", String(trace.spanCount)],
    ["Started", new Date(trace.startedAt).toLocaleString()],
  ];

  return (
    <div className="space-y-6">
      {/* Header card */}
      <div className="border-line-soft bg-panel-elevated space-y-4 rounded-2xl border p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <h2 className="font-display truncate text-xl font-bold tracking-[-0.01em]">
                {trace.name}
              </h2>
              <StatusBadge status={trace.status} />
            </div>
            <p className="text-muted-foreground mt-0.5 font-mono text-[12px]">{trace.id}</p>
          </div>
          {trace.langfuseUrl && (
            <Button variant="outline" size="sm" asChild className="border-line-soft">
              <a href={trace.langfuseUrl} target="_blank" rel="noreferrer">
                <ExternalLink className="size-4" aria-hidden />
                View in Langfuse
              </a>
            </Button>
          )}
        </div>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          {meta.map(([k, v]) => (
            <div key={k} className="flex flex-col gap-0.5">
              <dt className="text-muted-foreground font-mono text-[10.5px] uppercase tracking-[0.12em]">
                {k}
              </dt>
              <dd className="truncate text-[13px] font-medium">{v}</dd>
            </div>
          ))}
        </dl>

        {trace.scores.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <span className="text-muted-foreground font-mono text-[10.5px] uppercase tracking-[0.12em]">
              Scores
            </span>
            {trace.scores.map((s) => (
              <span
                key={s.name}
                className="border-line-soft bg-surface-1 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[11px]"
                title={s.comment ?? undefined}
              >
                <span className="text-muted-foreground">{s.name}</span>
                <span className="font-semibold tabular-nums">{s.value.toFixed(2)}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Span waterfall */}
      <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-2xl border">
        <div className="border-line-soft flex items-center gap-2 border-b px-5 py-3.5">
          <span className="font-display text-[13.5px] font-bold tracking-[-0.01em]">Spans</span>
          <span className="text-muted-foreground font-mono text-[10.5px]">
            {trace.spans.length} spans · {fmtLatency(trace.latencyMs)} total
          </span>
        </div>
        <div className="p-3">
          <TraceSpanTree spans={trace.spans} totalMs={trace.latencyMs} />
        </div>
      </div>
    </div>
  );
}
