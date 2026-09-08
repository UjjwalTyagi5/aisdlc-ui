"use client";

/**
 * Who may read whose artifacts on this project.
 *
 * THE HALF OF THE FEATURE THAT IS NOT ENFORCEMENT. Without this, the first time
 * anyone learns the rules is when an agent refuses to run — and the message it gives
 * names a stage, not a person to go and ask.
 *
 * READ DOWN A COLUMN to answer "what can this agent build on". Read across a row to
 * answer "who is waiting on me". The owner column is the one people actually come
 * here for: it names the role that signs a stage off, which is who a request goes to.
 *
 * FOUR STATES, and the two in the middle are the ones a matrix usually gets wrong:
 *
 *   open           published — ANY consumer may read it. One signature serves them
 *                  all; showing this as per-consumer approval would imply a gate
 *                  that does not exist and is not wanted.
 *   granted        an owner allowed THIS consumer THIS version as an exception.
 *                  Deliberately styled differently from `open` — an exception shown
 *                  as routine is how a reviewer learns the gate means nothing.
 *   needs_request  nothing published, no grant. Not an error; most cells start here.
 *   self           a stage does not consume itself.
 *
 * WHEN THE GATE IS OFF the whole table is academic — agents read the working draft
 * regardless — and that is stated at the top rather than left to be inferred from a
 * screen full of green.
 */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, KeyRound, Lock, Minus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import {
  getConsumptionMatrix, type ConsumptionState,
} from "@/lib/api/artifact-versions";
import { qk } from "@/lib/api/query-keys";
import { PHASE_LABEL } from "@/lib/agents";
import { ROLE_META } from "@/lib/roles";
import type { Phase, ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

/** The backend uses `code_review`; the UI's Phase union says `review`. */
function toPhase(stage: string): Phase {
  return (stage === "code_review" ? "review" : stage) as Phase;
}

function label(stage: string): string {
  return PHASE_LABEL[toPhase(stage)] ?? stage;
}

function roleLabel(role: string | null): string {
  if (!role) return "—";
  return ROLE_META[role as keyof typeof ROLE_META]?.label ?? role;
}

const CELL: Record<ConsumptionState, {
  Icon: typeof CheckCircle2; cls: string; title: string;
}> = {
  open: {
    Icon: CheckCircle2,
    cls: "text-emerald-600 dark:text-emerald-400",
    title: "Published — this agent may read it",
  },
  granted: {
    Icon: KeyRound,
    cls: "text-amber-600 dark:text-amber-400",
    title: "Not published; this agent has been granted an exception",
  },
  needs_request: {
    Icon: Lock,
    cls: "text-muted-foreground/50",
    title: "Nothing published — ask the owner to publish, or request access",
  },
  self: {
    Icon: Minus,
    cls: "text-muted-foreground/25",
    title: "A stage does not consume itself",
  },
};

export function ArtifactAccessMatrix({ projectId }: { projectId: ProjectId }) {
  const q = useQuery({
    queryKey: qk.artifactVersions.matrix(projectId),
    queryFn: () => getConsumptionMatrix(projectId),
  });

  if (q.isLoading) return <LoadingState label="Loading access matrix…" />;
  if (q.isError) {
    return (
      <ErrorState
        title="Could not load the access matrix"
        description={(q.error as Error)?.message}
        onRetry={() => void q.refetch()}
      />
    );
  }

  const data = q.data;
  if (!data || data.stages.length === 0) {
    return <EmptyState title="No agents to show" />;
  }

  const stages = data.stages.map((r) => r.stage);

  return (
    <section className="space-y-3">
      <header className="space-y-1">
        <h3 className="text-sm font-medium">Artifact access</h3>
        <p className="text-muted-foreground text-xs">
          Which agent may build on which. Rows produce, columns consume.
        </p>
        {!data.enforced && (
          // Said first, because otherwise a screen of padlocks reads as "everything
          // is blocked" when in fact nothing is.
          <p className="text-xs text-amber-700 dark:text-amber-400">
            Publication is <strong>not enforced</strong> on this project — agents
            currently read the latest working draft regardless of what this table says.
            Turn it on in Settings to apply these rules.
          </p>
        )}
      </header>

      {/* Wide by nature: nine columns plus two. Scrolls in its own box so the page
          body never scrolls sideways. */}
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[720px] text-xs">
          <thead>
            <tr className="border-b bg-muted/40">
              <th className="p-2 text-left font-medium">Produced by</th>
              <th className="p-2 text-left font-medium">Signed off by</th>
              <th className="p-2 text-left font-medium">Published</th>
              {stages.map((s) => (
                <th
                  key={s}
                  className="p-2 text-center font-medium"
                  title={`Consumed by ${label(s)}`}
                >
                  {label(s)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.stages.map((row) => (
              <tr key={row.stage} className="border-b last:border-0">
                <td className="p-2 font-medium whitespace-nowrap">
                  {label(row.stage)}
                </td>
                <td className="text-muted-foreground p-2 whitespace-nowrap">
                  {roleLabel(row.ownerRole)}
                </td>
                <td className="p-2 whitespace-nowrap">
                  {row.publishedVersion != null ? (
                    <Badge variant="outline" className="font-mono">
                      v{row.publishedVersion}
                    </Badge>
                  ) : (
                    <span className="text-muted-foreground">none</span>
                  )}
                </td>
                {stages.map((consumer) => {
                  const state = row.consumers[consumer] ?? "needs_request";
                  const cell = CELL[state];
                  return (
                    <td key={consumer} className="p-2 text-center">
                      <cell.Icon
                        className={cn("mx-auto h-4 w-4", cell.cls)}
                        aria-label={state}
                      />
                      <span className="sr-only">{cell.title}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {(["open", "granted", "needs_request", "self"] as ConsumptionState[]).map(
          (s) => {
            const cell = CELL[s];
            return (
              <li key={s} className="flex items-center gap-1.5">
                <cell.Icon className={cn("h-3.5 w-3.5", cell.cls)} aria-hidden />
                {cell.title}
              </li>
            );
          },
        )}
      </ul>
    </section>
  );
}
