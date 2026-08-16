"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Loader2, Send, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/ui/status-badge";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { ApprovalCard } from "@/components/app/approval-card";
import { ClarificationCard } from "@/components/app/clarification-card";
import { AGENT_LABEL } from "@/lib/agents";
import { StreamEvent, type ApprovalDecision, type RunId } from "@/lib/schemas";
import { useSse } from "@/lib/stream/use-sse";
import { bubbleFromRunEvent, type RunBubble } from "@/lib/stream/run-event-bubble";
import { advanceCopilotRun, cancelRun, getRun } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import { approvePermissionForPhase, hasPermission } from "@/lib/auth/permissions";
import { useSession } from "@/hooks/use-session";

const ACTIVE_STATUSES = [
  "queued",
  "running",
  "awaiting_approval",
  "awaiting_clarification",
];

/**
 * Run Conversation surface — a chat-shaped column bound to a `runId`.
 *
 * Renders the run's per-run SSE events as message bubbles, drops the inline
 * gate/clarification cards when the run is awaiting a human, and exposes a
 * composer that is enabled *only* when a clarification is pending (its text
 * becomes the answer). It talks to the RUN via the existing run SSE
 * (`/api/runs/[id]/stream`) + signals — NOT the legacy agent WebSocket.
 *
 * The card + signal wiring mirrors `RunDetailDrawer` (the reference impl):
 * approval/clarification both fall back to `runId` as the correlation key
 * (D-08), since the discrete artifact/clarification ids aren't yet fields on
 * the Run schema.
 */
export function RunConversation({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const session = useSession();

  const id = runId as RunId;

  const runQ = useQuery({
    queryKey: qk.runs.detail(id),
    queryFn: () => getRun(id),
    // Poll while active so status transitions (→ awaiting_*) surface the
    // right inline card even if the SSE-driven invalidation lags.
    refetchInterval: (q) =>
      q.state.data && ACTIVE_STATUSES.includes(q.state.data.status) ? 5_000 : false,
  });
  const run = runQ.data;

  // ── Per-run SSE → chat bubbles ──────────────────────────────────────────
  const enabled = process.env.NEXT_PUBLIC_DISABLE_STREAMS !== "1";
  const [bubbles, setBubbles] = React.useState<RunBubble[]>([]);
  const [feedState, setFeedState] = React.useState<
    "idle" | "connecting" | "connected" | "reconnecting" | "offline"
  >("idle");

  const handleEvent = React.useCallback((raw: unknown) => {
    const parsed = StreamEvent.safeParse(raw);
    if (!parsed.success) return;
    const bubble = bubbleFromRunEvent(parsed.data);
    if (!bubble) return;
    // Append (oldest→newest) so the column reads top-to-bottom like a chat.
    setBubbles((prev) =>
      // Dedupe on id to survive SSE reconnect replays.
      prev.some((b) => b.id === bubble.id) ? prev : [...prev, bubble],
    );
  }, []);

  useSse({
    url: enabled ? `/api/runs/${encodeURIComponent(runId)}/stream` : null,
    onEvent: handleEvent,
    onState: setFeedState,
    maxAttempts: 5,
  });

  // Auto-scroll to newest bubble.
  const bottomRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [bubbles.length]);

  // ── Signal wiring — mirrors RunDetailDrawer ─────────────────────────────
  const [approvalPending, setApprovalPending] = React.useState(false);
  const [pendingDecision, setPendingDecision] =
    React.useState<ApprovalDecision | null>(null);
  const [clarificationPending, setClarificationPending] = React.useState(false);

  const handleApprove = React.useCallback(async () => {
    if (!run) return;
    setApprovalPending(true);
    setPendingDecision("approve");
    try {
      await advanceCopilotRun(run.id, { decision: "approved", stage: run.phase });
      await queryClient.invalidateQueries({ queryKey: qk.runs.detail(run.id) });
    } finally {
      setApprovalPending(false);
      setPendingDecision(null);
    }
  }, [run, queryClient]);

  const handleReject = React.useCallback(
    async (reason: string) => {
      if (!run) return;
      setApprovalPending(true);
      setPendingDecision("reject");
      try {
        await advanceCopilotRun(run.id, {
          decision: "rejected",
          stage: run.phase,
          reason,
        });
        await queryClient.invalidateQueries({ queryKey: qk.runs.detail(run.id) });
      } finally {
        setApprovalPending(false);
        setPendingDecision(null);
      }
    },
    [run, queryClient],
  );

  const handleAnswer = React.useCallback(
    async (answer: string) => {
      if (!run) return;
      setClarificationPending(true);
      try {
        // The answer rides as the gate's reason — one endpoint moves a run,
        // whatever its gate was asking.
        await advanceCopilotRun(run.id, {
          decision: "approved",
          stage: run.phase,
          reason: answer,
        });
        await queryClient.invalidateQueries({ queryKey: qk.runs.detail(run.id) });
      } finally {
        setClarificationPending(false);
      }
    },
    [run, queryClient],
  );

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

  // ── RBAC gates — mirror RunDetailDrawer exactly ─────────────────────────
  const approvalPermission = run?.phase
    ? approvePermissionForPhase(run.phase)
    : null;
  const canApprove = approvalPermission
    ? hasPermission(session, approvalPermission)
    : false;
  const canAnswer = canApprove;

  const isAwaitingClarification = run?.status === "awaiting_clarification";
  const isAwaitingApproval = run?.status === "awaiting_approval";
  const isActive = !!run && ACTIVE_STATUSES.includes(run.status);

  // ── Composer state — enabled ONLY when a clarification is pending ────────
  const [composerText, setComposerText] = React.useState("");
  const composerEnabled = isAwaitingClarification && canAnswer;

  const submitComposer = React.useCallback(async () => {
    const trimmed = composerText.trim();
    if (!trimmed || !composerEnabled) return;
    await handleAnswer(trimmed);
    setComposerText("");
  }, [composerText, composerEnabled, handleAnswer]);

  // ── Render ──────────────────────────────────────────────────────────────
  if (runQ.isLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 md:p-8">
        <LoadingState variant="list" rows={5} />
      </div>
    );
  }

  if (runQ.isError || !run) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 md:p-8">
        <ApiErrorState
          title="Couldn't load run"
          description={
            runQ.error instanceof Error ? runQ.error.message : "Unknown error."
          }
          onRetry={() => runQ.refetch()}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-var(--app-header-h,3.5rem))] w-full max-w-3xl flex-col">
      {/* ── Header ── */}
      <header className="border-line-soft bg-panel-elevated/60 flex items-center gap-3 border-b px-4 py-3 backdrop-blur-sm md:px-6">
        <Button variant="ghost" size="icon" asChild className="-ml-2 size-8 shrink-0">
          <Link href={`/runs/${run.id}`} aria-label="Open full run view">
            <ArrowLeft className="size-4" aria-hidden />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="font-display truncate text-[15px] font-bold leading-tight">
            {run.title}
          </h1>
          <p className="text-muted-foreground truncate font-mono text-[11px]">
            {run.id} · {AGENT_LABEL[run.agent]}
          </p>
        </div>
        <StatusBadge status={run.status} />
        {isActive && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
            aria-busy={cancelMutation.isPending}
            className="border-line-soft h-8 shrink-0 text-xs"
          >
            {cancelMutation.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <XCircle className="size-3.5" aria-hidden />
            )}
            Cancel
          </Button>
        )}
      </header>

      {/* ── Message stream ── */}
      <div className="flex-1 space-y-3 overflow-auto px-4 py-5 md:px-6">
        {bubbles.length === 0 ? (
          <p className="text-muted-foreground py-8 text-center font-mono text-[11px]">
            {!enabled
              ? "Streams disabled (NEXT_PUBLIC_DISABLE_STREAMS=1)"
              : feedState === "connecting" || feedState === "idle"
                ? "Connecting to the run…"
                : "Waiting for the run to report progress…"}
          </p>
        ) : (
          bubbles.map((b) => <MessageBubble key={b.id} bubble={b} />)
        )}

        {/* Inline clarification card (REQ-M10-06) */}
        {isAwaitingClarification && (
          <ClarificationCard
            questions={run.clarificationQuestions ?? []}
            clarificationId={run.id}
            deadline={run.clarificationDeadline ?? undefined}
            onSubmit={handleAnswer}
            pending={clarificationPending}
            canAnswer={canAnswer}
          />
        )}

        {/* Inline approval gate card (D-04) */}
        {isAwaitingApproval && (
          <ApprovalCard
            status={run.status}
            title="Gate: Approval required"
            description={
              <span>
                This run is paused at an approval gate
                {run.pendingApprovers && run.pendingApprovers.length > 0 && (
                  <>
                    {" — awaiting "}
                    <span className="font-mono text-xs">
                      {run.pendingApprovers.join(", ")}
                    </span>
                  </>
                )}
                .
              </span>
            }
            onApprove={canApprove ? handleApprove : undefined}
            onReject={canApprove ? handleReject : undefined}
            pending={approvalPending}
            pendingDecision={pendingDecision}
          />
        )}

        <div ref={bottomRef} />
      </div>

      {/* ── Composer — enabled ONLY when a clarification is pending ── */}
      <div className="border-line-soft bg-panel-elevated/40 border-t px-4 py-3 md:px-6">
        {composerEnabled ? (
          <div className="space-y-2">
            <Textarea
              autoFocus
              placeholder="Type your answer for the agent…"
              value={composerText}
              onChange={(e) => setComposerText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void submitComposer();
                }
              }}
              rows={2}
              disabled={clarificationPending}
              aria-label="Clarification answer"
            />
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-[11px]">
                The agent is waiting on your answer. ⌘/Ctrl+Enter to send.
              </span>
              <Button
                size="sm"
                onClick={() => void submitComposer()}
                disabled={!composerText.trim() || clarificationPending}
                aria-busy={clarificationPending}
                className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r text-white"
              >
                {clarificationPending ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <Send className="size-4" aria-hidden />
                )}
                Send answer
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Textarea
              placeholder="The run will ask here when it needs a decision."
              rows={2}
              disabled
              aria-label="Composer (disabled — no clarification pending)"
              className="resize-none opacity-70"
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────

const TONE_ACCENT: Record<RunBubble["tone"], string> = {
  brand: "border-l-brand-bright",
  info: "border-l-info",
  success: "border-l-success",
  warning: "border-l-warning",
  danger: "border-l-destructive",
};

const ROLE_LABEL: Record<RunBubble["role"], string> = {
  agent: "Agent",
  orchestrator: "Orchestrator",
  system: "System",
};

function MessageBubble({ bubble }: { bubble: RunBubble }) {
  const muted = bubble.role === "system";
  return (
    <div
      className={cn(
        "border-line-soft bg-panel-elevated/40 rounded-[var(--radius)] border border-l-2 px-3.5 py-2.5",
        TONE_ACCENT[bubble.tone],
        muted && "opacity-80",
      )}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-muted-foreground font-mono text-[10px] font-semibold uppercase tracking-[0.1em]">
            {ROLE_LABEL[bubble.role]}
          </span>
          <span className="text-foreground truncate text-[12px] font-semibold">
            {bubble.label}
          </span>
        </div>
        <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
          {new Date(bubble.at).toLocaleTimeString(undefined, {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })}
        </span>
      </div>
      {bubble.text && (
        <p className="text-muted-foreground whitespace-pre-wrap break-words text-[12.5px] leading-relaxed">
          {bubble.text}
        </p>
      )}
    </div>
  );
}
