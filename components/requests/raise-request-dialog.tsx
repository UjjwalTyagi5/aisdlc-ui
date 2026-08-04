"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, Info, Loader2, Paperclip, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import { createRequest } from "@/lib/api/governance-approvals";
import { qk } from "@/lib/api/query-keys";
import {
  approverChainFrom,
  initialApproverRole,
  raisableTypesFor,
} from "@/lib/requests/routing";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import {
  REQUEST_TYPE_HINT,
  REQUEST_TYPE_LABEL,
  RequestCreateInput,
  type RequestAttachment,
  type RequestPriority,
} from "@/lib/schemas/governance-approval";
import type { Project } from "@/lib/schemas";

const PRIORITY_LABEL: Record<RequestPriority, string> = {
  low: "Low",
  normal: "Normal",
  high: "High",
  urgent: "Urgent",
};

/** Bytes → "2.4 MB". Metadata only; nothing is uploaded. */
function fileSize(bytes: number): string {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/**
 * Raise a request.
 *
 * THE APPROVER IS SHOWN, NOT CHOSEN. Every other field here is the requester's
 * to set; the approver is computed from `lib/requests/routing.ts` and rendered
 * read-only, because a form that let you pick your own approver would make the
 * whole chain advisory. Showing the full remaining ladder up front is what
 * makes a later escalation read as the process working rather than the request
 * being passed around.
 *
 * Never rendered for an Organization Admin — the page hides the button, and the
 * endpoint refuses independently (403), because a dialog that cannot be opened
 * is not an access control.
 */
export function RaiseRequestDialog({
  open,
  onOpenChange,
  projects,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Projects the viewer may file against — already scope-filtered upstream. */
  projects: Project[];
}) {
  const queryClient = useQueryClient();
  const { role } = useAccessScope();
  const { units, isOrgWide } = useScopedBusinessUnits();

  // Scoped to what this role may actually ask for — see raisableTypesFor.
  const raisable = raisableTypesFor(role);
  const [type, setType] = React.useState<string>(() => raisable[0] ?? "other");
  const [title, setTitle] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [priority, setPriority] = React.useState<RequestPriority>("normal");
  const [workspaceId, setWorkspaceId] = React.useState<string>("");
  const [projectId, setProjectId] = React.useState<string>("none");
  const [attachments, setAttachments] = React.useState<RequestAttachment[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);

  // A role change (or the first resolve of the session) can invalidate the
  // selected type — reset rather than submitting one this role cannot raise.
  React.useEffect(() => {
    if (raisable.length > 0 && !raisable.includes(type as never)) {
      setType(raisable[0]!);
    }
  }, [raisable, type]);

  // One unit to choose from is not a choice — preselect rather than making the
  // person open a dropdown to pick the only option.
  React.useEffect(() => {
    if (!workspaceId && units.length === 1) setWorkspaceId(units[0]!.id);
  }, [units, workspaceId]);

  // Reset on close so a cancelled draft never resurfaces half-filled later.
  React.useEffect(() => {
    if (open) return;
    setType(raisable[0] ?? "other");
    setTitle("");
    setDescription("");
    setPriority("normal");
    setProjectId("none");
    setAttachments([]);
    setError(null);
    // Intentionally keyed on `open` alone: this resets a CLOSED dialog, and
    // adding `raisable` would re-run it mid-edit whenever the memo re-created
    // the array, wiping a half-typed request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const scopedProjects = React.useMemo(
    () =>
      projects.filter(
        (p) => !p.archived && (!workspaceId || String(p.workspaceId ?? "") === workspaceId),
      ),
    [projects, workspaceId],
  );

  const approver = initialApproverRole(
    type as Parameters<typeof initialApproverRole>[0],
    role,
  );
  const chain = approverChainFrom(approver);

  const mutation = useMutation({
    mutationFn: () =>
      createRequest({
        type: type as RequestCreateInput["type"],
        title: title.trim(),
        description: description.trim(),
        priority,
        workspaceId,
        projectId: projectId === "none" ? null : projectId,
        attachments,
      }),
    onSuccess: (created) => {
      toast.success("Request raised", {
        description: approver
          ? `Waiting on the ${ROLE_META[approver].label}.`
          : created.title,
      });
      queryClient.invalidateQueries({ queryKey: qk.governanceApprovals.list() });
      onOpenChange(false);
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Couldn't raise the request"),
  });

  function submit() {
    setError(null);
    const parsed = RequestCreateInput.safeParse({
      type,
      title: title.trim(),
      description: description.trim(),
      priority,
      workspaceId,
      projectId: projectId === "none" ? null : projectId,
      attachments,
    });
    // Validate against the same schema the endpoint uses, so the message a
    // person sees is the one the server would have produced.
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check the form");
      return;
    }
    mutation.mutate();
  }

  function addFiles(list: FileList | null) {
    if (!list) return;
    const next = Array.from(list).map((f) => ({
      name: f.name,
      sizeBytes: f.size,
      contentType: f.type || "application/octet-stream",
    }));
    setAttachments((prev) => [...prev, ...next].slice(0, 10));
    if (fileRef.current) fileRef.current.value = "";
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[620px]">
        <DialogHeader>
          <DialogTitle>Raise a request</DialogTitle>
          <DialogDescription>
            Ask for something you don&apos;t have yet — access, a model, a connector, budget
            headroom. It routes to the first tier that can grant it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="req-type">Request type</Label>
              <Select value={type} onValueChange={setType}>
                <SelectTrigger id="req-type" className="border-line-soft">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {raisable.map((t) => (
                    <SelectItem key={t} value={t}>
                      {REQUEST_TYPE_LABEL[t]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {REQUEST_TYPE_HINT[type as keyof typeof REQUEST_TYPE_HINT] && (
                <p className="text-muted-foreground text-[11.5px]">
                  {REQUEST_TYPE_HINT[type as keyof typeof REQUEST_TYPE_HINT]}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="req-priority">Priority</Label>
              <Select
                value={priority}
                onValueChange={(v) => setPriority(v as RequestPriority)}
              >
                <SelectTrigger id="req-priority" className="border-line-soft">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(PRIORITY_LABEL) as RequestPriority[]).map((p) => (
                    <SelectItem key={p} value={p}>
                      {PRIORITY_LABEL[p]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="req-title">Title</Label>
            <Input
              id="req-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Short summary of what you need"
              className="border-line-soft"
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="req-description">Description</Label>
            <Textarea
              id="req-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What do you need, and why? Include anything the approver would otherwise have to ask for."
              rows={4}
              className="border-line-soft"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="req-unit">{BUSINESS_UNIT_LABEL}</Label>
              <Select value={workspaceId} onValueChange={setWorkspaceId}>
                <SelectTrigger id="req-unit" className="border-line-soft">
                  <SelectValue placeholder="Choose one" />
                </SelectTrigger>
                <SelectContent>
                  {units.map((u) => (
                    <SelectItem key={u.id} value={u.id}>
                      {u.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {units.length === 0 && !isOrgWide && (
                <p className="text-muted-foreground text-[11.5px]">
                  You aren&apos;t bound to a business unit yet.
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="req-project">Project</Label>
              <Select value={projectId} onValueChange={setProjectId}>
                <SelectTrigger id="req-project" className="border-line-soft">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Not project-specific</SelectItem>
                  {scopedProjects.map((p) => (
                    <SelectItem key={String(p.id)} value={String(p.id)}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* ── Attachments ────────────────────────────────────────────────── */}
          <div className="space-y-1.5">
            <Label>Supporting documents</Label>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="sr-only"
              onChange={(e) => addFiles(e.target.files)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="border-line-soft h-8 gap-1.5"
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip className="size-3.5" aria-hidden />
              Attach files
            </Button>
            {attachments.length > 0 && (
              <ul className="space-y-1 pt-1">
                {attachments.map((a, i) => (
                  <li
                    key={`${a.name}-${i}`}
                    className="border-line-soft bg-surface-1 flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[12px]"
                  >
                    <Paperclip className="text-muted-foreground size-3 shrink-0" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">{a.name}</span>
                    <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">
                      {fileSize(a.sizeBytes)}
                    </span>
                    <button
                      type="button"
                      onClick={() => setAttachments((p) => p.filter((_, j) => j !== i))}
                      className="text-muted-foreground hover:text-foreground shrink-0"
                      aria-label={`Remove ${a.name}`}
                    >
                      <X className="size-3.5" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {/* Said plainly rather than implied. A fake upload that appeared to
                succeed would be worse than none, because someone would rely on
                it — see RequestAttachment in the schema. */}
            <p className="text-muted-foreground flex items-center gap-1.5 text-[11.5px]">
              <Info className="size-3 shrink-0" aria-hidden />
              File names are recorded with the request; contents are not stored in this build.
            </p>
          </div>

          {/* ── Approver (derived) ─────────────────────────────────────────── */}
          <div className="border-line-soft bg-surface-1 space-y-1.5 rounded-lg border px-3 py-2.5">
            <p className="text-muted-foreground font-mono text-[10px] tracking-[0.14em] uppercase">
              Approval route
            </p>
            {approver ? (
              <>
                <p className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
                  {chain.map((r, i) => (
                    <React.Fragment key={r}>
                      {i > 0 && (
                        <ArrowRight className="text-muted-foreground size-3" aria-hidden />
                      )}
                      <span
                        className={cn(
                          i === 0 ? "text-foreground font-medium" : "text-muted-foreground",
                        )}
                      >
                        {ROLE_META[r as PlatformRole].label}
                      </span>
                    </React.Fragment>
                  ))}
                </p>
                <p className="text-muted-foreground text-[11.5px]">
                  Goes to the {ROLE_META[approver].label} first. If they can&apos;t grant it, it
                  climbs one tier at a time — it never stalls without an approver.
                </p>
              </>
            ) : (
              <p className="text-muted-foreground text-[12px]">
                No approval route for your role.
              </p>
            )}
          </div>

          {error && (
            <p role="alert" className="text-destructive text-[12.5px]">
              {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={mutation.isPending}>
            {mutation.isPending && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            Submit request
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
