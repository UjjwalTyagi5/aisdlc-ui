"use client";

/**
 * Approve or reject a generated document on behalf of whoever runs the project.
 *
 * ONE HOOK RATHER THAN A COPY PER PAGE, for the same reason as `useDeleteArtifact`:
 * DocumentCard renders on the Design page, the Requirements page and StageWorkbench,
 * and a decision that behaves differently on one of them is the kind of inconsistency
 * discovered by approving the wrong thing.
 *
 * THE GATE HERE IS UX, NOT SECURITY. `hasPermission` decides whether the buttons render
 * at all; the backend requires `approve` AND that the caller administers this specific
 * project. Someone who tampers with client state to reveal the controls still gets a
 * 403 — or a 404, since the project check refuses without confirming the project exists.
 */

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { approveArtifact, rejectArtifact } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import { useSession } from "@/hooks/use-session";
import type { Artifact, ArtifactId, ProjectId } from "@/lib/schemas";

export interface UseArtifactApprovalResult {
  /** True when this user may decide. Callers pass the handlers only if so — the card
   *  renders no controls without them. */
  canDecide: boolean;
  approve: (id: ArtifactId) => void;
  reject: (id: ArtifactId) => void;
  /** The artifact currently being decided, so its card can show a pending state. */
  decidingId: string | null;
}

export function useArtifactApproval(projectId: ProjectId): UseArtifactApprovalResult {
  const session = useSession();
  const queryClient = useQueryClient();
  const [decidingId, setDecidingId] = React.useState<string | null>(null);

  const canDecide = hasPermission(session, "approve");

  const settle = (artifact: Artifact | undefined, message: string) => {
    toast.success(message);
    // Both keys: the list being looked at, and any detail query holding the artifact
    // whose status and downloadUrl have just changed.
    void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
    if (artifact) {
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.detail(artifact.id) });
    }
    setDecidingId(null);
  };

  const fail = (error: unknown) => {
    // The backend distinguishes "the file could not be moved, so the artifact was left
    // pending" (502) from a permission or state conflict (403/404/409), and that
    // difference decides whether retrying is worth it.
    const detail =
      (error as { body?: { detail?: string } })?.body?.detail ??
      (error instanceof Error ? error.message : "");
    toast.error(detail || "Could not record the decision");
    setDecidingId(null);
  };

  const approveMutation = useMutation({
    mutationFn: (id: ArtifactId) => approveArtifact(id),
    onSuccess: (artifact) => settle(artifact as Artifact, "Approved"),
    onError: fail,
  });

  const rejectMutation = useMutation({
    mutationFn: (id: ArtifactId) => rejectArtifact(id),
    onSuccess: (artifact) => settle(artifact as Artifact, "Rejected"),
    onError: fail,
  });

  return {
    canDecide,
    approve: (id) => {
      setDecidingId(id);
      approveMutation.mutate(id);
    },
    reject: (id) => {
      setDecidingId(id);
      rejectMutation.mutate(id);
    },
    decidingId,
  };
}
