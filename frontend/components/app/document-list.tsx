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
  CheckCircle2, Clock, Download, FileText, Loader2, Trash2, Upload, XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/ui/empty-state";
import { useDeleteArtifact } from "@/hooks/use-delete-artifact";
import { useSession } from "@/hooks/use-session";
import {
  approveArtifact, listArtifacts, rejectArtifact, uploadArtifact,
} from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import { PHASE_LABEL } from "@/lib/agents";
import { ownerRoleLabel } from "@/lib/roles";
import type { Artifact, Phase, ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

/** The backend stage name; the UI's Phase union says `review`. */
function stageLabel(stage?: string | null): string {
  if (!stage) return "Project-wide";
  const phase = (stage === "code_review" ? "review" : stage) as keyof typeof PHASE_LABEL;
  return PHASE_LABEL[phase] ?? stage;
}

/** Who a pending document is waiting on.
 *
 * MIRRORS `_artifact_for_decision`: the stage's own owner, or project administration —
 * a project-wide document has no owning agent, so administration is the only route.
 * Named rather than left to "Pending", because a status that says something is stuck
 * without saying whose move it is sends people to ask in chat.
 *
 * TWO EQUAL APPROVERS, NOT AN ESCALATION. A Project Admin owns every agent on their
 * project, so they and the stage's own role are peers here: either may decide, whoever
 * gets there first, and one approval closes it. Nothing waits for the other and nothing
 * is escalated to anybody. The wording says "either can approve" for that reason —
 * "X or a project admin" reads as a fallback used when X is unavailable, which would
 * misdescribe both who may act and how many decisions are needed.
 */
function waitingOn(a: Artifact): string {
  if (a.scope === "project" || !a.stage) return "a Project Admin";
  const phase = (a.stage === "code_review" ? "review" : a.stage) as Phase;
  try {
    return `${ownerRoleLabel(phase)} or Project Admin — either can approve`;
  } catch {
    // An unknown stage is not worth crashing a list over, and a Project Admin can
    // decide any document on their project whatever agent it belongs to.
    return "a Project Admin";
  }
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
  const [search, setSearch] = React.useState("");
  const [statusFilter, setStatusFilter] = React.useState<
    "all" | "approved" | "pending" | "rejected"
  >("all");
  // DELETION IS A REQUEST, NOT AN ACTION, and it comes from the SHARED hook rather
  // than a second copy here. `ArtifactList` on Requirements, Design and StageWorkbench
  // uses the same one, and a delete that asks for approval on one screen and destroys
  // outright on another is the kind of inconsistency you discover by losing a file.
  const deletion = useDeleteArtifact(projectId);

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

  // FILTERING IS SEPARATE FROM SCOPING. `scoped` is what belongs on this screen at all;
  // `documents` is what the person is currently looking for within that. Collapsing the
  // two would make an empty search look like an empty project.
  const scoped = React.useMemo(
    () =>
      (source ?? []).filter(
        (a) =>
          // `story` rows are the payload projection, not documents.
          a.type !== "story" &&
          // THIS AGENT'S DOCUMENTS, PLUS THE PROJECT-WIDE ONES — never another
          // agent's. The listing endpoint returns every document in the project, so
          // without this the Design screen showed Requirements' files badged
          // "Requirements", which is precisely the separation the scope exists to
          // make. Another agent's document belongs on that agent's screen; if this
          // stage may READ it, that happens through the published version, not by
          // appearing in this list.
          (a.scope === "project" || a.stage === stage),
      ),
    [source, stage],
  );

  /** The statuses on this screen, normalised the way the chip is: anything that is
   *  neither approved nor rejected reads as pending. */
  const presentStatuses = React.useMemo(
    () =>
      Array.from(
        new Set(
          scoped.map((a) =>
            a.status === "approved" || a.status === "rejected" ? a.status : "pending",
          ),
        ),
      ).sort(),
    [scoped],
  );

  const documents = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    return scoped.filter((a) => {
      if (statusFilter !== "all" && (a.status ?? "pending") !== statusFilter) return false;
      // Title only. The approver's email is on the row too, but matching it would make
      // typing a colleague's name return documents they merely signed, which is a
      // different question from "find the file I am thinking of".
      return !q || a.title.toLowerCase().includes(q);
    });
  }, [scoped, search, statusFilter]);

  // The file waiting on a note, and the note itself. Null when nothing is staged.
  const [staged, setStaged] = React.useState<File | null>(null);
  const [note, setNote] = React.useState("");

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });

  const upload = useMutation({
    mutationFn: ({ file, note }: { file: File; note: string }) =>
      uploadArtifact(projectId, file, { stage, note }),
    onSuccess: (a) => {
      setStaged(null);
      setNote("");
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
    // STAGED, NOT SENT. Uploading on pick left no moment to say why, and the note is
    // worth most on the documents somebody deliberately puts forward. The upload is
    // still one more click, not a dialog to dismiss.
    if (file) setStaged(file);
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

      {/* THE NOTE, ASKED FOR ONCE AND NEVER REQUIRED. An approver's first question is
          "why am I being asked to accept this", and the answer used to live only in
          whatever conversation happened around the upload. Optional on purpose: a
          mandatory box gets "." typed into it, and a meaningless note is worse than
          none because the approver still has to read it. */}
      {staged && (
        <div className="border-line-soft bg-surface-2 space-y-2 rounded-lg border p-3">
          <div className="flex items-center gap-2 text-xs">
            <FileText className="text-muted-foreground h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="truncate font-medium">{staged.name}</span>
          </div>
          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="Why are you putting this forward? (optional)"
            aria-label="Note for the approver"
            className="text-xs"
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={upload.isPending}
              onClick={() => {
                setStaged(null);
                setNote("");
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={upload.isPending}
              onClick={() => upload.mutate({ file: staged, note })}
            >
              {upload.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              Upload
            </Button>
          </div>
        </div>
      )}

      {/* SHOWN ONLY ONCE THERE IS SOMETHING TO SIFT. A search box above two documents is
          furniture; the threshold is where scanning the list stops being faster than
          typing. Hidden rather than disabled, so it does not read as broken. */}
      {scoped.length > 3 && (
        // SEARCH ON ITS OWN ROW, matching the Stories toolbar beside it — and for the
        // reason that toolbar needed fixing: this column is ~390px, and a `flex-1`
        // input sharing a row with a fixed-width select collapses towards a sliver as
        // soon as anything else joins it.
        <div className="flex flex-col gap-2">
          <Input
            placeholder="Search documents…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 w-full font-sans text-sm"
            aria-label="Search documents"
          />
          {/* ONLY THE STATUSES ACTUALLY PRESENT. Offering "Rejected" on a list with no
              rejected document is an option whose only outcome is an empty screen.
              Derived from `scoped`, not from `documents`, so choosing one status does
              not delete every other option and strand the person on it. */}
          {presentStatuses.length > 1 && (
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v as typeof statusFilter)}
            >
              <SelectTrigger className="h-8 w-32 font-sans text-xs" aria-label="Filter by status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any status</SelectItem>
                {presentStatuses.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s === "approved" ? "Approved" : s === "rejected" ? "Rejected" : "Pending"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      )}

      {scoped.length === 0 ? (
        <EmptyState
          title="No documents yet"
          description={
            canUpload
              ? "Upload one, or run the agent to generate it. Nothing is part of the record until it is approved."
              : "Nothing has been added to this project's record yet."
          }
        />
      ) : documents.length === 0 ? (
        // A DIFFERENT ANSWER FROM "no documents yet", because the situations differ: the
        // project HAS documents and this filter excludes them all. Saying "none yet"
        // here would send someone looking for a missing upload.
        <p className="text-muted-foreground rounded-md border border-dashed p-3 text-xs">
          No documents match. {scoped.length} on this screen —{" "}
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => {
              setSearch("");
              setStatusFilter("all");
            }}
          >
            clear the filters
          </button>
          .
        </p>
      ) : (
        // CAPPED AND SCROLLED. Every document added made this panel taller and pushed
        // Stories further down the page — at fifteen documents the list below it was
        // off-screen entirely. `max-h` with its own overflow keeps the panel a fixed
        // share of the column no matter how much lands in it.
        <ul className="max-h-80 divide-y overflow-y-auto rounded-md border">
          {documents.map((a) => {
            const chip = statusChip(a);
            const isProjectWide = a.scope === "project";
            // BOTH HALVES OF THE BACKEND RULE. `_artifact_for_decision` accepts the
            // stage's own owner OR project administration, and this had only the first
            // — so a Project Admin, who owns every agent on their project but need not
            // hold `artifact:approve_<stage>` for any of them, saw their own upload
            // stuck on "Pending" with no button to accept it. The server would have
            // taken the approval; the screen never offered it.
            const mayDecide = isProjectWide
              ? canApproveProject
              : canApproveStage || canApproveProject;
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

                {/* WHO AND WHEN — the question this screen exists to answer. For a
                    document still waiting, "who" is the person it is waiting ON, not
                    just who put it there: "Pending" alone tells you something is stuck
                    without telling you whose move it is. */}
                <span className="text-muted-foreground truncate text-xs">
                  {a.status === "approved" && a.approvedBy
                    ? `${a.approvedBy}${
                        a.approvedAt
                          ? ` · ${new Date(a.approvedAt).toLocaleDateString()}`
                          : ""
                      }`
                    : pending
                      ? `${a.uploadedBy ? `added by ${a.uploadedBy} · ` : ""}${
                          mayDecide ? "waiting on you" : `waiting on ${waitingOn(a)}`
                        }`
                      : a.uploadedBy
                        ? `added by ${a.uploadedBy}`
                        : ""}
                </span>

                {/* THE UPLOADER'S REASON, shown to whoever has to decide. It sits on
                    its own line rather than in the meta run above, because that line is
                    scanned and this is read. */}
                {a.uploadNote && (
                  <span
                    className="text-muted-foreground w-full basis-full text-xs italic"
                    title={a.uploadNote}
                  >
                    &ldquo;{a.uploadNote}&rdquo;
                  </span>
                )}

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
                  {/* `onDelete` is undefined when the viewer lacks `artifact:delete`,
                      which hides the control rather than showing a disabled one. */}
                  {deletion.onDelete && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Request deletion of ${a.title}`}
                      disabled={deletion.deletingId === a.id}
                      onClick={() => deletion.onDelete?.(a)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* Rendered once; the hook owns the dialog, its reason box and the request. */}
      {deletion.dialog}
    </section>
  );
}
