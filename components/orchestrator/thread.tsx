"use client";

import * as React from "react";
import { Bot, Send, ShieldAlert, Sparkles, Square, User, Workflow } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownMessage } from "@/components/app/markdown-message";
import { ThinkingIndicator } from "@/components/app/thinking-indicator";
import { PHASE_LABEL } from "@/lib/agents";
import { splitModelKey, type OrchestratorMessage } from "@/lib/orchestrator/types";

export interface ThreadProps {
  messages: OrchestratorMessage[];
  /** True while a turn is streaming — drives the Stop affordance and indicator. */
  busy: boolean;
  /** Disable the composer entirely (no project/model resolved yet). */
  disabled?: boolean;
  placeholder: string;
  onSend: (text: string) => void;
  onStop: () => void;
  onGateDecision: (decision: "approved" | "rejected") => void;
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
  onGateDecision,
  footerSlot,
  emptySlot,
}: ThreadProps) {
  const [text, setText] = React.useState("");
  const bottomRef = React.useRef<HTMLDivElement>(null);

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

  // The one gate still awaiting a decision, if any.
  const openGate = [...messages].reverse().find((m) => m.gate && !m.gate.decided);

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

            const isOpenGate = openGate?.id === m.id;

            return (
              <div
                key={m.id}
                className={cn(
                  "border-line-soft bg-panel-elevated rounded-lg border px-3.5 py-3",
                  m.gate && "border-warning/45 bg-warning/[0.04]",
                  m.gate?.decided === "approved" && "border-success/40 bg-success/[0.04]",
                  m.gate?.decided === "rejected" && "border-destructive/45 bg-destructive/[0.04]",
                )}
              >
                <Attribution message={m} />

                {m.gate && (
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="text-warning font-mono text-[10px] font-semibold tracking-wide uppercase">
                      Gate
                    </span>
                    {m.gate.mandatory && (
                      <span className="text-destructive inline-flex items-center gap-1 font-mono text-[10px] tracking-wide uppercase">
                        <ShieldAlert className="size-3" aria-hidden />
                        Mandatory — cannot be waived
                      </span>
                    )}
                    {m.gate.decided && (
                      <span
                        className={cn(
                          "font-mono text-[10px] tracking-wide uppercase",
                          m.gate.decided === "approved" ? "text-success" : "text-destructive",
                        )}
                      >
                        {m.gate.decided}
                      </span>
                    )}
                  </div>
                )}

                {m.content ? (
                  <MarkdownMessage content={m.content} />
                ) : (
                  <ThinkingIndicator
                    label={m.phase ? `${PHASE_LABEL[m.phase]} agent working` : "Orchestrating"}
                  />
                )}

                {isOpenGate && (
                  <div className="border-line-soft mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
                    <Button size="sm" className="h-7 text-[12px]" onClick={() => onGateDecision("approved")}>
                      Approve &amp; continue
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-destructive hover:text-destructive h-7 text-[12px]"
                      onClick={() => onGateDecision("rejected")}
                    >
                      Reject
                    </Button>
                    <span className="text-muted-foreground text-[11.5px]">
                      The run is stopped until you decide.
                    </span>
                  </div>
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
        <div className="border-line-soft bg-panel-elevated focus-within:border-primary/50 flex items-end gap-2 rounded-xl border px-2.5 py-2 transition-colors">
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
