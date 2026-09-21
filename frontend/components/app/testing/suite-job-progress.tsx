"use client";

import * as React from "react";
import { CheckCircle2, CircleAlert, Loader2 } from "lucide-react";

import { Callout, Pill } from "@/components/app/report-primitives";
import { isActive, type SuiteJob } from "@/lib/api/testing-suites";
import { cn } from "@/lib/utils";

function elapsed(job: SuiteJob): string {
  const start = Date.parse(job.started_at ?? job.created_at);
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  const s = Math.max(0, Math.round((end - start) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

/** What a suite job is doing now, or what it did — its own words, in order. */
export function SuiteJobProgress({ job, title }: { job: SuiteJob; title: string }) {
  const active = isActive(job);
  const [, tick] = React.useState(0);
  React.useEffect(() => {
    if (!active) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);

  const tone = job.status === "succeeded" ? "success" : active ? "info" : "danger";
  const label = { queued: "Queued", running: "Running", succeeded: "Done", failed: "Failed", interrupted: "Interrupted" }[job.status];
  return (
    <div className="bg-surface-1 space-y-2 rounded-lg border p-3" aria-live="polite">
      <div className="flex items-center gap-2 text-sm">
        {active ? <Loader2 className="text-brand-bright size-4 animate-spin" aria-hidden />
          : job.status === "succeeded" ? <CheckCircle2 className="text-success size-4" aria-hidden />
            : <CircleAlert className="text-destructive size-4" aria-hidden />}
        <span className="font-medium">{title}</span>
        <Pill tone={tone}>{label}</Pill>
        <span className="text-muted-foreground ml-auto text-xs tabular-nums">{elapsed(job)}</span>
      </div>
      {job.progress.length > 0 && (
        <ol className="text-muted-foreground max-h-40 space-y-0.5 overflow-auto font-mono text-[11px]">
          {job.progress.slice(-12).map((p, i, arr) => (
            <li key={`${p.at}-${i}`} className={cn(i === arr.length - 1 && active && "text-foreground", p.level === "error" && "text-destructive")}>
              {p.message}
            </li>
          ))}
        </ol>
      )}
      {(job.status === "failed" || job.status === "interrupted") && job.error && (
        <Callout tone="danger" title="It did not complete">{job.error}</Callout>
      )}
    </div>
  );
}
