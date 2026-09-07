"use client";

/**
 * Publication state for one stage's artifact payload, and the decision on it.
 *
 * WHAT THIS SCREEN HAS TO GET RIGHT. Four states, and the two in the middle are the
 * ones a UI naturally gets wrong:
 *
 *   draft       produced, nobody has signed it. NOT a failure — most versions sit here.
 *   published   signed. The version a consuming agent is entitled to read.
 *   superseded  was published, a newer version has replaced it. Deliberately not
 *               styled as an error: it was legitimate work, and a run that consumed
 *               it is still correct about what it built on.
 *   rejected    turned down, with a reason. Kept readable so the next run can see why.
 *
 * THE EMPTY STATE IS THE POINT. "Nothing published yet" must read as a real answer,
 * because that is exactly what a consuming agent will be told. Showing the draft as
 * though it were approved is the failure this whole feature exists to prevent.
 *
 * THE GATE HERE IS UX, NOT SECURITY. `hasPermission` decides whether the buttons
 * render; the route demands `artifact:approve_<stage>` AND project access, and refuses
 * self-publication on top. Someone who tampers with client state to reveal the
 * controls gets the backend's refusal in the error banner — which is a better outcome
 * than a button that silently does nothing.
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, ChevronDown, ChevronRight, Clock, FileClock, KeyRound, Loader2,
  Snowflake, XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { useSession } from "@/hooks/use-session";
import {
  getVersionConsumers, listStageVersions, publishStageVersion, rejectStageVersion,
  snapshotStageVersion, toBackendStage, type ArtifactVersion,
} from "@/lib/api/artifact-versions";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import { ownerRoleLabel } from "@/lib/roles";
import type { Phase, ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

/** The badge each state earns. `superseded` is muted rather than destructive — it is
 *  replaced work, not wrong work, and colouring it as an error teaches people to
 *  ignore the colour. */
function statusChip(status: ArtifactVersion["status"]) {
  switch (status) {
    case "published":
      return {
        label: "Published",
        cls: "border-emerald-600/30 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400",
        Icon: CheckCircle2,
      };
    case "rejected":
      return {
        label: "Rejected",
        cls: "border-destructive/30 bg-destructive/10 text-destructive",
        Icon: XCircle,
      };
    case "superseded":
      return {
        label: "Superseded",
        cls: "border-border bg-muted text-muted-foreground",
        Icon: FileClock,
      };
    default:
      return {
        label: "Draft",
        cls: "border-amber-600/30 bg-amber-600/10 text-amber-700 dark:text-amber-400",
        Icon: Clock,
      };
  }
}

export interface StageVersionPanelProps {
  projectId: ProjectId;
  phase: Phase;
  className?: string;
}

/**
 * What was built on one version — the blast radius.
 *
 * THE QUESTION ASKED WHEN A VERSION TURNS OUT TO BE WRONG: everything downstream that
 * read it, and therefore everything that has to be looked at again. Collapsed by
 * default because most of the time nobody is asking it, and a list nobody wants is
 * noise on a screen that has a decision to make.
 */
function VersionConsumerList({
  projectId,
  phase,
  version,
}: {
  projectId: ProjectId;
  phase: Phase;
  version: number;
}) {
  const q = useQuery({
    queryKey: qk.artifactVersions.versionConsumers(projectId, phase, version),
    queryFn: () => getVersionConsumers(projectId, phase, version),
  });

  if (q.isLoading) return <p className="text-muted-foreground text-xs">Loading…</p>;
  if (q.isError) {
    return (
      <p className="text-destructive text-xs">
        {(q.error as Error)?.message ?? "Could not load consumers"}
      </p>
    );
  }

  const rows = q.data?.consumers ?? [];
  if (rows.length === 0) {
    // A real answer, not an empty box: nothing has built on this yet.
    return (
      <p className="text-muted-foreground text-xs">
        Nothing has been built on this version yet.
      </p>
    );
  }

  return (
    <ul className="space-y-1">
      {rows.map((c, i) => (
        <li
          key={`${c.consumerStage}-${i}`}
          className="text-muted-foreground flex items-center gap-2 text-xs"
        >
          <span className="font-medium">{c.consumerStage}</span>
          {c.viaGrant && (
            <span
              className="flex items-center gap-1 text-amber-700 dark:text-amber-400"
              title="Read under an owner-granted exception, not because it was published"
            >
              <KeyRound className="h-3 w-3" />
              exception
            </span>
          )}
          <span className="ml-auto">
            {c.consumedAt ? new Date(c.consumedAt).toLocaleString() : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function StageVersionPanel({
  projectId,
  phase,
  className,
}: StageVersionPanelProps) {
  const session = useSession();
  const queryClient = useQueryClient();
  const [deciding, setDeciding] = React.useState<number | null>(null);
  const [expanded, setExpanded] = React.useState<number | null>(null);

  const stage = toBackendStage(phase);
  const canDecide = hasPermission(session, `artifact:approve_${stage}`);
  // Freezing is producing, not accepting — the same permission as running the agent.
  const canProduce = hasPermission(session, "run:create");
  // `produced_by` is the backend's `request.state.user_id`, which is this same id —
  // comparing against email instead would silently never match.
  const me = session?.user.id ?? null;

  const versionsQ = useQuery({
    queryKey: qk.artifactVersions.forStage(projectId, phase),
    queryFn: () => listStageVersions(projectId, phase),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: qk.artifactVersions.forStage(projectId, phase),
    });

  const publish = useMutation({
    mutationFn: (version: number) => publishStageVersion(projectId, phase, version),
    onMutate: (version) => setDeciding(version),
    onSettled: () => setDeciding(null),
    onSuccess: (row) => {
      toast.success(`${phase} v${row.version} published`);
      void invalidate();
    },
    // The backend's own message, not a generic one: "you cannot publish your own
    // version" and "a newer version is already published" need different actions from
    // the reader, and flattening them to "Something went wrong" hides which.
    onError: (e: Error) => toast.error(e.message || "Could not publish this version"),
  });

  const reject = useMutation({
    mutationFn: ({ version, reason }: { version: number; reason: string }) =>
      rejectStageVersion(projectId, phase, version, reason),
    onMutate: ({ version }) => setDeciding(version),
    onSettled: () => setDeciding(null),
    onSuccess: (row) => {
      toast.success(`${phase} v${row.version} rejected`);
      void invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Could not reject this version"),
  });

  // A rejection must say why — the service AND a CHECK constraint both require it, so
  // the reason is collected before the call rather than after a round trip that could
  // only fail. Deliberately a dialog rather than window.prompt: a native prompt blocks
  // the whole page, cannot be styled or tested, and reads as a bug in a product this
  // size.
  const [rejecting, setRejecting] = React.useState<number | null>(null);
  const [reason, setReason] = React.useState("");

  // A version now freezes the stage's PAYLOAD and nothing else. It used to also carry
  // a hand-ticked list of documents (`covers`) that decided which of them other agents
  // could read — so a document had to be approved and then, in a second ceremony,
  // selected here. Approval is the whole gate now, and this dialog is a confirmation
  // rather than a form.
  const [freezing, setFreezing] = React.useState(false);

  const freeze = useMutation({
    // No payload: the backend reads the stage's working output itself. Sending it from
    // here would let somebody freeze something the agent never produced.
    mutationFn: () =>
      snapshotStageVersion(projectId, phase, { payload: undefined }),
    onSuccess: (row) => {
      toast.success(
        row.version
          ? `Froze ${phase} v${row.version}`
          : "Version frozen",
      );
      setFreezing(false);
      void invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Could not freeze a version"),
  });

  const submitRejection = () => {
    const text = reason.trim();
    if (!text || rejecting == null) return;
    reject.mutate({ version: rejecting, reason: text });
    setRejecting(null);
    setReason("");
  };

  if (versionsQ.isLoading) return <LoadingState label="Loading versions…" />;
  if (versionsQ.isError) {
    return (
      <ErrorState
        title="Could not load versions"
        description={(versionsQ.error as Error)?.message}
        onRetry={() => void versionsQ.refetch()}
      />
    );
  }

  const versions = versionsQ.data ?? [];
  const published = versions.find((v) => v.status === "published");

  return (
    <section className={cn("space-y-3", className)}>
      <header className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium">Published version</h3>
          <p className="text-xs text-muted-foreground">
            {published
              ? `v${published.version}, signed off by ${published.publishedBy}. This is what other agents may read.`
              : `Nothing published yet — other agents have no approved ${phase} to read. ${ownerRoleLabel(phase)} signs it off.`}
          </p>
        </div>
        {canProduce && (
          <Button
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={() => setFreezing(true)}
          >
            <Snowflake className="h-3.5 w-3.5" />
            Freeze
          </Button>
        )}
      </header>

      {versions.length === 0 ? (
        <EmptyState
          title="No versions yet"
          description={`A version is frozen from this stage's output, then ${ownerRoleLabel(
            phase,
          )} publishes it. Nothing is approved until then.`}
        />
      ) : (
        <ul className="divide-y rounded-md border">
          {versions.map((v) => {
            const chip = statusChip(v.status);
            const busy = deciding === v.version;
            // The backend refuses this and says why; hiding the buttons avoids
            // offering a click that can only fail.
            const isMine = me != null && v.producedBy === me;
            const decidable = canDecide && v.status === "draft" && !isMine;
            return (
              <li key={v.id} className="p-3">
                <div className="flex items-center gap-3">
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground shrink-0"
                  aria-label={`Show what was built on v${v.version}`}
                  aria-expanded={expanded === v.version}
                  onClick={() =>
                    setExpanded(expanded === v.version ? null : v.version)
                  }
                >
                  {expanded === v.version ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                  )}
                </button>
                <span className="w-10 shrink-0 font-mono text-sm text-muted-foreground">
                  v{v.version}
                </span>
                <Badge variant="outline" className={cn("gap-1", chip.cls)}>
                  <chip.Icon className="h-3 w-3" />
                  {chip.label}
                </Badge>
                <div className="min-w-0 flex-1 text-xs text-muted-foreground">
                  {/* `block`, not bare `truncate`. Truncation needs a block box to
                      clip against — on an inline span it does nothing, and a user id
                      is long enough to overflow this 340px column and render on top
                      of the hash beside it. The two spans below already had it. */}
                  <span className="block truncate">produced by {v.producedBy}</span>
                  {v.status === "rejected" && v.rejectionReason ? (
                    <span className="block truncate text-destructive">
                      {v.rejectionReason}
                    </span>
                  ) : null}
                  {v.status === "published" && v.publishedBy ? (
                    <span className="block truncate">
                      published by {v.publishedBy}
                    </span>
                  ) : null}
                </div>
                <span
                  className="hidden shrink-0 font-mono text-[10px] text-muted-foreground sm:inline"
                  title={`content hash ${v.contentHash}`}
                >
                  {v.contentHash.slice(0, 8)}
                </span>
                {decidable ? (
                  <div className="flex shrink-0 gap-2">
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => publish.mutate(v.version)}
                    >
                      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : "Publish"}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => setRejecting(v.version)}
                    >
                      Reject
                    </Button>
                  </div>
                ) : v.status === "draft" && canDecide && isMine ? (
                  // Named explicitly rather than left blank: an owner who produced the
                  // version needs to know WHY there is no button, or it reads as a bug.
                  <span className="shrink-0 text-xs text-muted-foreground">
                    You produced this
                  </span>
                ) : null}
                </div>
                {expanded === v.version && (
                  <div className="mt-2 border-l pl-4">
                    <VersionConsumerList
                      projectId={projectId}
                      phase={phase}
                      version={v.version}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Dialog open={freezing} onOpenChange={setFreezing}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Freeze a {phase} version</DialogTitle>
            <DialogDescription>
              Captures this stage&apos;s current output as an unchangeable version, so
              what {ownerRoleLabel(phase)} signs off cannot change afterwards.
            </DialogDescription>
          </DialogHeader>

          {/* NO DOCUMENT PICKER. Documents used to be ticked here and only became
              readable downstream once a published version covered them — which meant
              approving a document did not, on its own, do anything. Approval is now the
              whole gate, so a document approved on this stage's screen is already
              available to the other agents and has nothing to do with this dialog. */}
          <p className="text-muted-foreground text-xs">
            Approved documents are already readable by the other agents — this freezes
            the stage&apos;s output only.
          </p>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFreezing(false)}>
              Cancel
            </Button>
            <Button disabled={freeze.isPending} onClick={() => freeze.mutate()}>
              {freeze.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              Freeze version
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={rejecting != null}
        onOpenChange={(o) => {
          if (!o) {
            setRejecting(null);
            setReason("");
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Reject v{rejecting}</DialogTitle>
            <DialogDescription>
              The version stays readable so the next run can see what was turned down.
              Say what has to change.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. no acceptance criteria on stories 3 and 7"
            rows={3}
            autoFocus
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setRejecting(null);
                setReason("");
              }}
            >
              Cancel
            </Button>
            {/* Disabled until there IS a reason: an unexplained rejection is
                indistinguishable from a mistake to whoever has to act on it. */}
            <Button
              variant="destructive"
              disabled={!reason.trim()}
              onClick={submitRejection}
            >
              Reject version
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
