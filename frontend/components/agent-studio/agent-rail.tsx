"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { AgentProfileSummaryEntry } from "@/lib/schemas/agent-profiles";

import { agentLabel } from "./agents";

export interface AgentRailProps {
  agents: AgentProfileSummaryEntry[];
  selectedId: string;
  onSelect: (agentId: string) => void;
}

/** Left rail: the 8 agents in pipeline order, each with a state hint. */
export function AgentRail({ agents, selectedId, onSelect }: AgentRailProps) {
  return (
    <nav aria-label="Agents" className="flex flex-col gap-1">
      {agents.map((agent) => {
        const selected = agent.agent_id === selectedId;
        const hasActive = agent.active_version !== null;
        // Older superseded versions are history, not pending work — flag a draft
        // only when a version NEWER than the active one is waiting to be published.
        const hasDraft =
          agent.latest_version !== null &&
          agent.latest_version > (agent.active_version ?? 0) &&
          agent.draft_count > 0;
        return (
          <button
            key={agent.agent_id}
            type="button"
            onClick={() => onSelect(agent.agent_id)}
            aria-current={selected ? "true" : undefined}
            className={cn(
              "focus-visible:ring-ring group flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
              selected
                ? "bg-brand-bright/10 text-foreground ring-brand-bright/30 ring-1"
                : "hover:bg-accent/50 text-foreground/80",
            )}
          >
            <span className="flex-1 truncate text-sm font-medium">
              {agentLabel(agent.agent_id)}
            </span>
            {hasActive ? (
              <Badge
                variant={selected ? "info" : "outline"}
                className="shrink-0 font-mono text-[10px]"
              >
                v{agent.active_version}
              </Badge>
            ) : hasDraft ? null : (
              <span className="text-muted-foreground/70 shrink-0 text-[11px]">
                default
              </span>
            )}
            {hasDraft && (
              <span
                className="flex shrink-0 items-center gap-1"
                title="A draft newer than the active version is unpublished"
              >
                <span
                  className="bg-warning size-1.5 rounded-full"
                  aria-hidden
                />
                <span className="text-muted-foreground text-[10px]">draft</span>
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}

export function AgentRailSkeleton() {
  return (
    <div className="flex flex-col gap-1" aria-hidden>
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex items-center gap-2.5 px-3 py-2.5">
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-4 w-8" />
        </div>
      ))}
    </div>
  );
}
