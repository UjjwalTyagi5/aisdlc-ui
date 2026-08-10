"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Inbox, Info } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ApprovalGateRow } from "@/components/app/approval-gate-row";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { listApprovals } from "@/lib/api/approvals";
import { qk } from "@/lib/api/query-keys";
import type { ApprovalGate, ProjectId } from "@/lib/schemas";

/**
 * Project Approvals / Review — PRD §32.1.
 *
 * NOT a second inbox. The personal queue at /approvals remains the single
 * place a person clears what is waiting on *them*, across every project
 * (§33.2: "A person on four projects has one queue").
 *
 * This screen answers a different question — "what is this project waiting
 * on, and who holds it?" — which is the Project Admin's monitoring view
 * (§15.4: "See approvals pending on it as fallback; monitor the project's
 * queue"). Rows the viewer can action are actionable; the rest are visible
 * but clearly owned by someone else.
 */
export default function ProjectApprovalsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const session = useSession({ required: true });
  const role = effectivePlatformRole(session);
  const queryClient = useQueryClient();

  const gatesQ = useQuery({
    queryKey: qk.approvals.list({ project: id }),
    queryFn: () => listApprovals({}),
  });

  const gates = React.useMemo(
    () => (gatesQ.data ?? []).filter((g) => g.projectId === id),
    [gatesQ.data, id],
  );

  const mine = gates.filter((g) => hasPermission(session, g.requiredPermission));
  const others = gates.filter((g) => !hasPermission(session, g.requiredPermission));

  const handleResolved = (gateId: string) => {
    queryClient.setQueriesData<ApprovalGate[]>(
      { queryKey: qk.approvals.all() },
      (old) => (Array.isArray(old) ? old.filter((g) => g.id !== gateId) : old),
    );
    queryClient.invalidateQueries({ queryKey: qk.approvals.metrics() });
  };

  if (!hasPermission(session, "artifact:view")) {
    return (
      <RestrictedAccess description="Project approvals require access to this project's artifacts." />
    );
  }

  const isFallback = role === "project_admin";

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <div>
        <h2 className="font-display text-lg font-semibold tracking-tight">
          Approvals &amp; review
        </h2>
        <p className="text-muted-foreground mt-1 max-w-2xl text-[13px]">
          What this project is waiting on, and who holds it. Your own queue
          across every project lives on{" "}
          <Link href="/approvals" className="text-brand-bright underline underline-offset-2">
            Approvals
          </Link>
          .
        </p>
      </div>

      {isFallback && (
        <div className="border-line-soft text-muted-foreground flex items-start gap-2 rounded-lg border border-dashed px-4 py-3 text-[12.5px]">
          <Info className="mt-px size-4 shrink-0" aria-hidden />
          <p>
            You are the fallback approver on every agent for this project. Use
            it only when the owning role is unavailable — a fallback approval is
            audited as a fallback, never as the owner&apos;s decision. It cannot
            override the mandatory security or release sign-offs.
          </p>
        </div>
      )}

      {gatesQ.isError ? (
        <ErrorState
          title="Couldn't load approvals"
          description={
            gatesQ.error instanceof Error ? gatesQ.error.message : "Unknown error."
          }
          onRetry={() => gatesQ.refetch()}
        />
      ) : gatesQ.isLoading ? (
        <LoadingState variant="list" rows={3} />
      ) : gates.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="Nothing pending on this project"
          description="No agent is paused at a gate and no clarification is outstanding. Consequential actions and sign-offs will appear here the moment they fire."
        />
      ) : (
        <div className="space-y-6">
          {mine.length > 0 && (
            <section className="space-y-3">
              <h3 className="text-muted-foreground font-mono text-[10.5px] tracking-widest uppercase">
                Waiting on you · {mine.length}
              </h3>
              <ul className="space-y-5">
                {mine.map((gate) => (
                  <ApprovalGateRow
                    key={gate.id}
                    gate={gate}
                    onResolved={handleResolved}
                  />
                ))}
              </ul>
            </section>
          )}

          {others.length > 0 && (
            <section className="space-y-3">
              <h3 className="text-muted-foreground font-mono text-[10.5px] tracking-widest uppercase">
                Waiting on someone else · {others.length}
              </h3>
              <ul className="border-line-soft bg-panel-elevated divide-line-soft divide-y overflow-hidden rounded-xl border">
                {others.map((gate) => (
                  <li
                    key={gate.id}
                    className="flex flex-wrap items-center gap-3 px-4 py-3"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium">
                        {gate.title}
                      </span>
                      <span className="text-muted-foreground mt-0.5 block text-[11.5px]">
                        {gate.summary}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="text-muted-foreground block font-mono text-[10px] tracking-widest uppercase">
                        Routed to
                      </span>
                      <span className="block text-[12.5px] font-medium">
                        {gate.waitingForRole}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="text-muted-foreground text-[12px]">
                Approvals route sideways to the agent&apos;s owning role — never
                up to a governance tier, and never to whoever happens to be
                around. No one approves their own request.
              </p>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
