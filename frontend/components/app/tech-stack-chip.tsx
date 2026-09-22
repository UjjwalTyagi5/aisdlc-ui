"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Layers } from "lucide-react";

import { getProjectTechStack } from "@/lib/api/tech-stacks";
import { qk } from "@/lib/api/query-keys";
import { SOURCE_LABEL } from "@/lib/schemas/tech-stacks";
import { cn } from "@/lib/utils";

/**
 * The tech stack this project's agents follow, beside the agent that follows it — so nobody
 * runs the Design agent without seeing which stack it will design for. Links to where it is
 * chosen: Agent Studio, the project's Skills tab.
 */
export function TechStackChip({ projectId }: { projectId: string }) {
  const q = useQuery({
    queryKey: qk.techStacks.project(projectId),
    queryFn: () => getProjectTechStack(projectId),
    staleTime: 60_000,
  });
  const base = "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors";
  if (q.isLoading) return null;
  if (q.isError || !q.data) {
    return (
      <span className={cn(base, "text-muted-foreground")} title={q.error instanceof Error ? q.error.message : undefined}>
        Tech stack unavailable
      </span>
    );
  }
  const eff = q.data.effective;
  const label = eff.stack ? `Tech stack: ${eff.stack.name}` : "No tech stack set";
  const title = eff.warning ?? (eff.stack ? SOURCE_LABEL[eff.source] : "Agents recommend a stack. Choose one in Agent Studio.");
  return (
    <Link
      href={`/agent-studio?project=${encodeURIComponent(projectId)}&tab=skills`}
      title={title}
      className={cn(base, "hover:bg-accent", eff.warning && "border-amber-500/50 text-amber-800 dark:text-amber-300")}
    >
      {eff.warning ? <AlertTriangle className="size-3.5" aria-hidden /> : <Layers className="size-3.5" aria-hidden />}
      {label}
    </Link>
  );
}
