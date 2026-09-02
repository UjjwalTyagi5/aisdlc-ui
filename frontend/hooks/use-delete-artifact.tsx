"use client";

/**
 * Delete an artifact, with the confirmation step and the permission gate.
 *
 * ONE HOOK RATHER THAN THREE COPIES. `ArtifactList` renders on the Design page, the
 * Requirements page and `StageWorkbench`; a delete that confirms on one of them and not
 * another is the kind of inconsistency that gets discovered by destroying something.
 * The dialog copy, the toast wording, the permission check and the cache invalidation
 * all live here so every list behaves identically.
 *
 * THE GATE IS UX, NOT SECURITY. `hasPermission` decides whether to render the button at
 * all; the authoritative check is the backend's `require_permission("artifact:delete")`.
 * A user who tampers with client state to reveal the control still gets a 403.
 */

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { deleteArtifact } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import { useSession } from "@/hooks/use-session";
import type { Artifact, ProjectId } from "@/lib/schemas";

export interface UseDeleteArtifactResult {
  /** Pass to `ArtifactList.onDelete`. `undefined` when the user lacks the permission,
   *  which makes the list hide the control rather than show a disabled one. */
  onDelete: ((artifact: Artifact) => void) | undefined;
  /** Pass to `ArtifactList.deletingId`. */
  deletingId: string | null;
  /** Render once, anywhere in the tree. */
  dialog: React.ReactNode;
}

export function useDeleteArtifact(
  projectId: ProjectId,
  opts?: {
    /** Called after a successful delete — use it to clear a detail pane still showing
     *  the artifact that no longer exists. */
    onDeleted?: (artifact: Artifact) => void;
  },
): UseDeleteArtifactResult {
  const session = useSession();
  const queryClient = useQueryClient();
  const [pending, setPending] = React.useState<Artifact | null>(null);
  const onDeleted = opts?.onDeleted;

  const allowed = hasPermission(session, "artifact:delete");

  const mutation = useMutation({
    mutationFn: (artifact: Artifact) => deleteArtifact(artifact.id),
    onSuccess: (_data, artifact) => {
      toast.success(`Deleted ${artifact.title}`);
      // Both keys: the list the user is looking at, and any detail query holding the
      // now-gone artifact. Without the second, navigating back to it renders stale data.
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.detail(artifact.id) });
      setPending(null);
      onDeleted?.(artifact);
    },
    onError: (error: unknown) => {
      // The backend distinguishes "the file could not be deleted, so the artifact was
      // kept" (502) from a permission or not-found failure, and that difference decides
      // whether retrying is worth it — so surface its message rather than a generic one.
      const detail =
        (error as { body?: { detail?: string } })?.body?.detail ??
        (error instanceof Error ? error.message : "");
      toast.error(detail || "Could not delete the artifact");
      setPending(null);
    },
  });

  const dialog = (
    <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this artifact?</DialogTitle>
          <DialogDescription>
            {/* Name the artifact and state the two things that go, because "are you
                sure?" alone does not tell the user what they are about to lose. */}
            <span className="font-medium">{pending?.title}</span> and its stored file will
            be permanently deleted. This cannot be undone. A record of the deletion is
            kept in the audit log.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setPending(null)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => pending && mutation.mutate(pending)}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  return {
    onDelete: allowed ? (artifact: Artifact) => setPending(artifact) : undefined,
    deletingId: mutation.isPending ? (pending?.id ?? null) : null,
    dialog,
  };
}
