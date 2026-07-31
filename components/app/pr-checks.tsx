"use client";

import * as React from "react";
import {
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  Loader2,
  MinusCircle,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { PrCheck, PrCheckStatus } from "@/lib/schemas";

const ICON: Record<PrCheckStatus, React.ComponentType<{ className?: string }>> = {
  pending: CircleDashed,
  running: Loader2,
  passing: CheckCircle2,
  failing: XCircle,
  neutral: MinusCircle,
};

const TONE: Record<PrCheckStatus, string> = {
  pending: "text-muted-foreground",
  running: "text-info [&>svg]:animate-spin",
  passing: "text-success",
  failing: "text-destructive",
  neutral: "text-muted-foreground",
};

export interface PrChecksProps {
  checks: readonly PrCheck[];
  className?: string;
}

export function PrChecks({ checks, className }: PrChecksProps) {
  const failing = checks.some((c) => c.status === "failing");
  const running = checks.some((c) => c.status === "running" || c.status === "pending");
  const summary = failing
    ? "Some checks are failing."
    : running
      ? "Checks in progress."
      : "All checks passing.";
  const rollup: PrCheckStatus = failing ? "failing" : running ? "running" : "passing";

  return (
    <section className={cn("space-y-2", className)} aria-label="CI checks">
      <div className="flex items-baseline justify-between">
        <h3 className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
          Checks
        </h3>
        <span className={cn("text-xs", TONE[rollup])}>{summary}</span>
      </div>
      <ul className="divide-y rounded-md border">
        {checks.map((c) => {
          const Icon = ICON[c.status];
          return (
            <li key={c.name} className="flex items-center gap-3 px-3 py-2 text-sm">
              <span className={cn("flex items-center", TONE[c.status])}>
                <Icon className="size-4" aria-hidden />
              </span>
              <span className="flex-1 truncate font-mono text-xs">{c.name}</span>
              {c.summary && (
                <span className="text-muted-foreground hidden truncate text-xs sm:inline">
                  {c.summary}
                </span>
              )}
              {c.durationMs !== undefined && (
                <span className="text-muted-foreground font-mono text-[10px]">
                  {Math.round(c.durationMs / 1000)}s
                </span>
              )}
              {c.url && (
                <a
                  href={c.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-muted-foreground hover:text-foreground"
                  aria-label={`Open details for ${c.name}`}
                >
                  <ExternalLink className="size-3.5" aria-hidden />
                </a>
              )}
            </li>
          );
        })}
        {checks.length === 0 && (
          <li className="text-muted-foreground p-3 text-center text-sm">
            No checks reported yet.
          </li>
        )}
      </ul>
    </section>
  );
}
