"use client";

import * as React from "react";
import { AlertTriangle, Brain, Code2, Database, Dot, Workflow } from "lucide-react";

import { cn } from "@/lib/utils";
import { CostBadge } from "@/components/ui/cost-badge";
import type { Span, SpanType } from "@/lib/schemas";

const TYPE_ICON: Record<SpanType, React.ComponentType<{ className?: string }>> = {
  generation: Brain,
  tool: Code2,
  retrieval: Database,
  span: Workflow,
  event: Dot,
};

/** Depth of a span in the tree, walking parent links (with a cycle guard). */
function depthOf(span: Span, byId: Map<string, Span>): number {
  let d = 0;
  let cur: Span | undefined = span;
  while (cur?.parentId) {
    cur = byId.get(cur.parentId);
    d += 1;
    if (d > 32) break;
  }
  return d;
}

export function TraceSpanTree({
  spans,
  totalMs,
}: {
  spans: readonly Span[];
  totalMs: number;
}) {
  const byId = React.useMemo(
    () => new Map(spans.map((s) => [s.id as string, s] as const)),
    [spans],
  );
  const denom = Math.max(totalMs, 1);

  if (spans.length === 0) {
    return (
      <div className="text-muted-foreground py-8 text-center font-mono text-[12px]">
        No spans recorded.
      </div>
    );
  }

  return (
    <ol className="divide-line-soft divide-y" aria-label="Trace span waterfall">
      {spans.map((s) => {
        const Icon = TYPE_ICON[s.type];
        const depth = depthOf(s, byId);
        const leftPct = Math.min((s.startOffsetMs / denom) * 100, 99);
        const widthPct = Math.max((s.latencyMs / denom) * 100, 1.5);
        const isError = s.level === "error";
        return (
          <li key={s.id} className="flex items-center gap-3 px-2 py-2">
            <div
              className="flex min-w-0 items-center gap-2"
              style={{ paddingLeft: depth * 14, width: "34%" }}
            >
              <Icon
                className={cn(
                  "size-3.5 shrink-0",
                  isError ? "text-destructive" : "text-muted-foreground",
                )}
                aria-hidden
              />
              <span className="truncate font-mono text-[12px]" title={s.name}>
                {s.name}
              </span>
              {isError && <AlertTriangle className="text-destructive size-3 shrink-0" aria-hidden />}
            </div>

            <div className="bg-surface-2 relative h-4 flex-1 overflow-hidden rounded">
              <div
                className={cn(
                  "absolute top-0 h-4 rounded",
                  isError ? "bg-destructive/70" : "bg-brand-bright/70",
                )}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                title={`${s.latencyMs} ms`}
              />
            </div>

            <span className="text-muted-foreground w-16 shrink-0 text-right font-mono text-[11px] tabular-nums">
              {s.latencyMs} ms
            </span>
            {s.cost ? (
              <CostBadge usd={s.cost.usd} tokens={s.cost.inputTokens + s.cost.outputTokens} />
            ) : (
              <span className="w-14 shrink-0" />
            )}
          </li>
        );
      })}
    </ol>
  );
}
