"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { getRunEval } from "@/lib/api/eval";
import { qk } from "@/lib/api/query-keys";
import type { RunId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

/** Map a 0–1 quality score to a color band + badge variant.
 *
 *  ≥ 0.80  → green  (success)
 *  ≥ 0.50  → amber  (warning)
 *  < 0.50  → red    (destructive)
 */
export function scoreColorBand(score: number): "success" | "warning" | "destructive" {
  if (score >= 0.8) return "success";
  if (score >= 0.5) return "warning";
  return "destructive";
}

/** Format a 0–1 score as a percentage string, e.g. 0.92 → "92%". */
export function formatScorePercent(score: number): string {
  return `${Math.round(score * 100)}%`;
}

export interface EvalIndicatorProps {
  runId: string;
}

/**
 * EvalIndicator — compact quality badge for a run.
 *
 * Self-fetches EvalRecord data via the BFF /runs/{runId}/eval endpoint
 * (requires artifact:view; the parent drawer gates rendering on hasPermission).
 * Uses only pre-installed shadcn primitives (Badge, Skeleton, Tooltip — T-9.3-12).
 *
 * States:
 *   loading  — three skeleton blocks
 *   empty    — muted "No quality signal yet" label
 *   error    — silent (does not surface errors to avoid auth confusion; parent has the gate)
 *   loaded   — colored percentage badge + signals tooltip
 */
export function EvalIndicator({ runId }: EvalIndicatorProps) {
  const { data, isLoading, isError } = useQuery({
    // Cast runId to RunId branded type for the query key (safe: same runtime string).
    queryKey: qk.runs.eval(runId as RunId),
    queryFn: () => getRunEval(runId),
    staleTime: 60_000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-1.5" aria-label="Loading quality signal">
        <Skeleton className="h-[18px] w-[60px] rounded" />
      </div>
    );
  }

  // Silently suppress errors — the backend will 403 when lacking artifact:view;
  // the parent drawer already hides this component in that case (T-9.3-11).
  if (isError || !data || data.length === 0) {
    return (
      <span className="font-mono text-[11px] text-muted-foreground">
        No quality signal yet
      </span>
    );
  }

  // Use the latest record (first in the newest-first list).
  const latest = data[0];
  if (!latest) {
    return (
      <span className="font-mono text-[11px] text-muted-foreground">
        No quality signal yet
      </span>
    );
  }
  const { score, signals } = latest;

  if (score === null || score === undefined) {
    return (
      <span className="font-mono text-[11px] text-muted-foreground">
        No quality signal yet
      </span>
    );
  }

  const band = scoreColorBand(score);
  const label = formatScorePercent(score);
  const signalEntries = signals ? Object.entries(signals) : [];

  const badge = (
    <Badge
      variant={band}
      className={cn(
        "cursor-default font-mono text-[11px] tabular-nums",
        // Slightly tighter padding than the default px-2.5 to match the compact metric strip.
        "px-1.5 py-0",
      )}
      aria-label={`Quality score: ${label}`}
    >
      {label}
    </Badge>
  );

  if (signalEntries.length === 0) {
    return badge;
  }

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent
          side="bottom"
          className="max-w-[200px] space-y-0.5 font-mono text-[10px]"
        >
          <p className="font-semibold uppercase tracking-wide text-[9px] opacity-70 mb-1">
            Quality signals
          </p>
          {signalEntries.map(([key, value]) => (
            <p key={key} className="flex justify-between gap-3">
              <span className="opacity-70">{key}</span>
              <span>{typeof value === "number" ? value.toFixed(3) : String(value)}</span>
            </p>
          ))}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
