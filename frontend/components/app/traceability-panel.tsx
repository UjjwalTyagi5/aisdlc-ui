"use client";

import * as React from "react";
import { ArrowUpRight, ExternalLink, GitPullRequest, Network } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Artifact } from "@/lib/schemas";

export interface TraceabilityPanelProps {
  /** From a `StoryBody.traceability` record — missing links render as empty slots. */
  trace: {
    jiraIssueKey?: string;
    /** Browsable link to the item on its board, resolved at ingest. */
    boardUrl?: string;
    designArtifactId?: Artifact["id"];
    prUrl?: string;
  };
  /** When we know the project id, design links are scoped correctly. */
  projectId?: string;
  className?: string;
}

export function TraceabilityPanel({
  trace,
  projectId,
  className,
}: TraceabilityPanelProps) {
  const rows: Array<{
    key: string;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    value?: string;
    href?: string;
    external?: boolean;
  }> = [
    {
      key: "jira",
      label: "Work item",
      icon: ArrowUpRight,
      value: trace.jiraIssueKey,
      // The URL is resolved at INGEST, where the connector is in scope. It used to be
      // built here from a `jiraBaseUrl` prop that no caller ever passed, so the key
      // rendered as inert text on every story.
      href: trace.boardUrl,
      external: true,
    },
    {
      key: "design",
      label: "Design",
      icon: Network,
      value: trace.designArtifactId,
      href:
        trace.designArtifactId && projectId
          ? `/projects/${projectId}/design?artifact=${encodeURIComponent(trace.designArtifactId)}`
          : undefined,
    },
    {
      key: "pr",
      label: "Pull request",
      icon: GitPullRequest,
      value: trace.prUrl,
      href: trace.prUrl,
      external: true,
    },
  ];

  // ONLY THE LINKS THAT EXIST. `designArtifactId` and `prUrl` are written by nothing
  // in the product — their sole writer is scripts/seed_e2e_fixtures.py — so every story
  // showed three rows reading "unlinked" forever, which advertises a feature rather
  // than reporting a state. Filtering rather than deleting them means each reappears on
  // its own the moment something populates it.
  const linked = rows.filter((r) => r.value);
  if (linked.length === 0) return null;

  return (
    <section className={cn("space-y-2", className)} aria-label="Traceability">
      {/* Elevated font-display eyebrow header — matches northstar mono label treatment */}
      <h3 className="font-display text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        Traceability
      </h3>

      <ul className="divide-y divide-line-soft rounded-md border border-line-soft bg-panel-elevated">
        {linked.map((r) => (
          <li key={r.key} className="flex items-center gap-3 px-3 py-2.5">
            <r.icon
              className={cn(
                "size-4 shrink-0",
                r.value ? "text-muted-foreground" : "text-muted-foreground/40",
              )}
              aria-hidden
            />
            <span className="w-24 shrink-0 font-mono text-[11px] font-medium text-muted-foreground">
              {r.label}
            </span>
            {r.value ? (
              r.href ? (
                <a
                  href={r.href}
                  target={r.external ? "_blank" : undefined}
                  rel={r.external ? "noreferrer noopener" : undefined}
                  className="hover:text-foreground inline-flex min-w-0 items-center gap-1 truncate font-mono text-[11px] text-foreground/80 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
                >
                  <span className="truncate">{r.value}</span>
                  {r.external && <ExternalLink className="size-3 shrink-0" aria-hidden />}
                </a>
              ) : (
                <span className="font-mono text-[11px] text-foreground/80">{r.value}</span>
              )
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
