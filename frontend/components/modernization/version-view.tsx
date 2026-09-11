"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, Loader2, XCircle } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Textarea } from "@/components/ui/textarea";
import { useSession } from "@/hooks/use-session";
import { GATE_POLICY } from "@/lib/agents";
import {
  getStageVersion,
  publishStageVersion,
  rejectStageVersion,
  toBackendStage,
  type ArtifactVersionDetail,
} from "@/lib/api/artifact-versions";
import { versionExportHref, type Track3Stage } from "@/lib/api/modernization";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import type { ProjectId } from "@/lib/schemas";

import { VERSION_STATUS } from "./version-history";

/**
 * One recorded brief or assessment, opened from the left rail: the version's own
 * frozen payload, a Word/PDF download of THAT version, and the Sign-off.
 *
 * THE SIGN-OFF IS THE EXISTING STAGE-VERSION GATE. Approve publishes this version (and
 * supersedes an older published one); the backend demands the stage's approve
 * permission and refuses a person approving a version they produced themselves — its
 * message is shown as-is, because "you produced this" and "a newer version is already
 * approved" need different actions from the reader.
 */
export function VersionView({
  projectId,
  stage,
  noun,
  version,
  render,
}: {
  projectId: ProjectId;
  stage: Track3Stage;
  noun: string;
  version: number;
  render: (payload: unknown, detail: ArtifactVersionDetail) => React.ReactNode;
}) {
  const queryClient = useQueryClient();
  const session = useSession();
  const canDecide = hasPermission(session, `artifact:approve_${toBackendStage(stage)}`);
  const gate = GATE_POLICY[stage];

  const detailQ = useQuery({
    queryKey: [...qk.artifactVersions.forStage(projectId, stage), version],
    queryFn: () => getStageVersion(projectId, stage, version),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: qk.artifactVersions.forStage(projectId, stage) });

  const publish = useMutation({
    mutationFn: () => publishStageVersion(projectId, stage, version),
    onSuccess: () => {
      toast.success(`${noun[0]!.toUpperCase()}${noun.slice(1)} v${version} approved`);
      void invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Could not approve this version"),
  });

  const [rejecting, setRejecting] = React.useState(false);
  const [reason, setReason] = React.useState("");
  const reject = useMutation({
    mutationFn: () => rejectStageVersion(projectId, stage, version, reason.trim()),
    onSuccess: () => {
      toast.success(`${noun[0]!.toUpperCase()}${noun.slice(1)} v${version} rejected`);
      setRejecting(false);
      setReason("");
      void invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Could not reject this version"),
  });

  if (detailQ.isLoading) return <LoadingState variant="card" />;
  if (detailQ.isError || !detailQ.data) {
    return (
      <ErrorState
        title={`${noun} v${version} could not be loaded`}
        description={detailQ.error instanceof Error ? detailQ.error.message : "Unknown error."}
        onRetry={() => detailQ.refetch()}
      />
    );
  }

  const detail = detailQ.data;
  const meta = VERSION_STATUS[detail.status];
  const busy = publish.isPending || reject.isPending;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/20 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium capitalize">
            {noun} v{detail.version}
          </span>
          <Badge variant={meta.variant}>{meta.label}</Badge>
          {detail.status === "rejected" && detail.rejectionReason && (
            <span className="text-muted-foreground text-xs">Reason: {detail.rejectionReason}</span>
          )}
          {detail.status === "published" && detail.publishedAt && (
            <span className="text-muted-foreground text-xs">
              Approved {new Date(detail.publishedAt).toLocaleString()}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <a href={versionExportHref(projectId, stage, version, "docx")} download>
              <Download className="size-4" aria-hidden />
              Word (.docx)
            </a>
          </Button>
          <Button asChild variant="outline" size="sm">
            <a href={versionExportHref(projectId, stage, version, "pdf")} download>
              <Download className="size-4" aria-hidden />
              PDF
            </a>
          </Button>
          {canDecide && detail.status === "draft" && (
            <>
              <Button size="sm" onClick={() => publish.mutate()} disabled={busy}>
                {publish.isPending ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <CheckCircle2 className="size-4" aria-hidden />
                )}
                Approve
              </Button>
              <Button size="sm" variant="outline" onClick={() => setRejecting(true)} disabled={busy}>
                <XCircle className="size-4" aria-hidden />
                Reject
              </Button>
            </>
          )}
        </div>
      </div>
      {detail.status === "draft" && (
        <p className="text-muted-foreground -mt-4 text-xs">
          {gate.title.replace(/^Gate: /, "Sign-off: ")} — {gate.ownerLabel} or Project Admin, and not the person
          who produced it.
        </p>
      )}

      {render(detail.payload, detail)}

      <Dialog open={rejecting} onOpenChange={setRejecting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Reject {noun} v{version}
            </DialogTitle>
            <DialogDescription>
              Say what is wrong — the reason stays on the version, and the agent sees it next time.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. The target stack should be .NET 8 on Kubernetes, not App Service."
            rows={4}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
            <Button onClick={() => reject.mutate()} disabled={!reason.trim() || reject.isPending}>
              {reject.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Reject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
