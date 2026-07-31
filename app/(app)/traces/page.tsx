"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { LoadingState } from "@/components/ui/loading-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ActivityTabs } from "@/components/app/activity-tabs";
import { TraceMetricsStrip } from "@/components/app/trace-metrics-strip";
import { TracesExplorer } from "@/components/app/traces-explorer";
import { getTraceMetrics, type TraceFilters } from "@/lib/api/traces";
import { qk } from "@/lib/api/query-keys";

const WINDOW_OPTIONS = [7, 30, 90] as const;
const ALL = "all";

export default function TracesPage() {
  const session = useSession({ required: true });
  const [windowDays, setWindowDays] = React.useState<number>(30);
  // Filter state lives here (not in TracesExplorer) so the summary cards and the
  // table share one source of truth — picking a project rescopes both together.
  const [agent, setAgent] = React.useState<string>(ALL);
  const [project, setProject] = React.useState<string>(ALL);
  const [status, setStatus] = React.useState<string>(ALL);

  const filters: TraceFilters = {
    agent: agent === ALL ? undefined : agent,
    project: project === ALL ? undefined : project,
    status: status === ALL ? undefined : status,
  };

  const metricsQ = useQuery({
    queryKey: qk.traces.metrics(windowDays, filters),
    queryFn: () => getTraceMetrics(windowDays, filters),
  });

  if (!hasPermission(session, "trace:view")) {
    return (
      <RestrictedAccess description="Traces require the trace:view permission. Ask your admin for access." />
    );
  }

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <header
        className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div>
          <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
            <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
            Observe
          </div>
          <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
            Activity
          </h1>
          <p className="text-muted-foreground mt-2 max-w-[560px] text-[14px]">
            Per-agent execution traces — spans, latency, errors, and quality scores. The same
            Langfuse-sourced data as Cost, viewed for debugging.
          </p>
        </div>

        <Select value={String(windowDays)} onValueChange={(v) => setWindowDays(Number(v))}>
          <SelectTrigger className="border-line-soft h-9 w-36" aria-label="Select window">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {WINDOW_OPTIONS.map((d) => (
              <SelectItem key={d} value={String(d)}>
                Last {d} days
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </header>

      <ActivityTabs />

      {metricsQ.isLoading ? (
        <LoadingState variant="list" rows={1} />
      ) : metricsQ.data ? (
        <TraceMetricsStrip metrics={metricsQ.data} />
      ) : null}

      <TracesExplorer
        agent={agent}
        project={project}
        status={status}
        onAgentChange={setAgent}
        onProjectChange={setProject}
        onStatusChange={setStatus}
      />
    </div>
  );
}
