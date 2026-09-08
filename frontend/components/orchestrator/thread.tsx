"use client";

import * as React from "react";
import {
  AlertTriangle,
  Bot,
  Loader2,
  Paperclip,
  Send,
  Sparkles,
  Square,
  User,
  Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownMessage } from "@/components/app/markdown-message";
import { ThinkingIndicator } from "@/components/app/thinking-indicator";
import { PHASE_LABEL } from "@/lib/agents";
import { splitModelKey, type OrchestratorMessage } from "@/lib/orchestrator/types";

/**
 * What the picker offers.
 *
 * Mirrors `attachment_store.ALLOWED_ATTACHMENT_EXTS`, which is what actually decides —
 * this list only narrows the file dialog, and the server refuses anything else with a
 * readable 400. Images are offered because the store accepts them and the user can see
 * them in the thread; the agent CANNOT read one (there is no vision path in this
 * engine) and the prompt says so outright rather than letting the agent infer from the
 * file name.
 */
const ATTACH_ACCEPT =
  ".pdf,.docx,.doc,.txt,.md,.xlsx,.xls,.csv,.png,.jpg,.jpeg,.gif,.webp";

/** One stored attachment, as the server confirmed it. */
export interface ThreadAttachment {
  name: string;
  url: string;
}

export interface ThreadProps {
  messages: OrchestratorMessage[];
  /** True while a turn is streaming — drives the Stop affordance and indicator. */
  busy: boolean;
  /** Disable the composer entirely (no project/model resolved yet). */
  disabled?: boolean;
  placeholder: string;
  onSend: (text: string) => void;
  onStop: () => void;
  /**
   * Files the SERVER holds for this run — never the local `File` objects the browser
   * picked. A chip drawn from an unsent file would appear whether or not the upload
   * succeeded, and the user would believe the agent has a document it has never seen.
   */
  attachments?: ReadonlyArray<ThreadAttachment>;
  /** Hand picked files to the owner, which uploads them and refreshes `attachments`. */
  onAttachFiles?: (files: File[]) => void;
  /** True while an upload is in flight. */
  attaching?: boolean;
  /** Why the last upload failed. Shown, never swallowed. */
  attachError?: string | null;
  /** Rendered above the composer when the run is parked or finished. */
  footerSlot?: React.ReactNode;
  /** Rendered in place of the thread when there is nothing yet. */
  emptySlot?: React.ReactNode;
}

/** The Orchestrator's own turns are attributed to it, not to a stage agent. */
function Attribution({ message }: { message: OrchestratorMessage }) {
  const label = message.phase ? PHASE_LABEL[message.phase] : "Orchestrator";
  const model = message.modelKey ? splitModelKey(message.modelKey) : null;

  return (
    <div className="mb-1 flex flex-wrap items-center gap-2">
      <span className="flex items-center gap-1.5">
        {message.phase ? (
          <Bot className="text-brand-bright size-3.5" aria-hidden />
        ) : (
          <Sparkles className="text-brand-bright size-3.5" aria-hidden />
        )}
        <span className="text-[12px] font-semibold">{label}</span>
      </span>
      {model?.model_id && (
        <span className="text-muted-foreground border-line-soft rounded-full border px-1.5 py-px font-mono text-[9.5px]">
          {model.model_id}
        </span>
      )}
    </div>
  );
}

export function Thread({
  messages,
  busy,
  disabled,
  placeholder,
  onSend,
  onStop,
  attachments = [],
  onAttachFiles,
  attaching = false,
  attachError = null,
  footerSlot,
  emptySlot,
}: ThreadProps) {
  const [text, setText] = React.useState("");
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const lastContent = messages[messages.length - 1]?.content.length ?? 0;
  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, lastContent, busy]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5 md:px-6">
        {messages.length === 0 ? (
          emptySlot
        ) : (
          messages.map((m) => {
            if (m.role === "user") {
              return (
                <div key={m.id} className="flex justify-end">
                  <div className="bg-primary text-primary-foreground max-w-[80%] rounded-2xl rounded-br-md px-3.5 py-2.5">
                    <div className="mb-1 flex items-center justify-end gap-1.5 opacity-80">
                      <User className="size-3" aria-hidden />
                      <span className="text-[11px] font-medium">You</span>
                    </div>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{m.content}</p>
                    {/* What went WITH this turn. The composer's chips say what the run
                        holds, which is a different statement and cannot answer "did the
                        PRD go with that one?" — it looks the same whether the file went
                        with this turn or three turns later. */}
                    {m.attachments && m.attachments.length > 0 && (
                      <div
                        data-testid={`message-attachments-${m.id}`}
                        className="border-primary-foreground/25 mt-2 flex flex-wrap gap-1.5 border-t pt-2"
                      >
                        {m.attachments.map((a) => (
                          <a
                            key={a.url || a.name}
                            href={a.url}
                            target="_blank"
                            rel="noreferrer"
                            title={a.name}
                            className="bg-primary-foreground/15 hover:bg-primary-foreground/25 inline-flex max-w-[200px] items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-colors"
                          >
                            <Paperclip className="size-3 shrink-0" aria-hidden />
                            <span className="truncate">{a.name}</span>
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            }

            if (m.role === "system") {
              return (
                <div
                  key={m.id}
                  className="text-muted-foreground border-line-soft flex items-start gap-2 rounded-lg border border-dashed px-3 py-2 text-[12px]"
                >
                  <Workflow className="mt-px size-3.5 shrink-0" aria-hidden />
                  <span>{m.content}</span>
                </div>
              );
            }

            return (
              <div
                key={m.id}
                className="border-line-soft bg-panel-elevated rounded-lg border px-3.5 py-3"
              >
                <Attribution message={m} />

                {m.content ? (
                  <MarkdownMessage content={m.content} />
                ) : (
                  <ThinkingIndicator
                    label={m.phase ? `${PHASE_LABEL[m.phase]} agent working` : "Orchestrating"}
                  />
                )}
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>

      {footerSlot}

      {/* Composer */}
      <div className="border-line-soft bg-surface-1 border-t px-4 py-3 md:px-6">
        {/* What the agent will actually be given. Rendered from the server's own
            list, so a chip here means a stored file and nothing else. */}
        {attachments.length > 0 && (
          <div
            data-testid="orchestrator-attachments"
            className="mb-2 flex flex-wrap items-center gap-1.5"
          >
            {attachments.map((a) => (
              <a
                key={a.url || a.name}
                href={a.url}
                target="_blank"
                rel="noreferrer"
                className="border-line-soft bg-panel-elevated text-muted-foreground hover:text-foreground inline-flex max-w-[220px] items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px]"
              >
                <Paperclip className="size-3 shrink-0" aria-hidden />
                <span className="truncate">{a.name}</span>
              </a>
            ))}
            {/* The backend scopes attachments to the RUN, not to the turn they were
                attached to, because this engine has no agent order. Saying so stops a
                user re-uploading the same BRD for every agent they talk to. */}
            <span className="text-muted-foreground text-[11px]">
              Available to every agent on this run.
            </span>
          </div>
        )}

        {attachError && (
          <div className="border-destructive/40 bg-destructive/[0.06] text-destructive mb-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-[12.5px]">
            <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
            {/* Said out loud, and no chip drawn. An upload that failed quietly would
                leave the user believing the agent had the document. */}
            <span>{attachError}</span>
          </div>
        )}

        <div className="border-line-soft bg-panel-elevated focus-within:border-primary/50 flex items-end gap-2 rounded-xl border px-2.5 py-2 transition-colors">
          <input
            ref={fileInputRef}
            data-testid="orchestrator-attach-input"
            type="file"
            multiple
            accept={ATTACH_ACCEPT}
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) onAttachFiles?.(files);
              e.target.value = ""; // so the same file can be picked twice
            }}
          />
          <Button
            size="icon"
            variant="ghost"
            type="button"
            className="size-8 shrink-0"
            aria-label="Attach files"
            disabled={disabled || attaching || !onAttachFiles}
            onClick={() => fileInputRef.current?.click()}
          >
            {attaching ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Paperclip className="size-3.5" aria-hidden />
            )}
          </Button>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            disabled={disabled}
            placeholder={placeholder}
            aria-label="Message the Orchestrator"
            rows={1}
            className="max-h-40 min-h-[2.25rem] resize-none border-0 bg-transparent px-1 py-1.5 text-sm shadow-none focus-visible:ring-0"
          />
          {busy ? (
            <Button size="icon" variant="outline" className="size-8 shrink-0" onClick={onStop} aria-label="Stop the run">
              <Square className="size-3.5" aria-hidden />
            </Button>
          ) : (
            <Button
              size="icon"
              className="size-8 shrink-0"
              onClick={submit}
              disabled={disabled || !text.trim()}
              aria-label="Send"
            >
              <Send className="size-3.5" aria-hidden />
            </Button>
          )}
        </div>
        <p className="text-muted-foreground mt-1.5 text-[11px]">
          Enter sends · Shift+Enter for a new line
        </p>
      </div>
    </div>
  );
}
