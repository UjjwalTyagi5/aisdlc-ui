"use client";

import * as React from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Paperclip,
  Plus,
  SendHorizontal,
  Sparkles,
  User,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MarkdownMessage } from "@/components/app/markdown-message";
import { ThinkingIndicator } from "@/components/app/thinking-indicator";
import { DiffViewer } from "@/components/app/diff-viewer";

/**
 * UI-only in Chunk 6. Chunk 15 wires Vercel AI SDK `useChat` for real streaming.
 * The `onSend` callback receives the raw text so the hosting page can drive
 * messages however it wants (mock → REST → SSE etc).
 */
export interface AgentChatMessage {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  createdAt: string;
  /** When true, a blinking cursor is appended — simulates in-flight token stream. */
  streaming?: boolean;
  /** Tool calls observed during the message's stream. */
  toolCalls?: Array<{
    id: string;
    name: string;
    args: Record<string, unknown>;
    status: "completed" | "running" | "failed";
  }>;
  /** Artifact ids cited inline by the agent. */
  citations?: string[];
  /** Files attached to this turn — rendered as download links under the bubble. */
  attachments?: Array<{ name: string; url: string }>;
  /** Follow-up prompt chips offered after an agent turn (click → sends as a new message). */
  suggestions?: string[];
  /**
   * File diff cards produced this turn (from `code.diff` StreamEvents), one
   * entry per path — the net original→modified change across the whole turn,
   * not each intermediate write/edit_file call. Rendered as its own item in
   * the timeline, not merged into the text bubble.
   */
  diffs?: Array<{
    path: string;
    original: string;
    modified: string;
    changeKind: "created" | "edited";
  }>;
}

export interface AgentChatDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Shown as a chip at the top of the drawer. */
  context?: {
    page: string;
    artifactTitle?: string;
  };
  messages: readonly AgentChatMessage[];
  onSend: (text: string) => void | Promise<void>;
  /** Disables input (e.g. while a response streams). */
  busy?: boolean;
  /** Slash-command suggestions shown when input is empty + starts with "/". */
  slashCommands?: Array<{ command: string; description: string }>;
  // ── Session rail (optional; renders a left history rail when onNewChat is set) ──
  sessions?: ReadonlyArray<{ id: string; title: string; updated_at?: string | null }>;
  activeSessionId?: string;
  onSelectSession?: (id: string) => void;
  onNewChat?: () => void;
  // ── Attachments (optional) ──
  /** Files staged for the next send (chips above the composer). */
  attachments?: ReadonlyArray<{ name: string; url: string }>;
  onAttachFiles?: (files: File[]) => void;
  onRemoveAttachment?: (name: string) => void;
  /**
   * When set, the composer is disabled and this reason is shown as a notice +
   * input placeholder. Used by the Development page's "no code pulled" guard.
   */
  disabledReason?: string;
  /** Starter prompt chips shown in the empty state (click → sends). */
  starterSuggestions?: string[];
}

const ATTACH_ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.md,image/*";

const DEFAULT_SLASH_COMMANDS = [
  { command: "/summarize", description: "Summarize the selected artifact" },
  { command: "/critique", description: "Critique as a senior reviewer" },
  { command: "/edit", description: "Apply a specific edit" },
  { command: "/explain", description: "Explain a passage in plain English" },
];

// Resizable panel bounds. Agents emit long architecture docs, so the ceiling is
// generous; the floor stays readable. The viewport cap (92vw) is enforced in CSS.
const MIN_WIDTH = 400;
const DEFAULT_WIDTH = 560;
const MAX_WIDTH_CAP = 1100;
const KEY_STEP = 32;
const KEY_STEP_COARSE = 96;
const WIDTH_STORAGE_KEY = "agent-chat-drawer-width";

const clamp = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), hi);

export function AgentChatDrawer({
  open,
  onOpenChange,
  context,
  messages,
  onSend,
  busy,
  slashCommands = DEFAULT_SLASH_COMMANDS,
  sessions,
  activeSessionId,
  onSelectSession,
  onNewChat,
  attachments,
  onAttachFiles,
  onRemoveAttachment,
  disabledReason,
  starterSuggestions,
}: AgentChatDrawerProps) {
  const railEnabled = !!onNewChat;
  const attachEnabled = !!onAttachFiles;
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [draft, setDraft] = React.useState("");
  const listRef = React.useRef<HTMLDivElement>(null);

  // ── Resizable width ─────────────────────────────────────────────────────────
  // SSR renders the default; the persisted value is hydrated in an effect to keep
  // server and client markup identical.
  const [width, setWidth] = React.useState(DEFAULT_WIDTH);
  const [maxWidth, setMaxWidth] = React.useState(MAX_WIDTH_CAP);
  const [resizing, setResizing] = React.useState(false);
  const dragRef = React.useRef<{ startX: number; startW: number } | null>(null);
  const rafRef = React.useRef<number | null>(null);
  // Latest width computed mid-drag — pointerup reads this to persist the real final
  // value (the `width` state lags behind the rAF-throttled updates during a drag).
  const pendingWidthRef = React.useRef<number | null>(null);

  const persist = React.useCallback((next: number) => {
    try {
      window.localStorage.setItem(WIDTH_STORAGE_KEY, String(Math.round(next)));
    } catch {
      // private mode / storage disabled — width simply doesn't persist
    }
  }, []);

  // Hydrate persisted width + track the viewport cap (92vw, never above the hard cap).
  React.useEffect(() => {
    const computeMax = () =>
      Math.min(MAX_WIDTH_CAP, Math.round(window.innerWidth * 0.92));
    let saved = DEFAULT_WIDTH;
    try {
      const raw = window.localStorage.getItem(WIDTH_STORAGE_KEY);
      if (raw) saved = Number(raw) || DEFAULT_WIDTH;
    } catch {
      /* ignore */
    }
    setMaxWidth(computeMax());
    setWidth(clamp(saved, MIN_WIDTH, computeMax()));
    const onResize = () => {
      const max = computeMax();
      setMaxWidth(max);
      setWidth((w) => clamp(w, MIN_WIDTH, max));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const commitWidth = React.useCallback(
    (next: number) => {
      const clamped = clamp(Math.round(next), MIN_WIDTH, maxWidth);
      setWidth(clamped);
      persist(clamped);
    },
    [maxWidth, persist],
  );

  const onHandlePointerDown = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, startW: width };
      setResizing(true);
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        // capture can fail if the pointer is already released — drag still works
      }
      document.body.style.userSelect = "none";
    },
    [width],
  );

  const onHandlePointerMove = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag) return;
      // Panel is anchored right; dragging the handle left (smaller clientX) widens it.
      const next = clamp(drag.startW + (drag.startX - e.clientX), MIN_WIDTH, maxWidth);
      pendingWidthRef.current = next;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => setWidth(next));
    },
    [maxWidth],
  );

  const endDrag = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragRef.current) return;
      dragRef.current = null;
      setResizing(false);
      document.body.style.userSelect = "";
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* pointer already released */
      }
      const final = pendingWidthRef.current;
      pendingWidthRef.current = null;
      if (final !== null) {
        setWidth(final);
        persist(final);
      }
    },
    [persist],
  );

  // Functional update so a held / rapidly-repeated arrow key accumulates each step
  // instead of reading a stale closed-over width.
  const nudge = React.useCallback(
    (delta: number) => {
      setWidth((w) => {
        const next = clamp(w + delta, MIN_WIDTH, maxWidth);
        persist(next);
        return next;
      });
    },
    [maxWidth, persist],
  );

  const onHandleKeyDown = React.useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const step = e.shiftKey ? KEY_STEP_COARSE : KEY_STEP;
      // ArrowLeft widens (matches dragging the handle left); ArrowRight narrows.
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        nudge(step);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        nudge(-step);
      } else if (e.key === "Home") {
        e.preventDefault();
        commitWidth(maxWidth);
      } else if (e.key === "End") {
        e.preventDefault();
        commitWidth(MIN_WIDTH);
      }
    },
    [commitWidth, nudge, maxWidth],
  );

  React.useEffect(() => {
    // Auto-scroll to bottom on new message
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const sendText = React.useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || busy || disabledReason) return;
      setDraft("");
      await onSend(text);
    },
    [busy, disabledReason, onSend],
  );

  const send = () => sendText(draft);

  const showSlash = draft.startsWith("/") && !draft.includes(" ");

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        style={{ width, maxWidth: "92vw" }}
        className={cn(
          "flex flex-row gap-0 p-0 sm:max-w-none",
          resizing && "select-none",
        )}
      >
        {/* Drag-to-resize handle on the panel's left edge (VS Code / Linear pattern).
            Keyboard: ←/→ resize, Shift for coarse, Home/End to max/min, dbl-click resets. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat panel"
          aria-valuenow={Math.round(width)}
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={Math.round(maxWidth)}
          tabIndex={0}
          onPointerDown={onHandlePointerDown}
          onPointerMove={onHandlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onKeyDown={onHandleKeyDown}
          onDoubleClick={() => commitWidth(DEFAULT_WIDTH)}
          title="Drag to resize · double-click to reset"
          className="group focus-visible:ring-primary focus-visible:ring-offset-background absolute inset-y-0 left-0 z-20 flex w-3 -translate-x-1/2 cursor-col-resize touch-none items-center justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
        >
          <span
            aria-hidden
            className={cn(
              "bg-border group-hover:bg-primary h-full w-px transition-colors duration-150 motion-reduce:transition-none",
              resizing && "bg-primary",
            )}
          />
          <span
            aria-hidden
            className={cn(
              "group-hover:bg-primary absolute h-9 w-1 rounded-full bg-transparent transition-all duration-150 group-hover:w-1.5 motion-reduce:transition-none",
              resizing && "bg-primary w-1.5",
            )}
          />
        </div>

        {/* Left rail — per-user, per-agent session history (§11A). Only rendered
            when the host wires session handlers; otherwise the drawer is unchanged. */}
        {railEnabled && (
          <aside className="bg-muted/20 flex w-52 shrink-0 flex-col border-r">
            <div className="p-2">
              <Button
                size="sm"
                variant="outline"
                className="w-full justify-start gap-2"
                onClick={onNewChat}
              >
                <Plus className="size-4" aria-hidden />
                New chat
              </Button>
            </div>
            <ScrollArea className="flex-1">
              <ul className="space-y-0.5 p-2 pt-0">
                {(sessions ?? []).length === 0 && (
                  <li className="text-muted-foreground px-2 py-6 text-center text-xs">
                    No conversations yet
                  </li>
                )}
                {(sessions ?? []).map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => onSelectSession?.(s.id)}
                      title={s.title}
                      className={cn(
                        "hover:bg-muted w-full truncate rounded-md px-2 py-1.5 text-left text-xs",
                        s.id === activeSessionId && "bg-muted font-medium",
                      )}
                    >
                      {s.title}
                    </button>
                  </li>
                ))}
              </ul>
            </ScrollArea>
          </aside>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
        <SheetHeader className="space-y-2 border-b p-4 text-left">
          <SheetTitle className="flex items-center gap-2">
            <Sparkles className="text-primary size-4" aria-hidden />
            Agent chat
          </SheetTitle>
          <SheetDescription>
            Scoped to your current view. Actions here run with your permissions.
          </SheetDescription>
          {context && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              <Badge variant="secondary" className="font-mono text-[10px]">
                {context.page}
              </Badge>
              {context.artifactTitle && (
                <Badge variant="outline" className="font-mono text-[10px]">
                  {context.artifactTitle}
                </Badge>
              )}
            </div>
          )}
        </SheetHeader>

        <ScrollArea className="flex-1" viewportClassName="[&>div]:!block">
          <div ref={listRef} className="flex min-w-0 flex-col gap-4 p-4">
            {messages.length === 0 && (
              <div className="text-muted-foreground py-10 text-center text-sm">
                <MessageSquare className="mx-auto mb-2 size-5 opacity-60" aria-hidden />
                {disabledReason ? (
                  <p>{disabledReason}</p>
                ) : (
                  <>
                    <p>Start a conversation — ask the agent to make a change.</p>
                    {starterSuggestions && starterSuggestions.length > 0 && (
                      <div className="mx-auto mt-4 flex max-w-sm flex-col gap-1.5">
                        {starterSuggestions.map((s) => (
                          <button
                            key={s}
                            type="button"
                            onClick={() => void sendText(s)}
                            disabled={busy}
                            className="border-line-soft bg-surface-1 hover:bg-surface-2 disabled:opacity-50 rounded-lg border px-3 py-2 text-left text-[13px] transition-colors"
                          >
                            {s}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
            {messages.map((m) => (
              <ChatBubble key={m.id} message={m} onSuggestion={sendText} busy={busy} />
            ))}
          </div>
        </ScrollArea>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
          className="space-y-2 border-t p-3"
        >
          {disabledReason && (
            <p id="chat-disabled-reason" className="text-muted-foreground px-3 pb-2 text-xs">{disabledReason}</p>
          )}
          {showSlash && (
            <ul className="bg-popover text-popover-foreground overflow-hidden rounded-md border shadow-sm">
              {slashCommands
                .filter((c) => c.command.startsWith(draft))
                .slice(0, 4)
                .map((c) => (
                  <li key={c.command}>
                    <button
                      type="button"
                      onClick={() => setDraft(c.command + " ")}
                      className="hover:bg-accent flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm"
                    >
                      <code className="bg-muted rounded px-1 font-mono text-[11px]">
                        {c.command}
                      </code>
                      <span className="text-muted-foreground text-xs">{c.description}</span>
                    </button>
                  </li>
                ))}
            </ul>
          )}
          {attachments && attachments.length > 0 && (
            <ul className="flex flex-wrap gap-1.5">
              {attachments.map((a) => (
                <li
                  key={a.name}
                  className="bg-muted flex items-center gap-1 rounded border px-2 py-0.5 text-xs"
                >
                  <Paperclip className="size-3" aria-hidden />
                  <span className="max-w-[160px] truncate" title={a.name}>{a.name}</span>
                  {onRemoveAttachment && (
                    <button
                      type="button"
                      aria-label={`Remove ${a.name}`}
                      onClick={() => onRemoveAttachment(a.name)}
                      className="text-muted-foreground hover:text-foreground ml-0.5"
                    >
                      ×
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={disabledReason ?? "Message the agent — Enter to send, Shift+Enter for newline"}
            rows={2}
            disabled={busy || !!disabledReason}
            aria-label="Message the agent"
            aria-describedby={disabledReason ? "chat-disabled-reason" : undefined}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter inserts a newline. Ignore while an IME
              // composition is active (e.g. CJK input) so Enter confirms the candidate.
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="flex items-center justify-between gap-2">
            {attachEnabled && (
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ATTACH_ACCEPT}
                className="hidden"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  if (files.length) onAttachFiles!(files);
                  e.target.value = ""; // allow re-selecting the same file
                }}
              />
            )}
            <Button
              variant="ghost"
              size="icon"
              type="button"
              aria-label="Attach files"
              disabled={!attachEnabled || busy}
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip className="size-4" aria-hidden />
            </Button>
            <Button type="submit" size="sm" disabled={!draft.trim() || busy || !!disabledReason} aria-busy={busy}>
              <SendHorizontal className="size-4" aria-hidden />
              Send
            </Button>
          </div>
        </form>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function ChatBubble({
  message,
  onSuggestion,
  busy,
}: {
  message: AgentChatMessage;
  onSuggestion: (text: string) => void;
  busy?: boolean;
}) {
  const isUser = message.role === "user";
  const isEmptyStream = message.streaming && !message.content;

  return (
    <div className={cn("flex items-start gap-2.5", isUser && "flex-row-reverse")}>
      <Avatar className="size-7 shrink-0">
        <AvatarFallback className={isUser ? "bg-primary text-primary-foreground" : "bg-surface-2"}>
          {isUser ? <User className="size-3.5" /> : <Bot className="text-primary size-3.5" />}
        </AvatarFallback>
      </Avatar>

      <div className={cn("flex min-w-0 flex-col gap-1.5", isUser ? "max-w-[85%] items-end" : "flex-1 items-start")}>
        <div
          className={cn(
            "min-w-0 rounded-xl px-3.5 py-2.5 text-sm",
            isUser
              ? "bg-primary text-primary-foreground rounded-br-sm"
              : "bg-card text-card-foreground border-line-soft w-full rounded-bl-sm border",
          )}
        >
          {/* Tool-call timeline (agent only) */}
          {!isUser && message.toolCalls && message.toolCalls.length > 0 && (
            <ul className="mb-2 flex flex-col gap-1">
              {message.toolCalls.map((tc) => (
                <li
                  key={tc.id}
                  className="bg-muted text-muted-foreground border-border flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[10.5px]"
                >
                  <span
                    className={cn(
                      "size-1.5 shrink-0 rounded-full",
                      tc.status === "running" && "bg-info animate-pulse",
                      tc.status === "completed" && "bg-success",
                      tc.status === "failed" && "bg-destructive",
                    )}
                  />
                  <span className="font-semibold">{tc.name}</span>
                  <span className="truncate opacity-70">
                    ({Object.keys(tc.args).join(", ")})
                  </span>
                </li>
              ))}
            </ul>
          )}

          {/* Body */}
          {isEmptyStream ? (
            <ThinkingIndicator />
          ) : isUser ? (
            <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
          ) : (
            <div className="flex min-w-0 flex-col gap-1">
              <MarkdownMessage content={message.content} />
              {message.streaming && <ThinkingIndicator />}
            </div>
          )}

          {/* Citations */}
          {!isUser && message.citations && message.citations.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {message.citations.map((id) => (
                <span
                  key={id}
                  className="bg-muted text-muted-foreground border-border rounded border px-1.5 py-0.5 font-mono text-[10px]"
                >
                  ↳ {id}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Diff cards (agent only) — one per file touched this turn, own items
            in the timeline rather than merged into the text bubble. */}
        {!isUser && message.diffs && message.diffs.length > 0 && (
          <div className="flex w-full min-w-0 flex-col gap-2">
            {message.diffs.map((d) => (
              <DiffCard key={d.path} {...d} />
            ))}
          </div>
        )}

        {/* Follow-up suggestion chips (agent only, not while streaming) */}
        {!isUser && !message.streaming && message.suggestions && message.suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => onSuggestion(s)}
                disabled={busy}
                className="border-line-soft bg-surface-1 hover:bg-surface-2 text-foreground/80 disabled:opacity-50 inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors"
              >
                <Sparkles className="text-primary size-3" aria-hidden />
                {s}
              </button>
            ))}
          </div>
        )}
        {message.attachments && message.attachments.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {message.attachments.map((a) => (
              <a
                key={a.url}
                href={a.url}
                target="_blank"
                rel="noopener noreferrer"
                download
                className={cn(
                  // Rendered OUTSIDE the bubble on the page background (for both roles),
                  // so use page-visible colors — not primary-foreground (white), which
                  // was invisible on the light background for user messages.
                  "bg-muted text-muted-foreground border-border inline-flex items-center",
                  "gap-1 rounded border px-1.5 py-0.5 text-[11px] hover:underline",
                )}
              >
                <Paperclip className="size-3" aria-hidden />
                {a.name}
              </a>
            ))}
          </div>
        )}

        <span
          className={cn(
            "px-1 font-mono text-[10px] uppercase tracking-wider",
            "text-muted-foreground",
          )}
        >
          {new Date(message.createdAt).toLocaleTimeString(undefined, {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      </div>
    </div>
  );
}

/**
 * A single file's diff, collapsed by default. No accordion primitive exists
 * under components/ui/ (checked) — hand-rolled to match the disclosure
 * pattern already used by AuditEventRow (chevron + header button, toggling a
 * `hidden` body rather than mounting/unmounting it).
 *
 * `DiffViewer` stays mounted at all times (just visually hidden while
 * collapsed) rather than conditionally rendered — this keeps its Monaco
 * instance warm across repeated expand/collapse instead of re-loading it
 * every time, and keeps the card's contents test-observable regardless of
 * the collapsed state.
 */
function DiffCard({
  path,
  original,
  modified,
  changeKind,
}: {
  path: string;
  original: string;
  modified: string;
  changeKind: "created" | "edited";
}) {
  const [expanded, setExpanded] = React.useState(false);

  return (
    <div className="border-line-soft bg-surface-1 w-full min-w-0 overflow-hidden rounded-lg border">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="hover:bg-surface-2 flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
      >
        <span className="text-muted-foreground shrink-0" aria-hidden>
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs" title={path}>
          {path}
        </span>
        <Badge
          variant={changeKind === "created" ? "success" : "info"}
          className="shrink-0 text-[10px] capitalize"
        >
          {changeKind}
        </Badge>
      </button>
      <div hidden={!expanded} className="border-line-soft border-t">
        <DiffViewer original={original} modified={modified} filename={path} showToolbar />
      </div>
    </div>
  );
}
