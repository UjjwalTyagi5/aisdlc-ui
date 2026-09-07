"use client";

/**
 * A project's documents: what is approved, who approved it, and when.
 *
 * SEPARATE FROM `ArtifactList` BECAUSE THEY ARE SEPARATE THINGS. That component renders
 * the Requirements page's `story` rows, which are projections of
 * `Run.requirements_payload` — synthesised ids, no blob, no row in `artifacts`. They can
 * never be approved and never carry an approver, so an approval column beside them
 * would be blank for most of the list, and a column that is usually blank is a column
 * people stop reading.
 *
 * TWO SCOPES, SHOWN AS A BADGE. A document belongs either to one agent (`stage`) or to
 * the whole project (`scope: "project"`) — a policy, a standard. The badge is what makes
 * "which agent put this here" answerable at a glance, which is the reason the scope
 * exists at all.
 *
 * A PENDING DOCUMENT HAS NO DOWNLOAD LINK, and that is not an omission. Its bytes are in
 * the `_pending` area with no URL until somebody accepts it, so offering a download
 * would produce a 404 and read as a broken product rather than as a gate doing its job.
 *
 * THE BUTTONS ARE A UX GATE, NOT A SECURITY ONE. The route requires the stage's own
 * approve permission (or project administration for a project-level document) and
 * enforces it server-side. Hiding a control the caller cannot use is a courtesy; the
 * refusal behind it is the actual rule.
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, Clock, Download, FileText, Loader2, Upload, XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useSession } from "@/hooks/use-session";
import {
  approveArtifact, listArtifacts, rejectArtifact, uploadArtifact,
} from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import { PHASE_LABEL } from "@/lib/agents";
import type { Artifact, ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

/** The backend stage name; the UI's Phase union says `review`. */
function stageLabel(stage?: string | null): string {
  if (!stage) return "Project-wide";
  const phase = (stage === "code_review" ? "review" : stage) as keyof typeof PHASE_LABEL;
  return PHASE_LABEL[phase] ?? stage;
}

function statusChip(a: Artifact) {
  if (a.status === "approved") {
    return {
      label: "Approved",
      cls: "border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400",
      Icon: CheckCircle2,
    };
  }
  if (a.status === "rejected") {
    return {
      label: "Rejected",
      cls: "border-destructive/30 bg-destructive/10 text-destructive",
      Icon: XCircle,
    };
  }
  return {
    label: "Pending",
    cls: "border-amber-600/30 bg-amber-600/10 text-amber-700 dark:text-amber-400",
    Icon: Clock,
  };
}

export interface DocumentListProps {
  projectId: ProjectId;
  /** Every artifact for the project; stories are filtered out here.
   *
   *  OPTIONAL. Pages that already query artifacts pass theirs so the same data is not
   *  fetched twice; pages that do not can drop this component in with a projectId and
   *  a stage and nothing else. Both share the query key, so an approval on one screen
   *  refreshes the other. */
  items?: readonly Artifact[] | null;
  /** The stage this screen belongs to — what an upload from here is filed under.
   *  A BACKEND stage name. */
  stage: string;
  className?: string;
}

export function DocumentList({
  projectId,
  items,
  stage,
  className,
}: DocumentListProps) {
  const session = useSession();
  const queryClient = useQueryClient();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [busyId, setBusyId] = React.useState<string | null>(null);

  const canUpload = hasPermission(session, "run:create");
  // The stage's own permission, or project administration for the project-wide ones.
  // Mirrors the route; see the note above about this being UX rather than the rule.
  const canApproveStage = hasPermission(session, `artifact:approve_${stage}`);
  const canApproveProject = hasPermission(session, "approve");

  // Only fetches when the caller did not supply the list — otherwise this is inert
  // and the parent's data is used as-is.
  const ownQ = useQuery({
    queryKey: qk.artifacts.forProject(projectId),
    queryFn: () => listArtifacts(projectId),
    enabled: items === undefined,
  });
  const source = items === undefined ? ownQ.data : items;

  const documents = React.useMemo(
    // `story` rows are the payload projection, not documents. Everything else is a
    // real row in `artifacts` with a blob and an approval state.
    () => (source ?? []).filter((a) => a.type !== "story"),
    [source],
  );

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });

  const upload = useMutation({
    mutationFn: (file: File) => uploadArtifact(projectId, file, { stage }),
    onSuccess: (a) => {
      toast.success(`${a.title} uploaded — waiting for approval`);
      void refresh();
    },
    // The backend says WHY: a rejected extension, an oversized file, an unknown stage.
    // Three different things the user has to act on differently.
    onError: (e: Error) => toast.error(e.message || "Upload failed"),
  });

  const decide = useMutation({
    mutationFn: ({ a, approve }: { a: Artifact; approve: boolean }) =>
      approve ? approveArtifact(a.id) : rejectArtifact(a.id, "Rejected from Documents"),
    onMutate: ({ a }) => setBusyId(a.id),
    onSettled: () => setBusyId(null),
    onSuccess: (_r, { approve }) => {
      toast.success(approve ? "Document approved" : "Document rejected");
      void refresh();
    },
    onError: (e: Error) => toast.error(e.message || "Could not decide this document"),
  });

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset first: picking the SAME file twice fires no change event otherwise, so a
    // retry after a failed upload would appear to do nothing.
    e.target.value = "";
    if (file) upload.mutate(file);
  };

  return (
    <section className={cn("space-y-3", className)}>
      <header className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">Documents</h3>
          <p className="text-muted-foreground text-xs">
            Uploaded and generated files. Approved ones are part of the project&apos;s
            record.
          </p>
        </div>
        {canUpload && (
          <>
            <input
              ref={inputRef}
              type="file"
              className="hidden"
              onChange={onPick}
              aria-label="Upload a document"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={upload.isPending}
              onClick={() => inputRef.current?.click()}
            >
              {upload.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              Upload
            </Button>
          </>
        )}
      </header>

      {documents.length === 0 ? (
        <EmptyState
          title="No documents yet"
          description={
            canUpload
              ? "Upload one, or run the agent to generate it. Nothing is part of the record until it is approved."
              : "Nothing has been added to this project's record yet."
          }
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {documents.map((a) => {
            const chip = statusChip(a);
            const isProjectWide = a.scope === "project";
            const mayDecide = isProjectWide ? canApproveProject : canApproveStage;
            const pending = a.status !== "approved" && a.status !== "rejected";
            const busy = busyId === a.id;
            return (
              <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3">
                <FileText className="text-muted-foreground h-4 w-4 shrink-0" />
                <span className="truncate text-sm font-medium">{a.title}</span>

                <Badge variant="outline" className="text-muted-foreground shrink-0">
                  {isProjectWide ? "Project-wide" : stageLabel(a.stage)}
                </Badge>
                <Badge variant="outline" className={cn("shrink-0 gap-1", chip.cls)}>
                  <chip.Icon className="h-3 w-3" />
                  {chip.label}
                </Badge>

                {/* WHO AND WHEN — the question this screen exists to answer. */}
                <span className="text-muted-foreground truncate text-xs">
                  {a.status === "approved" && a.approvedBy
                    ? `${a.approvedBy}${
                        a.approvedAt
                          ? ` · ${new Date(a.approvedAt).toLocaleDateString()}`
                          : ""
                      }`
                    : a.uploadedBy
                      ? `added by ${a.uploadedBy}`
                      : ""}
                </span>

                <div className="ml-auto flex shrink-0 items-center gap-2">
                  {/* `downloadUrl` IS the decision, not a hint. The backend sets it
                      only when the document is approved AND its bytes actually landed,
                      so re-deriving the condition here would be a second copy of the
                      rule that could disagree with the first. */}
                  {a.downloadUrl && (
                    <Button size="sm" variant="ghost" asChild>
                      <a href={a.downloadUrl} download>
                        <Download className="h-3.5 w-3.5" />
                        <span className="sr-only">Download {a.title}</span>
                      </a>
                    </Button>
                  )}
                  {pending && mayDecide && (
                    <>
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={() => decide.mutate({ a, approve: true })}
                      >
                        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : "Approve"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => decide.mutate({ a, approve: false })}
                      >
                        Reject
                      </Button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
