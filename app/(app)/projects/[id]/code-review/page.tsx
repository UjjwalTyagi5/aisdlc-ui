"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  Copy,
  FileDiff,
  GitBranch,
  GitPullRequest,
  ListChecks,
  MessageSquare,
  ScrollText,
  Sparkles,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { ReviewTargetDialog } from "@/components/app/review-target-dialog";
import { RequireRole } from "@/components/auth/require-role";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useSession } from "@/hooks/use-session";
import { getProject } from "@/lib/api/projects";
import { listReviews, getReview } from "@/lib/api/code-review";
import { qk } from "@/lib/api/query-keys";
import type { PrepareResult, Severity, CodeReviewArtifact } from "@/lib/schemas/code-review";
import type { ProjectId } from "@/lib/schemas";

type Tab = "summary" | "findings" | "diff";

const REC_META: Record<string, { label: string; cls: string }> = {
  approve: { label: "Approve", cls: "bg-success/15 text-success border-success/30" },
  request_changes: {
    label: "Request changes",
    cls: "bg-destructive/15 text-destructive border-destructive/30",
  },
  needs_discussion: {
    label: "Needs discussion",
    cls: "bg-warning/15 text-warning border-warning/30",
  },
};

const SEV_META: Record<Severity, { label: string; dot: string; text: string }> = {
  critical: { label: "Critical", dot: "bg-destructive", text: "text-destructive" },
  high: { label: "High", dot: "bg-orange-500", text: "text-orange-500" },
  medium: { label: "Medium", dot: "bg-warning", text: "text-warning" },
  low: { label: "Low", dot: "bg-sky-500", text: "text-sky-500" },
  info: { label: "Info", dot: "bg-muted-foreground", text: "text-muted-foreground" },
};
const SEV_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];

export default function CodeReviewPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const queryClient = useQueryClient();
  useSession({ required: true });

  const projectQ = useQuery({
    queryKey: qk.projects.detail(id),
    queryFn: () => getProject(id),
  });

  const reviewsQ = useQuery({
    queryKey: qk.codeReview.reviews(id),
    queryFn: () => listReviews(id),
  });

  const [tab, setTab] = React.useState<Tab>("summary");
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [chatOpen, setChatOpen] = React.useState(false);
  const [prepared, setPrepared] = React.useState<PrepareResult | null>(null);
  const [activeReviewId, setActiveReviewId] = React.useState<string | null>(null);

  // Default to the most recent review once they load (unless a fresh diff is staged).
  React.useEffect(() => {
    const newest = reviewsQ.data?.[0]?.id;
    if (!prepared && !activeReviewId && newest) {
      setActiveReviewId(newest);
    }
  }, [reviewsQ.data, prepared, activeReviewId]);

  const reviewQ = useQuery({
    queryKey: qk.codeReview.review(id, activeReviewId ?? ""),
    queryFn: () => getReview(id, activeReviewId!),
    enabled: !!activeReviewId,
  });

  const chat = useAgentChat({
    agent: "code_review",
    context: { page: "Code Review", project_id: id },
    onArtifact: () => reviewsQ.refetch(),
  });

  // When a review run finishes (busy → idle), refresh the list and jump to the newest.
  const prevBusy = React.useRef(chat.busy);
  React.useEffect(() => {
    if (prevBusy.current && !chat.busy) {
      reviewsQ.refetch().then((r) => {
        const newest = r.data?.[0]?.id;
        if (newest) {
          setActiveReviewId(newest);
          setPrepared(null);
          setTab("summary");
          queryClient.invalidateQueries({ queryKey: qk.codeReview.review(id, newest) });
        }
      });
    }
    prevBusy.current = chat.busy;
  }, [chat.busy, id, queryClient, reviewsQ]);

  const onPrepared = (result: PrepareResult) => {
    setPrepared(result);
    setActiveReviewId(null);
    setTab("diff");
  };

  const runReview = () => {
    setChatOpen(true);
    void chat.send("Please review the prepared change and submit your findings.");
  };

  if (projectQ.isLoading) {
    return (
      <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
        <div className="bg-muted h-8 w-64 animate-pulse rounded" />
        <LoadingState variant="card" />
      </div>
    );
  }
  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState
          title="Project not found"
          description={projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."}
          onRetry={() => projectQ.refetch()}
        />
      </div>
    );
  }

  // The artifact currently displayed: a saved review, else just the prepared diff.
  const artifact = reviewQ.data ?? null;
  const reviews = reviewsQ.data ?? [];
  const ctx = artifact?.context;

  // Target chip text.
  const targetChip = (() => {
    if (prepared) {
      return prepared.mode === "pr"
        ? `PR #${prepared.pr_id} · ${prepared.source_branch} → ${prepared.base_branch}`
        : `${prepared.source_branch} → ${prepared.base_branch}`;
    }
    if (ctx) {
      return ctx.mode === "pr"
        ? `PR #${ctx.pr_id} · ${ctx.source_branch} → ${ctx.base_branch}`
        : `${ctx.source_branch} → ${ctx.base_branch}`;
    }
    return null;
  })();
  const repoName = prepared?.repo_name ?? ctx?.repo_name ?? "";
  const diffText = prepared?.diff ?? artifact?.diff ?? "";
  const hasReview = !!artifact;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ── Header ───────────────────────────────────────── */}
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">Code Review</h1>
            <p className="text-muted-foreground text-xs">
              {targetChip ? (
                <span className="inline-flex items-center gap-1">
                  {prepared?.mode === "pr" || ctx?.mode === "pr" ? (
                    <GitPullRequest className="size-3" aria-hidden />
                  ) : (
                    <GitBranch className="size-3" aria-hidden />
                  )}
                  <span className="font-mono">{repoName}</span>
                  <span className="opacity-50">·</span>
                  <span className="font-mono">{targetChip}</span>
                </span>
              ) : (
                <span>Read-only review of a branch diff or a pull request.</span>
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {reviews.length > 0 && (
              <ReviewSwitcher
                reviews={reviews}
                activeId={activeReviewId}
                onSelect={(rid) => {
                  setActiveReviewId(rid);
                  setPrepared(null);
                  setTab("summary");
                }}
              />
            )}
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
              <FileDiff className="size-4" aria-hidden />
              Select target
            </Button>
            <RequireRole capability="run:trigger">
              <Button size="sm" onClick={runReview} disabled={!prepared || chat.busy}>
                <Sparkles className="size-4" aria-hidden />
                Run review
              </Button>
            </RequireRole>
            <Button variant="outline" size="sm" onClick={() => setChatOpen(true)}>
              <MessageSquare className="size-4" aria-hidden />
              Chat
            </Button>
          </div>
        </div>
      </div>

      {/* ── Body: tabs + content ─────────────────────────── */}
      {!prepared && !hasReview && !reviewsQ.isLoading ? (
        <div className="flex-1 overflow-auto">
          <div className="mx-auto max-w-xl px-4 py-12">
            <EmptyState
              icon={FileDiff}
              title="No review yet"
              description="Select a branch-vs-base diff or an open PR, then run the review. Findings, a summary, and a merge recommendation appear here."
              action={
                <Button onClick={() => setPickerOpen(true)}>
                  <FileDiff className="size-4" aria-hidden />
                  Select target
                </Button>
              }
            />
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          {/* Tab bar */}
          <div className="flex items-center gap-1 border-b px-2 py-1.5">
            <TabBtn active={tab === "summary"} onClick={() => setTab("summary")} icon={ScrollText}>
              Summary
            </TabBtn>
            <TabBtn active={tab === "findings"} onClick={() => setTab("findings")} icon={ListChecks}>
              Findings
              {artifact && artifact.findings.length > 0 && (
                <span className="bg-muted text-muted-foreground ml-1 rounded-full px-1.5 text-[10px]">
                  {artifact.findings.length}
                </span>
              )}
            </TabBtn>
            <TabBtn active={tab === "diff"} onClick={() => setTab("diff")} icon={FileDiff}>
              Diff
            </TabBtn>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            {reviewQ.isLoading && activeReviewId ? (
              <LoadingState variant="card" />
            ) : tab === "summary" ? (
              <SummaryView artifact={artifact} onRun={runReview} canRun={!!prepared && !chat.busy} busy={chat.busy} />
            ) : tab === "findings" ? (
              <FindingsView artifact={artifact} onJump={() => setTab("diff")} />
            ) : (
              <DiffView diff={diffText} files={prepared?.files ?? null} />
            )}
          </div>
        </div>
      )}

      <ReviewTargetDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        projectId={id}
        onPrepared={onPrepared}
      />

      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{ page: "Code Review", artifactTitle: targetChip ?? undefined }}
        messages={chat.messages}
        onSend={chat.send}
        busy={chat.busy}
        disabledReason={
          prepared || hasReview
            ? undefined
            : "Select a branch or PR to review first (Select target)."
        }
        starterSuggestions={[
          "Review the prepared change and submit your findings.",
          "Focus on security and error handling.",
          "Is this change safe to merge?",
        ]}
      />
    </div>
  );
}

// ── Sub-views ─────────────────────────────────────────────

function TabBtn({
  active,
  onClick,
  icon: Icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/50",
      )}
    >
      <Icon className="size-4" aria-hidden />
      {children}
    </button>
  );
}

function ReviewSwitcher({
  reviews,
  activeId,
  onSelect,
}: {
  reviews: { id: string; label: string; merge_recommendation: string; findings_count: number; created_at: string }[];
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const active = reviews.find((r) => r.id === activeId);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="max-w-[220px]">
          <ScrollText className="size-4 shrink-0" aria-hidden />
          <span className="truncate">{active ? active.label : "Past reviews"}</span>
          <ChevronDown className="size-3.5 shrink-0 opacity-60" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel>Past reviews</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {reviews.map((r) => (
          <DropdownMenuItem key={r.id} onClick={() => onSelect(r.id)} className="flex-col items-start gap-0.5">
            <span className="flex w-full items-center justify-between gap-2">
              <span className="truncate font-mono text-xs">{r.label}</span>
              <Badge variant="outline" className={cn("text-[10px]", REC_META[r.merge_recommendation]?.cls)}>
                {REC_META[r.merge_recommendation]?.label ?? r.merge_recommendation}
              </Badge>
            </span>
            <span className="text-muted-foreground text-[10px]">
              {r.findings_count} findings · {new Date(r.created_at).toLocaleString()}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SummaryView({
  artifact,
  onRun,
  canRun,
  busy,
}: {
  artifact: CodeReviewArtifact | null;
  onRun: () => void;
  canRun: boolean;
  busy: boolean;
}) {
  if (!artifact) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState
          icon={Sparkles}
          title={busy ? "Reviewing…" : "Diff ready — run the review"}
          description={
            busy
              ? "The agent is analyzing the change. Findings and a recommendation will appear here."
              : "The diff is staged in the Diff tab. Run the review to generate findings, a summary, and a merge recommendation."
          }
          action={
            canRun ? (
              <Button onClick={onRun}>
                <Sparkles className="size-4" aria-hidden />
                Run review
              </Button>
            ) : undefined
          }
        />
      </div>
    );
  }
  const rec = REC_META[artifact.merge_recommendation];
  const m = artifact.metrics;
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant="outline" className={cn("border px-3 py-1 text-sm", rec?.cls)}>
          {rec?.label ?? artifact.merge_recommendation}
        </Badge>
        <span className="text-muted-foreground text-xs">
          {artifact.findings.length} findings ·{" "}
          {artifact.findings.filter((f) => f.severity === "critical" || f.severity === "high").length} critical/high
        </span>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Files" value={`${m.files_changed}`} />
        <Metric label="Added" value={`+${m.added}`} tone="text-success" />
        <Metric label="Removed" value={`-${m.removed}`} tone="text-destructive" />
        <Metric
          label="Δ complexity"
          value={m.complexity_delta != null ? `${m.complexity_delta > 0 ? "+" : ""}${m.complexity_delta}` : "—"}
        />
      </div>

      {/* Summary markdown (rendered as readable prose) */}
      {artifact.summary && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Review summary</h3>
          <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap rounded-lg border bg-surface-1 p-4 text-sm leading-relaxed">
            {artifact.summary}
          </div>
        </section>
      )}

      {artifact.requirements_coverage.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Requirements coverage</h3>
          <ul className="space-y-1.5">
            {artifact.requirements_coverage.map((c, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <Badge variant="outline" className="text-[10px]">{c.status}</Badge>
                <span className="font-mono text-xs">{c.ac_id}</span>
                {c.note && <span className="text-muted-foreground truncate">— {c.note}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {artifact.design_conformance.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Design conformance</h3>
          <ul className="space-y-1.5">
            {artifact.design_conformance.map((c, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <Badge variant="outline" className="text-[10px]">{c.status}</Badge>
                <span className="truncate">{c.rule}</span>
                {c.note && <span className="text-muted-foreground truncate">— {c.note}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border bg-surface-1 p-3">
      <div className={cn("font-mono text-lg font-semibold", tone)}>{value}</div>
      <div className="text-muted-foreground text-[10px] uppercase tracking-wider">{label}</div>
    </div>
  );
}

function FindingsView({
  artifact,
  onJump,
}: {
  artifact: CodeReviewArtifact | null;
  onJump: () => void;
}) {
  if (!artifact) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={ListChecks} title="No findings yet" description="Run the review to generate findings." variant="plain" />
      </div>
    );
  }
  if (artifact.findings.length === 0) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={Check} title="No issues found" description="The agent found nothing worth flagging in this change." variant="plain" />
      </div>
    );
  }
  const grouped = SEV_ORDER.map((sev) => ({
    sev,
    items: artifact.findings.filter((f) => f.severity === sev),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      {grouped.map((g) => (
        <section key={g.sev} className="space-y-2">
          <div className="flex items-center gap-2">
            <span className={cn("size-2 rounded-full", SEV_META[g.sev].dot)} aria-hidden />
            <h3 className={cn("text-xs font-semibold uppercase tracking-wider", SEV_META[g.sev].text)}>
              {SEV_META[g.sev].label} ({g.items.length})
            </h3>
          </div>
          <ul className="space-y-2">
            {g.items.map((f) => (
              <FindingCard key={f.id} finding={f} onJump={onJump} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function FindingCard({
  finding,
  onJump,
}: {
  finding: {
    id: string;
    severity: Severity;
    category: string;
    file: string;
    line: number;
    description: string;
    recommendation: string;
    autofix_patch?: string | null;
  };
  onJump: () => void;
}) {
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    if (!finding.autofix_patch) return;
    void navigator.clipboard.writeText(finding.autofix_patch).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <li className="rounded-lg border bg-surface-1 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] text-muted-foreground">{finding.id}</span>
            <Badge variant="outline" className="text-[10px]">{finding.category}</Badge>
            {finding.file && (
              <button
                type="button"
                onClick={onJump}
                className="text-brand-bright font-mono text-[11px] hover:underline"
                title="Show in diff"
              >
                {finding.file}
                {finding.line ? `:${finding.line}` : ""}
              </button>
            )}
          </div>
          <p className="text-sm">{finding.description}</p>
          {finding.recommendation && (
            <p className="text-muted-foreground text-xs">
              <span className="font-medium">Fix:</span> {finding.recommendation}
            </p>
          )}
        </div>
        {finding.autofix_patch && (
          <Button variant="outline" size="sm" onClick={copy} className="shrink-0">
            {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
            {copied ? "Copied" : "Copy fix"}
          </Button>
        )}
      </div>
      {finding.autofix_patch && (
        <pre className="mt-2 max-h-48 overflow-auto rounded-md border bg-surface-2 p-2 font-mono text-[11px] leading-relaxed">
          {finding.autofix_patch}
        </pre>
      )}
    </li>
  );
}

/** Split a unified diff into one block per file: {path, added, removed, body}. */
function parseDiffFiles(diff: string): { path: string; body: string }[] {
  const blocks: { path: string; body: string }[] = [];
  const lines = diff.split("\n");
  let cur: { path: string; body: string[] } | null = null;
  for (const ln of lines) {
    if (ln.startsWith("diff --git")) {
      if (cur) blocks.push({ path: cur.path, body: cur.body.join("\n") });
      const m = ln.match(/ b\/(.+)$/);
      cur = { path: m ? m[1]! : "file", body: [] };
    } else if (cur) {
      cur.body.push(ln);
    }
  }
  if (cur) blocks.push({ path: cur.path, body: cur.body.join("\n") });
  return blocks;
}

function DiffView({
  diff,
  files,
}: {
  diff: string;
  files: PrepareResult["files"] | null;
}) {
  const blocks = React.useMemo(() => parseDiffFiles(diff), [diff]);
  const slug = (p: string) => "diff-" + p.replace(/[^a-z0-9]/gi, "-");

  if (!diff) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={FileDiff} title="No diff" description="Select a target to compute the diff." variant="plain" />
      </div>
    );
  }

  const statByPath = new Map((files ?? []).map((f) => [f.path, f]));
  const jump = (path: string) =>
    document.getElementById(slug(path))?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="flex min-h-0 flex-col">
      {/* Helper line + clickable file chips */}
      <div className="space-y-2 border-b px-3 py-2">
        <p className="text-muted-foreground text-xs">
          {blocks.length} file{blocks.length === 1 ? "" : "s"} changed on this branch vs base — green is added, red is removed. Click a file to jump to it.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {blocks.map((b) => {
            const st = statByPath.get(b.path);
            return (
              <button
                key={b.path}
                type="button"
                onClick={() => jump(b.path)}
                className="border-line-soft bg-surface-1 hover:bg-surface-2 inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[10px] transition-colors"
                title={b.path}
              >
                {st && (
                  <span
                    className={cn(
                      "font-semibold",
                      st.status === "A" && "text-success",
                      st.status === "D" && "text-destructive",
                      st.status === "M" && "text-warning",
                    )}
                  >
                    {st.status}
                  </span>
                )}
                <span className="max-w-[260px] truncate">{b.path.split("/").pop()}</span>
                {st && (
                  <span className="text-muted-foreground">
                    <span className="text-success">+{st.added}</span>{" "}
                    <span className="text-destructive">-{st.removed}</span>
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Per-file diff sections */}
      <div className="min-h-0 flex-1 overflow-auto">
        {blocks.map((b) => {
          const st = statByPath.get(b.path);
          return (
            <section key={b.path} id={slug(b.path)} className="border-b last:border-b-0">
              <header className="bg-surface-2/60 sticky top-0 z-10 flex items-center justify-between gap-2 border-b px-3 py-1.5 backdrop-blur">
                <span className="flex min-w-0 items-center gap-2">
                  <FileDiff className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                  <span className="truncate font-mono text-xs font-medium">{b.path}</span>
                </span>
                {st && (
                  <span className="shrink-0 font-mono text-[10px]">
                    <span className="text-success">+{st.added}</span>{" "}
                    <span className="text-destructive">-{st.removed}</span>
                  </span>
                )}
              </header>
              <pre className="font-mono text-[12px] leading-[1.5]">
                {b.body.split("\n").map((ln, i) => {
                  const isAdd = ln.startsWith("+") && !ln.startsWith("+++");
                  const isDel = ln.startsWith("-") && !ln.startsWith("---");
                  const isHunk = ln.startsWith("@@");
                  const isMeta =
                    ln.startsWith("index ") ||
                    ln.startsWith("+++") ||
                    ln.startsWith("---") ||
                    ln.startsWith("new file") ||
                    ln.startsWith("deleted file") ||
                    ln.startsWith("similarity ") ||
                    ln.startsWith("rename ");
                  if (isMeta) return null; // hide git plumbing — keep the human-readable change
                  return (
                    <div
                      key={i}
                      className={cn(
                        "px-3",
                        isAdd && "bg-success/10 text-success",
                        isDel && "bg-destructive/10 text-destructive",
                        isHunk && "bg-brand-bright/10 text-brand-bright",
                      )}
                    >
                      {ln || " "}
                    </div>
                  );
                })}
              </pre>
            </section>
          );
        })}
      </div>
    </div>
  );
}
