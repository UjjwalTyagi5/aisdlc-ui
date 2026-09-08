"use client";

/**
 * Delete an artifact if you own it; otherwise ask the person who does.
 *
 * IT USED TO DESTROY IT UNCONDITIONALLY, which was the asymmetry the approval work
 * removed: uploading a document is gated on somebody accepting it, while removing one
 * was gated on nothing beyond `artifact:delete`, which most roles hold — so one
 * confirmed click could undo an approval nobody was asked about.
 *
 * WHO OWNS WHAT IS THE BACKEND'S CALL, and this hook does not try to predict it. A
 * Project Admin owns every agent on their project by default, and a stage's own role
 * (`artifact:approve_<stage>`) owns its agent — the same rule the approve and reject
 * routes already use. Those people delete outright; sending them through an approval
 * would ask for permission they already hold and could never complete anyway, because
 * self-approval is blocked and the request would escalate away from them.
 *
 * Everyone else raises a governance request routed to the stage's owner — or to the
 * Project Admin for a project-wide document, which has no stage to own it — and the
 * file goes only when they approve from Requests & Approvals.
 *
 * ONE HOOK RATHER THAN FOUR COPIES. `ArtifactList` renders on the Design page, the
 * Requirements page and `StageWorkbench`, and `DocumentList` has a delete control of its
 * own; a delete that asks for approval on one of them and not another is the kind of
 * inconsistency discovered by destroying something. The dialog, the required reason, the
 * toast wording, the permission check and the invalidation all live here.
 *
 * THE GATE IS UX, NOT SECURITY. `hasPermission` decides whether to render the button;
 * the authoritative checks are the backend's `require_permission("artifact:delete")` and
 * the approver it routes to. A user who tampers with client state still gets a 403.
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
import { Textarea } from "@/components/ui/textarea";
import { requestArtifactDeletion } from "@/lib/api/artifacts";
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
  // REQUIRED, and the backend 422s without it. Somebody is being asked to destroy
  // something irreversibly, and "approve this deletion" with no stated why is not a
  // decision anyone can take responsibly.
  const [reason, setReason] = React.useState("");
  const onDeleted = opts?.onDeleted;

  const allowed = hasPermission(session, "artifact:delete");

  const mutation = useMutation({
    mutationFn: ({ artifact, why }: { artifact: Artifact; why: string }) =>
      requestArtifactDeletion(artifact.id, why),
    onSuccess: ({ deleted }, { artifact }) => {
      // THE TOAST FOLLOWS WHAT ACTUALLY HAPPENED. An owner's click destroys the file;
      // everyone else's raises a request and destroys nothing. Saying "sent for
      // approval" after a real deletion — or "deleted" after a request — is the same
      // class of lie as a 204 from a call that removed nothing.
      toast.success(
        deleted
          ? `Deleted ${artifact.title}`
          : `Deletion of ${artifact.title} sent to its owner for approval`,
      );
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      void queryClient.invalidateQueries({ queryKey: qk.governanceApprovals.list() });
      setPending(null);
      setReason("");
      // Only when it is actually gone: the callers use this to clear a detail pane
      // showing the artifact, and closing it on a mere request would suggest the file
      // had already been removed.
      if (deleted) onDeleted?.(artifact);
    },
    onError: (error: unknown) => {
      // The backend's own message: "say why this should be deleted" and a permission
      // refusal need different actions from the reader.
      const detail =
        (error as { body?: { detail?: string } })?.body?.detail ??
        (error instanceof Error ? error.message : "");
      toast.error(detail || "Could not request deletion");
    },
  });

  const dialog = (
    <Dialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) {
          setPending(null);
          setReason("");
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this document?</DialogTitle>
          <DialogDescription>
            {/* Deliberately covers BOTH outcomes rather than promising one. The client
                cannot know which applies without asking the server — ownership is the
                backend's call — and a dialog that promised "nothing is deleted now" to
                a Project Admin, who owns every agent, would be wrong every time. */}
            <span className="font-medium">{pending?.title}</span> and its stored file are
            removed. If you own this agent that happens now; otherwise the owner is asked
            first and nothing changes until they approve.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why should this be deleted?"
          aria-label="Reason for deletion"
          rows={3}
        />
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setPending(null)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() =>
              pending && mutation.mutate({ artifact: pending, why: reason.trim() })
            }
            disabled={!reason.trim() || mutation.isPending}
          >
            {mutation.isPending ? "Working…" : "Delete"}
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
