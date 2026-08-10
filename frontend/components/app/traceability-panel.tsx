"use client";

import * as React from "react";
import { ArrowUpRight, ExternalLink, GitPullRequest, Network, TestTubes } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Artifact } from "@/lib/schemas";

export interface TraceabilityPanelProps {
  /** From a `StoryBody.traceability` record — missing links render as empty slots. */
  trace: {
    jiraIssueKey?: string;
    designArtifactId?: Artifact["id"];
    prUrl?: string;
  };
  /** Workspace base URL for Jira — optional. */
  jiraBaseUrl?: string;
  /** When we know the project id, design links are scoped correctly. */
  projectId?: string;
  className?: string;
}

export function TraceabilityPanel({
  trace,
  jiraBaseUrl,
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
      href:
        trace.jiraIssueKey && jiraBaseUrl
          ? `${jiraBaseUrl.replace(/\/$/, "")}/browse/${encodeURIComponent(trace.jiraIssueKey)}`
          : undefined,
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
    {
      key: "tests",
      label: "Tests",
      icon: TestTubes,
      // Not modelled yet — reserved for Chunk 12.
      value: undefined,
    },
  ];

  return (
    <section className={cn("space-y-2", className)} aria-label="Traceability">
      {/* Elevated font-display eyebrow header — matches northstar mono label treatment */}
      <h3 className="font-display text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        Traceability
      </h3>

      <ul className="divide-y divide-line-soft rounded-md border border-line-soft bg-panel-elevated">
        {rows.map((r) => (
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
            ) : (
              <span className="font-mono text-[11px] italic text-muted-foreground/50">
                unlinked
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
