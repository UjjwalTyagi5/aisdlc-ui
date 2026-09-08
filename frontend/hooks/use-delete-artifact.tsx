"use client";

/**
 * ASK to delete an artifact. NOTHING IS DELETED BY THIS HOOK.
 *
 * It used to call `deleteArtifact` and destroy the row and its file on the spot. That is
 * the asymmetry the approval work removed: uploading a document is gated on somebody
 * accepting it, while removing one was gated on nothing beyond `artifact:delete`, which
 * most roles hold — so one confirmed click could undo an approval nobody was asked about.
 *
 * It now raises a governance request routed to the owner of the artifact's own stage —
 * the person whose Approve put it in the record — or to the Project Admin for a
 * project-wide one. The file goes when they approve it, from Requests & Approvals.
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
    onSuccess: (_data, { artifact }) => {
      // SAYS REQUESTED, NEVER DELETED. The file is still there until the owner agrees,
      // and a toast claiming otherwise would be the same lie as a 204 from a call that
      // destroyed nothing.
      toast.success(`Deletion of ${artifact.title} sent to its owner for approval`);
      // The artifact list is UNCHANGED — nothing was removed — but the request queues
      // are, so those are what actually need refreshing.
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      void queryClient.invalidateQueries({ queryKey: qk.governanceApprovals.list() });
      setPending(null);
      setReason("");
      onDeleted?.(artifact);
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
          <DialogTitle>Request deletion</DialogTitle>
          <DialogDescription>
            {/* Names the artifact and says who decides, because "are you sure?" alone
                tells the user neither what they lose nor what happens next. */}
            <span className="font-medium">{pending?.title}</span> and its stored file will
            be removed once the owner of its stage approves. Nothing is deleted now — the
            request appears in Requests &amp; Approvals.
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
            {mutation.isPending ? "Sending…" : "Send for approval"}
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
