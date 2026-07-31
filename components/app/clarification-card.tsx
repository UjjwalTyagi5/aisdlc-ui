"use client";

import * as React from "react";
import { Loader2, MessageCircleQuestion, Send } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { SlaCountdown } from "@/components/app/sla-countdown";

export interface ClarificationCardProps {
  /** Agent's clarification question(s) — rendered as plain text (T-10.3-08, never dangerouslySetInnerHTML). */
  questions: string[];
  /** Correlates the answer with the pending clarification (D-08 server-side correlation). */
  clarificationId: string;
  /** ISO-8601 SLA deadline for answering — reuses the M5 SlaCountdown when present. */
  deadline?: string;
  /** Called with the trimmed answer when Submit is pressed. */
  onSubmit: (answer: string) => void | Promise<void>;
  /** Disable all actions while a signal is in flight. */
  pending?: boolean;
  /**
   * Whether the session holds the phase approval permission (defense-in-depth
   * UX gate, T-10.3-09 — backend require_permission remains authoritative).
   */
  canAnswer: boolean;
  className?: string;
}

/**
 * ClarificationCard — durable within-agent clarification UI (REQ-M10-06).
 *
 * Thin adaptation of `ApprovalCard` (D-M10-04, registry-safe): reuses the
 * same brand-orange eyebrow + gradient-card shell, the existing `Textarea`
 * + `Button` primitives, and the M5 `SlaCountdown`. Submitting calls
 * `onSubmit(answer)` — the caller (run-detail-drawer) wires this to
 * `sendClarificationAnswer`, which sends a Temporal SIGNAL through the
 * existing BFF route, NOT an ephemeral WebSocket chat message (D-M10-03).
 */

/**
 * Pure submit-gating logic, exported for unit testing (mirrors the
 * eval-indicator pattern of testing exported helpers in a node
 * environment without a DOM). Mirrors the Submit button's `disabled`
 * condition: an answer can only be submitted when the session is
 * permitted, no signal is in flight, and the trimmed answer is non-empty.
 */
export function canSubmitAnswer(
  answer: string,
  canAnswer: boolean,
  pending?: boolean,
): boolean {
  return canAnswer && !pending && answer.trim().length > 0;
}

export function ClarificationCard({
  questions,
  clarificationId,
  deadline,
  onSubmit,
  pending,
  canAnswer,
  className,
}: ClarificationCardProps) {
  const [answer, setAnswer] = React.useState("");

  const handleSubmit = React.useCallback(async () => {
    const trimmed = answer.trim();
    if (!trimmed) return;
    await onSubmit(trimmed);
    setAnswer("");
  }, [answer, onSubmit]);

  return (
    <section
      className={cn(
        // Mirrors ApprovalCard's brand-orange border + gradient bg shell.
        "space-y-3 rounded-[var(--radius)] border border-primary/35 bg-gradient-to-b from-primary/7 to-transparent p-4",
        className,
      )}
      aria-label="Agent clarification"
      data-clarification-id={clarificationId}
    >
      {/* Eyebrow: brand dot + mono label, mirrors ApprovalCard */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="size-2 rounded-full bg-brand-bright shadow-[0_0_10px_var(--color-brand-bright)]"
            aria-hidden
          />
          <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em] text-brand-bright">
            Agent clarification
          </span>
        </div>
        {deadline && <SlaCountdown deadline={deadline} />}
      </div>

      <header className="flex items-start gap-2">
        <MessageCircleQuestion
          className="text-brand-bright mt-0.5 size-4 shrink-0"
          aria-hidden
        />
        <div className="space-y-1.5">
          {questions.length > 0 ? (
            questions.map((q, i) => (
              <p key={i} className="font-display text-[13.5px] font-semibold leading-snug">
                {q}
              </p>
            ))
          ) : (
            <p className="text-muted-foreground text-[13.5px]">
              The agent is waiting for clarification.
            </p>
          )}
        </div>
      </header>

      {canAnswer ? (
        <div className="space-y-2 border-t border-line-soft pt-3">
          <Textarea
            placeholder="Type your answer for the agent…"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={3}
            disabled={pending}
            aria-label="Clarification answer"
          />
          <div className="flex justify-end">
            <Button
              onClick={handleSubmit}
              size="sm"
              disabled={!canSubmitAnswer(answer, canAnswer, pending)}
              aria-busy={pending}
              className="bg-gradient-to-r from-brand-gradient-from to-brand-gradient-to text-white"
            >
              {pending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              Submit answer
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-muted-foreground border-t border-line-soft pt-3 text-[12.5px]">
          You do not have permission to answer this clarification.
        </p>
      )}
    </section>
  );
}
