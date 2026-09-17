"use client";

/**
 * Send a document for approval — who may, and the act itself, in one place.
 *
 * ONE HOOK RATHER THAN A COPY PER SURFACE. The Documents panel has a "Raise for approval"
 * button, and the Code Review report carries the same action beside its status. Two
 * copies would drift on exactly the parts that matter: who sees the button, and what is
 * refreshed afterwards so the document stops saying "Draft".
 *
 * WHO MAY (the route's rule, `may_raise_for_approval`): `run:create`, OR use of the agent
 * the document belongs to. A project-wide document belongs to no agent. The agent reach
 * is fetched only when `run:create` does not already settle it — the same query the
 * agent pages draw their padlocks from.
 *
 * THE GATE HERE IS UX, NOT SECURITY — the route decides; see shared/routers/artifacts.py.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useSession } from "@/hooks/use-session";
import { submitArtifact } from "@/lib/api/artifacts";
import { getMyAgentAccess } from "@/lib/api/capabilities";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import type { Artifact, ProjectId } from "@/lib/schemas";

export interface UseRaiseForApprovalResult {
  /** True when this person may send a document of `stage` (a BACKEND stage name). */
  mayRaise: (stage: string | null | undefined) => boolean;
  raise: (artifact: Artifact) => void;
  /** The document being sent right now, so its control can show progress. */
  raisingId: string | null;
}

export function useRaiseForApproval(projectId: ProjectId): UseRaiseForApprovalResult {
  const session = useSession();
  const queryClient = useQueryClient();
  const canCreateRuns = hasPermission(session, "run:create");

  const accessQ = useQuery({
    queryKey: qk.myAgentAccess.forProject(projectId),
    queryFn: () => getMyAgentAccess(projectId),
    staleTime: 30_000,
    enabled: !canCreateRuns,
  });

  const mutation = useMutation({
    mutationFn: (a: Artifact) => submitArtifact(a.id),
    onSuccess: () => {
      toast.success("Raised for approval");
      // The document's own status, wherever it is shown…
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      // …and the count beside Requests & Approvals, which is now wrong until this lands.
      void queryClient.invalidateQueries({ queryKey: qk.approvals.list({}) });
    },
    onError: (e: Error) => toast.error(e.message || "Couldn't raise it for approval"),
  });

  const mayRaise = (stage: string | null | undefined) => {
    if (canCreateRuns) return true;
    if (!stage) return false; // a project-wide document belongs to no agent
    const phase = stage === "code_review" ? "review" : stage;
    return (accessQ.data?.reach?.[phase] ?? "none") !== "none";
  };

  return {
    mayRaise,
    raise: (a) => mutation.mutate(a),
    raisingId: mutation.isPending ? (mutation.variables?.id ?? null) : null,
  };
}
