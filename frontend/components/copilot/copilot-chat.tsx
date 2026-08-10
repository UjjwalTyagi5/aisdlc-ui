"use client";

import * as React from "react";
import {
  Bot,
  Brain,
  Check,
  ChevronRight,
  Clock,
  FileText,
  Loader2,
  PanelRight,
  Send,
  Square,
  User,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownMessage } from "@/components/app/markdown-message";
import { ThinkingIndicator } from "@/components/app/thinking-indicator";
import { ChoiceCard } from "@/components/copilot/choice-card";
import { GateInline } from "@/components/copilot/gate-inline";
import { ownerRoleLabel, stageLabel } from "@/lib/copilot/stages";
import type { CopilotMessage, CopilotToolActivity } from "@/lib/copilot/use-copilot";
import type { ChoiceCard as ChoiceCardT, GateState } from "@/lib/copilot/types";

export interface CopilotChatProps {
  runId: string;
  messages: CopilotMessage[];
  streaming: boolean;
  activeStage: string;
  choiceCard: ChoiceCardT | null;
  gate: GateState | null;
  error: string | null;
  connState: "idle" | "connecting" | "connected" | "reconnecting" | "closed" | "error";
  onSend: (text: string) => void;
  /** True while a turn is in flight (dispatched or streaming) — drives the Stop affordance. */
  working?: boolean;
  /** Stop the in-flight turn. */
  onStop?: () => void;
  onAnswerChoice: (cardId: string, ids: string[], freeText?: string) => void;
  onGateDecision?: (decision: "approved" | "rejected", stage: string, reason?: string) => void;
  /** Open a specific artifact in the panel (uncollapses it). */
  onOpenArtifact?: (id: string) => void;
}

/**
 * The unified streaming chat thread. Renders agent-attributed bubbles (labeled
 * by the stage that produced them / "Orchestrator"), an inline ChoiceCard when a
 * pick is pending, an inline GateInline when a gate is pending, and a composer.
 *
 * Composer enablement:
 *  - Enabled while interviewing / running (the free-form driver turn).
 *  - When a choice card is pending, the card IS the input — the composer nudges
 *    the user to the card.
 *  - When a cross-role gate is pending and the user can't approve, the composer
 *    is replaced by a clear "waiting on approver" affordance (scroll-back stays).
 */
export function CopilotChat({
  runId,
  messages,
  streaming,
  activeStage,
  choiceCard,
  gate,
  error,
  connState,
  onSend,
  working,
  onStop,
  onAnswerChoice,
  onGateDecision,
  onOpenArtifact,
}: CopilotChatProps) {
  const [text, setText] = React.useState("");
  const bottomRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, streaming, choiceCard?.card_id, gate?.stage, error]);

  const gatePending = gate?.status === "awaiting_gate";
  const awaitingApprover = gatePending && !gate?.can_approve;
  // Cards take priority as the input surface; a cross-role gate blocks the composer.
  const composerEnabled = !awaitingApprover && !choiceCard;

  const submit = React.useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || !composerEnabled) return;
    onSend(trimmed);
    setText("");
  }, [text, composerEnabled, onSend]);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
      {/* Thread */}
      <div className="flex-1 space-y-3 overflow-auto px-4 py-5 md:px-6">
        {connState === "reconnecting" && messages.length > 0 && (
          <div className="border-warning/35 bg-warning/[0.06] text-warning flex items-center gap-2 rounded-[var(--radius)] border px-3.5 py-2 text-[12px]">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            Reconnecting to the Copilot session…
          </div>
        )}

        {messages.length === 0 && !choiceCard && (
          <EmptyThread connState={connState} activeStage={activeStage} />
        )}

        {messages.map((m) =>
          m.card ? (
            <div key={m.id} className="ml-auto max-w-[85%]">
              <ChoiceCard
                card={m.card}
                onAnswer={() => {}}
                readOnly
                answeredIds={m.answeredIds ?? []}
                answeredFreeText={m.answeredIds && m.answeredIds.length ? undefined : m.content}
              />
            </div>
          ) : m.artifactCard ? (
            // A one-time "stage output ready → open in panel" affordance, inline
            // in the thread so it scrolls into history instead of pinning above
            // the composer. The substantive doc lives in the panel, never chat.
            <StageArtifactsCard
              key={m.id}
              stage={m.stage ?? activeStage}
              titles={m.artifactCard.titles}
              firstId={m.artifactCard.firstId}
              onOpen={onOpenArtifact}
            />
          ) : (
            <MessageBubble key={m.id} message={m} />
          ),
        )}

        {/* Pre-bubble working indicator: the instant a turn is dispatched (before
            the first token/tool/thinking event opens the agent bubble — which can
            be 20-30s on a cold tool call), show a live "Thinking… Ns" stub so the
            thread never looks dead/broken while the agent works. */}
        {!!working &&
          !streaming &&
          !messages.some((m) => m.streaming) &&
          !choiceCard &&
          !gatePending &&
          messages[messages.length - 1]?.role === "user" && (
            <div className="animate-in fade-in flex flex-row gap-2.5 duration-200">
              <span className="border-brand-bright/30 bg-brand-bright/10 text-brand-bright mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border [&>svg]:size-3.5">
                <Bot aria-hidden />
              </span>
              <div className="border-line-soft bg-panel-elevated/40 border-l-brand-bright min-w-0 max-w-[85%] rounded-[var(--radius)] border border-l-2 px-3.5 py-2.5">
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="text-muted-foreground font-mono text-[10px] font-semibold uppercase tracking-[0.1em]">
                    Agent
                  </span>
                  <span className="truncate text-[12px] font-semibold text-foreground">
                    {stageLabel(activeStage)}
                  </span>
                </div>
                <ThinkingIndicator label="Thinking" className="text-[12px]" />
              </div>
            </div>
          )}

        {/* Live choice card */}
        {choiceCard && (
          <div className="animate-in fade-in slide-in-from-bottom-1 duration-200">
            <ChoiceCard card={choiceCard} onAnswer={onAnswerChoice} />
          </div>
        )}

        {/* Inline gate */}
        {gatePending && gate && (
          <div className="animate-in fade-in slide-in-from-bottom-1 duration-200">
            <GateInline runId={runId} gate={gate} onDecision={onGateDecision} />
          </div>
        )}

        {/* Error bubble — agent failure surfaces here, not a fake "failed" run */}
        {error && (
          <div className="border-destructive/30 bg-destructive/5 text-destructive rounded-[var(--radius)] border border-l-2 border-l-destructive px-3.5 py-2.5 text-[12.5px]">
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="border-line-soft bg-panel-elevated/40 border-t px-4 py-3 md:px-6">
        {awaitingApprover ? (
          <div className="border-warning/35 bg-warning/[0.06] flex items-center gap-2.5 rounded-[var(--radius)] border px-3.5 py-3">
            <Clock className="text-warning size-4 shrink-0" aria-hidden />
            <p className="text-[12.5px] leading-snug">
              <span className="font-medium text-foreground">
                Waiting on {ownerRoleLabel(gate!.owner_role)}
              </span>{" "}
              <span className="text-muted-foreground">
                to approve {stageLabel(gate!.stage)}. You&apos;ll be able to continue here as soon as
                they decide — scroll up to review the conversation.
              </span>
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={
                choiceCard
                  ? "Answer the card above — or type to reply in words…"
                  : `Message the ${stageLabel(activeStage)} agent…`
              }
              rows={2}
              disabled={!composerEnabled}
              aria-label="Message the Copilot"
              className={cn(
                "border-line-soft resize-none text-[13px]",
                !composerEnabled && "opacity-70",
              )}
            />
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground text-[11px]">
                {streaming ? (
                  <span className="inline-flex items-center gap-1.5">
                    <Loader2 className="size-3 animate-spin" aria-hidden />
                    {stageLabel(activeStage)} is working…
                  </span>
                ) : (
                  "Enter to send · Shift+Enter for a new line"
                )}
              </span>
              {(!!working || streaming) && onStop ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onStop}
                  className="gap-1.5"
                  aria-label="Stop the current turn"
                >
                  <Square className="size-3.5 fill-current" aria-hidden />
                  Stop
                </Button>
              ) : (
                <Button
                  size="sm"
                  onClick={submit}
                  disabled={!text.trim() || !composerEnabled}
                  className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r text-white"
                >
                  <Send className="size-4" aria-hidden />
                  Send
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────

/**
 * The compact "output is ready → open in panel" card. Replaces dumping the
 * substantive stage document into a chat bubble: it summarizes the produced
 * artifacts and opens the panel on click. Rendered once, inline in the thread.
 */
function StageArtifactsCard({
  stage,
  titles,
  firstId,
  onOpen,
}: {
  stage: string;
  titles: string[];
  firstId: string | null;
  onOpen?: (id: string) => void;
}) {
  const summary = titles.slice(0, 6).join(" · ") + (titles.length > 6 ? " · …" : "");
  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 flex gap-2.5 duration-200">
      <span className="border-brand-bright/30 bg-brand-bright/10 text-brand-bright mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border">
        <FileText className="size-3.5" aria-hidden />
      </span>
      <div className="border-line-soft bg-panel-elevated/40 border-l-brand-bright min-w-0 max-w-[85%] rounded-[var(--radius)] border border-l-2 px-3.5 py-2.5">
        <p className="text-[12.5px] font-medium text-foreground">
          <Check className="mr-1 inline size-3.5 align-[-2px] text-emerald-500" aria-hidden />
          {stageLabel(stage)} ready
        </p>
        {summary && (
          <p className="text-muted-foreground mt-0.5 truncate text-[11.5px]">{summary}</p>
        )}
        {firstId && onOpen && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpen(firstId)}
            className="mt-2 h-7 gap-1.5 text-[12px]"
          >
            <PanelRight className="size-3.5" aria-hidden />
            Open in panel
          </Button>
        )}
      </div>
    </div>
  );
}

/** snake_case / camelCase tool id → human label ("list_board_projects" → "List board projects"). */
function humanizeTool(name: string): string {
  const words = name
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The activity trail: one chip per tool the agent called this turn. */
function ToolTrail({ tools }: { tools: CopilotToolActivity[] }) {
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {tools.map((t, i) => (
        <span
          key={`${t.name}-${i}`}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10.5px]",
            t.status === "running"
              ? "border-brand-bright/30 bg-brand-bright/[0.07] text-brand-bright"
              : "border-line-soft bg-panel-elevated/60 text-muted-foreground",
          )}
        >
          {t.status === "running" ? (
            <Loader2 className="size-3 animate-spin" aria-hidden />
          ) : (
            <Check className="size-3 text-emerald-500" aria-hidden />
          )}
          {humanizeTool(t.name)}
        </span>
      ))}
    </div>
  );
}

/**
 * Collapsible extended-thinking panel — collapsed by default, like Claude's
 * "thinking". While live it shows a running timer; once the turn settles it
 * summarizes how long the reasoning took ("Thought for Ns").
 */
function ThinkingPanel({
  text,
  live,
  durationSecs,
}: {
  text: string;
  live: boolean;
  durationSecs: number | null;
}) {
  const [open, setOpen] = React.useState(false);
  const summary = live
    ? "Thinking…"
    : durationSecs != null
      ? `Thought for ${durationSecs}s`
      : "Thought process";
  return (
    <div className="border-line-soft bg-panel-elevated/30 mb-2 rounded-md border border-dashed">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium transition-colors"
      >
        <Brain className={cn("size-3.5", live && "animate-pulse text-brand-bright")} aria-hidden />
        {summary}
        <ChevronRight
          className={cn("ml-auto size-3.5 transition-transform", open && "rotate-90")}
          aria-hidden
        />
      </button>
      {open && (
        <p className="text-muted-foreground border-line-soft whitespace-pre-wrap break-words border-t px-2.5 py-2 font-mono text-[11px] leading-relaxed">
          {text}
        </p>
      )}
    </div>
  );
}

function MessageBubble({ message }: { message: CopilotMessage }) {
  const isUser = message.role === "user";
  const label = isUser ? "You" : stageLabel(message.stage ?? "requirements");
  const hasTools = !isUser && (message.tools?.length ?? 0) > 0;
  const hasThinking = !isUser && !!message.thinking?.trim();
  // While streaming with no visible text yet, show a live "working" indicator so a
  // tool-only / pre-text turn never looks dead.
  const showWorking = !isUser && message.streaming && !message.content.trim();
  const workingLabel = hasTools ? "Working" : "Thinking";
  const time = formatTime(message.createdAt);

  // Measure the reasoning phase so a settled turn can show "Thought for Ns"
  // (Claude-style). Start when thinking first appears; freeze once visible text
  // begins or the stream ends. Replayed (non-streamed) turns stay unmeasured.
  const thinkingStartRef = React.useRef<number | null>(null);
  const [thoughtSecs, setThoughtSecs] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (isUser) return;
    if (hasThinking && thinkingStartRef.current == null) {
      thinkingStartRef.current = Date.now();
    }
    const started = thinkingStartRef.current;
    if (started != null && thoughtSecs == null) {
      const settled = !!message.content.trim() || !message.streaming;
      if (settled) setThoughtSecs(Math.max(1, Math.round((Date.now() - started) / 1000)));
    }
  }, [isUser, hasThinking, message.content, message.streaming, thoughtSecs]);

  return (
    <div className={cn("flex gap-2.5", isUser ? "flex-row-reverse" : "flex-row")}>
      <span
        className={cn(
          "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border [&>svg]:size-3.5",
          isUser
            ? "border-line-soft bg-panel-elevated/70 text-muted-foreground"
            : "border-brand-bright/30 bg-brand-bright/10 text-brand-bright",
        )}
      >
        {isUser ? <User aria-hidden /> : <Bot aria-hidden />}
      </span>
      <div
        className={cn(
          "min-w-0 max-w-[85%] rounded-[var(--radius)] border px-3.5 py-2.5",
          isUser
            ? "border-line-soft bg-panel-elevated/60"
            : "border-line-soft bg-panel-elevated/40 border-l-2 border-l-brand-bright",
        )}
      >
        <div className="mb-1.5 flex items-center gap-2">
          <span className="text-muted-foreground font-mono text-[10px] font-semibold uppercase tracking-[0.1em]">
            {isUser ? "Driver" : "Agent"}
          </span>
          <span className="truncate text-[12px] font-semibold text-foreground">{label}</span>
          {time && (
            <span className="text-muted-foreground/70 ml-auto shrink-0 font-mono text-[10px] tabular-nums">
              {time}
            </span>
          )}
        </div>

        {hasThinking && (
          <ThinkingPanel
            text={message.thinking!}
            live={!!message.streaming}
            durationSecs={thoughtSecs}
          />
        )}
        {hasTools && <ToolTrail tools={message.tools!} />}

        {isUser ? (
          <p className="text-muted-foreground whitespace-pre-wrap break-words text-[12.5px] leading-relaxed">
            {message.content}
          </p>
        ) : showWorking ? (
          <ThinkingIndicator label={workingLabel} className="text-[12px]" />
        ) : (
          <div className="text-[12.5px] text-foreground/90">
            <MarkdownMessage content={message.content} />
            {message.streaming && <ThinkingIndicator label="Working" className="mt-1.5 text-[12px]" />}
          </div>
        )}
      </div>
    </div>
  );
}

/** Wall-clock HH:MM for a message timestamp; empty string if unparseable. */
function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function EmptyThread({
  connState,
  activeStage,
}: {
  connState: string;
  activeStage: string;
}) {
  const reconnecting = connState === "reconnecting";
  const connecting = connState === "connecting" || connState === "idle" || reconnecting;
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <span className="border-brand-bright/30 bg-brand-bright/10 text-brand-bright mx-auto mb-4 flex size-12 items-center justify-center rounded-full border">
        {connecting ? (
          <Loader2 className="size-5 animate-spin" aria-hidden />
        ) : (
          <Bot className="size-5" aria-hidden />
        )}
      </span>
      <h2 className="font-display text-[15px] font-bold">
        {reconnecting
          ? "Reconnecting…"
          : connecting
            ? "Opening the Copilot…"
            : `${stageLabel(activeStage)} is ready`}
      </h2>
      <p className="text-muted-foreground mx-auto mt-1.5 max-w-xs text-[12.5px] leading-relaxed">
        {reconnecting
          ? "The connection dropped — reattaching to the pipeline session."
          : connecting
            ? "Connecting to the pipeline session."
            : "Say hello, or tell the agent what you want to work on. It'll interview you and surface choice cards for picks."}
      </p>
    </div>
  );
}
