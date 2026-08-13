"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Loader2, Trash2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { RunDetailDrawer } from "@/components/app/run-detail-drawer";
import { cancelRun, deleteRun, getRun } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import type { RunId } from "@/lib/schemas";

const ACTIVE_STATUSES = ["queued", "running", "awaiting_approval", "awaiting_clarification"];

/**
 * URL-addressable run detail. Removes the /runs/{id} 404 that the runs table,
 * project recent-runs, and the drawer's own deep-link all dead-ended on. Reuses
 * the full RunDetailDrawer (phase pipeline, live SSE feed, timeline, audit,
 * approvals) rendered open over this page, and adds Cancel + Delete actions.
 */
export default function RunDetailPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const params = useParams<{ id: string }>();
  const id = params.id as RunId;

  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const runQ = useQuery({
    queryKey: qk.runs.detail(id),
    queryFn: () => getRun(id),
  });

  const cancelMutation = useMutation({
    mutationFn: () => cancelRun(id),
    onSuccess: () => {
      toast.success("Run cancelled");
      void queryClient.invalidateQueries({ queryKey: qk.runs.detail(id) });
      void queryClient.invalidateQueries({ queryKey: qk.runs.all() });
    },
    onError: (err) =>
      toast.error("Couldn't cancel run", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteRun(id),
    onSuccess: () => {
      toast.success("Run deleted");
      void queryClient.invalidateQueries({ queryKey: qk.runs.all() });
      router.push("/runs");
    },
    onError: (err) =>
      toast.error("Couldn't delete run", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const status = runQ.data?.status;
  const isActive = !!status && ACTIVE_STATUSES.includes(status);

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <div className="flex items-center justify-between gap-3">
        <Button variant="ghost" size="sm" asChild className="-ml-2">
          <Link href="/runs">
            <ArrowLeft className="size-4" aria-hidden />
            Back to runs
          </Link>
        </Button>

        {runQ.data && (
          <div className="flex gap-1.5">
            {isActive && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
                aria-busy={cancelMutation.isPending}
                className="border-line-soft"
              >
                {cancelMutation.isPending ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <XCircle className="size-4" aria-hidden />
                )}
                Cancel run
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmDelete(true)}
              className="border-line-soft text-destructive hover:text-destructive"
            >
              <Trash2 className="size-4" aria-hidden />
              Delete
            </Button>
          </div>
        )}
      </div>

      {runQ.isLoading ? (
        <LoadingState variant="list" rows={4} />
      ) : runQ.isError ? (
        <ApiErrorState
          title="Couldn't load run"
          error={
            runQ.error && "code" in runQ.error && "message" in runQ.error
              ? (runQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(runQ.error && "code" in runQ.error)
              ? runQ.error instanceof Error
                ? runQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => runQ.refetch()}
        />
      ) : runQ.data ? (
        <RunDetailDrawer
          run={runQ.data}
          open
          onOpenChange={(v) => {
            if (!v) router.push("/runs");
          }}
        />
      ) : null}

      <Dialog open={confirmDelete} onOpenChange={(v) => !deleteMutation.isPending && setConfirmDelete(v)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="font-display flex items-center gap-2">
              <Trash2 className="text-destructive size-5" aria-hidden />
              Delete this run?
            </DialogTitle>
            <DialogDescription>
              This permanently deletes the run and its artifacts. If it&apos;s still active, its
              workflow is terminated first. This can&apos;t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmDelete(false)}
              disabled={deleteMutation.isPending}
              className="border-line-soft"
            >
              Keep
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
              aria-busy={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
              Delete run
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
