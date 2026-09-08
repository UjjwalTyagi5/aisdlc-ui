"use client";

/**
 * What this run built on.
 *
 * THE QUESTION ASKED AFTER SOMETHING GOES WRONG, and before artifact versions existed
 * it had no answer at all — every agent read the latest non-null payload, so nothing
 * recorded that a particular run read a particular thing. Not a stale answer: none.
 *
 * THREE THINGS THIS SCREEN HAS TO SAY, and two of them are easy to leave out:
 *
 *   what was read     stage and version, with the CONTENT HASH. "design v2" is a claim
 *                     about a row; the hash is checkable against the audit entry
 *                     written when that version was signed.
 *   how it was allowed published, or an owner-granted EXCEPTION. An exception rendered
 *                     identically to routine approved work is how a reviewer learns
 *                     the column means nothing.
 *   what has changed  the version's status NOW. A run that built on something since
 *                     superseded is exactly what somebody is looking for, and the run
 *                     itself stays correct about what it used.
 *
 * AN EMPTY LIST IS MEANINGFUL, not blank. It means the run consumed nothing through
 * the gate — no upstream, or publication was not enforced when it ran — and saying
 * which is the difference between evidence and an empty box.
 */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, FileClock, KeyRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { getRunEvidence, type RunEvidenceEntry } from "@/lib/api/artifact-versions";
import { qk } from "@/lib/api/query-keys";
import { PHASE_LABEL } from "@/lib/agents";
import type { Phase, ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

function label(stage: string): string {
  const phase = (stage === "code_review" ? "review" : stage) as Phase;
  return PHASE_LABEL[phase] ?? stage;
}

/** How the read was allowed. `granted` is deliberately NOT styled like `published`. */
function allowance(entry: RunEvidenceEntry) {
  return entry.viaGrant
    ? {
        Icon: KeyRound,
        label: "Granted exception",
        cls: "border-amber-600/30 bg-amber-600/10 text-amber-700 dark:text-amber-400",
        title: "Not published — an owner allowed this consumer this version",
      }
    : {
        Icon: CheckCircle2,
        label: "Published",
        cls: "border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400",
        title: `Signed off by ${entry.publishedBy ?? "an owner"}`,
      };
}

export interface RunEvidenceProps {
  projectId: ProjectId;
  runId: string;
  /** Whether the project enforces publication. Without it, "nothing recorded" cannot
   *  be distinguished from "nothing was read", and the empty state would mislead. */
  enforced?: boolean;
  className?: string;
}

export function RunEvidence({
  projectId,
  runId,
  enforced,
  className,
}: RunEvidenceProps) {
  const q = useQuery({
    queryKey: qk.artifactVersions.runEvidence(projectId, runId),
    queryFn: () => getRunEvidence(projectId, runId),
  });

  if (q.isLoading) return <LoadingState label="Loading evidence…" />;
  if (q.isError) {
    return (
      <ErrorState
        title="Could not load what this run built on"
        description={(q.error as Error)?.message}
        onRetry={() => void q.refetch()}
      />
    );
  }

  const rows = q.data?.consumed ?? [];

  if (rows.length === 0) {
    return (
      <EmptyState
        title="Nothing recorded for this run"
        description={
          enforced === false
            ? "Artifact publication is not enforced on this project, so upstream reads are not recorded. Turn it on in Settings to build this trail."
            : "This run did not read any upstream artifacts through the publication gate."
        }
      />
    );
  }

  return (
    <section className={cn("space-y-2", className)}>
      <header>
        <h3 className="text-sm font-medium">Built on</h3>
        <p className="text-muted-foreground text-xs">
          The approved artifacts this run read, and the exact content it read.
        </p>
      </header>

      <ul className="divide-y rounded-md border">
        {rows.map((r, i) => {
          const how = allowance(r);
          return (
            <li
              key={`${r.producingStage}-${r.version}-${i}`}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-xs"
            >
              <span className="font-medium">{label(r.producingStage)}</span>
              <Badge variant="outline" className="font-mono">
                v{r.version}
              </Badge>
              <Badge variant="outline" className={cn("gap-1", how.cls)} title={how.title}>
                <how.Icon className="h-3 w-3" />
                {how.label}
              </Badge>
              {/* Only shown when it has MOVED. Repeating "published" on every settled
                  row would bury the one line that matters. */}
              {r.statusNow === "superseded" && (
                <Badge
                  variant="outline"
                  className="text-muted-foreground gap-1"
                  title="A newer version has been published since this run read it"
                >
                  <FileClock className="h-3 w-3" />
                  superseded since
                </Badge>
              )}
              <span
                className="text-muted-foreground font-mono"
                title={`content hash ${r.contentHash}`}
              >
                {r.contentHash.slice(0, 8)}
              </span>
              <span className="text-muted-foreground ml-auto">
                {r.consumedAt ? new Date(r.consumedAt).toLocaleString() : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
