"use client";

import * as React from "react";
import { Check, ListChecks, Loader2, SendHorizonal } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { ChoiceCard as ChoiceCardT } from "@/lib/copilot/types";

export interface ChoiceCardProps {
  card: ChoiceCardT;
  /** Called on confirm with the picks + optional free-text override. */
  onAnswer: (cardId: string, selectedIds: string[], freeText?: string) => void;
  /** Render read-only (already answered / historical transcript entry). */
  readOnly?: boolean;
  /** Prefilled selection for a read-only echo. */
  answeredIds?: string[];
  answeredFreeText?: string;
  pending?: boolean;
  className?: string;
}

const KIND_LABEL: Record<ChoiceCardT["kind"], string> = {
  ado_project: "Select project",
  story_multiselect: "Select stories",
  repo: "Select repository",
  branch: "Select branch",
  confirm: "Confirm",
  custom: "Choose",
};

export function ChoiceCard({
  card,
  onAnswer,
  readOnly = false,
  answeredIds,
  answeredFreeText,
  pending = false,
  className,
}: ChoiceCardProps) {
  const single = card.max_select === 1;
  const [selected, setSelected] = React.useState<string[]>(answeredIds ?? []);
  const [freeText, setFreeText] = React.useState<string>(answeredFreeText ?? "");
  const [submitted, setSubmitted] = React.useState(readOnly);

  const min = Math.max(0, card.min_select);
  const max = card.max_select > 0 ? card.max_select : card.options.length;
  const usingFreeText = freeText.trim().length > 0;
  const count = selected.length;
  const valid = usingFreeText || (count >= min && count <= max && count > 0);
  const locked = readOnly || submitted || pending;

  const toggle = React.useCallback(
    (id: string) => {
      if (locked) return;
      setSelected((cur) => {
        if (single) return [id];
        if (cur.includes(id)) return cur.filter((x) => x !== id);
        if (cur.length >= max) return cur; // respect max_select
        return [...cur, id];
      });
    },
    [locked, single, max],
  );

  const confirm = React.useCallback(() => {
    if (!valid || locked) return;
    setSubmitted(true);
    onAnswer(card.card_id, selected, freeText.trim() || undefined);
  }, [valid, locked, onAnswer, card.card_id, selected, freeText]);

  const titleId = `choice-${card.card_id}-prompt`;

  return (
    <section
      aria-labelledby={titleId}
      className={cn(
        "space-y-3 rounded-[var(--radius)] border border-brand-bright/30 bg-gradient-to-b from-brand-bright/[0.06] to-transparent p-4",
        locked && "opacity-95",
        className,
      )}
    >
      {/* Eyebrow */}
      <div className="flex items-center justify-between gap-2">
        <div className="text-brand-bright flex items-center gap-2 font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em]">
          <ListChecks className="size-3.5" aria-hidden />
          {KIND_LABEL[card.kind]}
          {!single && (
            <span className="text-muted-foreground normal-case tracking-normal">
              {min === max ? `pick ${max}` : `pick ${min}–${max}`}
            </span>
          )}
        </div>
        {locked && (
          <span className="text-success inline-flex items-center gap-1 font-mono text-[10px] uppercase">
            <Check className="size-3" aria-hidden />
            Answered
          </span>
        )}
      </div>

      <p id={titleId} className="text-[13.5px] font-medium leading-snug text-foreground">
        {card.prompt}
      </p>

      {/* Options */}
      {single ? (
        <RadioGroup
          value={selected[0] ?? ""}
          onValueChange={(v) => toggle(v)}
          className="gap-1.5"
          aria-label={card.prompt}
        >
          {card.options.map((o) => {
            const active = selected[0] === o.id;
            return (
              <label
                key={o.id}
                htmlFor={`${card.card_id}-${o.id}`}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-[var(--radius)] border px-3 py-2 transition-colors",
                  active
                    ? "border-brand-bright/50 bg-brand-bright/[0.07]"
                    : "border-line-soft hover:bg-panel-elevated/60",
                  locked && "cursor-default hover:bg-transparent",
                )}
              >
                <RadioGroupItem
                  id={`${card.card_id}-${o.id}`}
                  value={o.id}
                  disabled={locked}
                  className="mt-0.5"
                />
                <OptionText label={o.label} sublabel={o.sublabel ?? undefined} />
              </label>
            );
          })}
        </RadioGroup>
      ) : (
        <div className="grid gap-1.5" role="group" aria-label={card.prompt}>
          {card.options.map((o) => {
            const active = selected.includes(o.id);
            const atMax = !active && count >= max;
            return (
              <label
                key={o.id}
                htmlFor={`${card.card_id}-${o.id}`}
                className={cn(
                  "flex items-start gap-3 rounded-[var(--radius)] border px-3 py-2 transition-colors",
                  active
                    ? "border-brand-bright/50 bg-brand-bright/[0.07]"
                    : "border-line-soft hover:bg-panel-elevated/60",
                  atMax && "opacity-50",
                  locked ? "cursor-default hover:bg-transparent" : "cursor-pointer",
                )}
              >
                <Checkbox
                  id={`${card.card_id}-${o.id}`}
                  checked={active}
                  disabled={locked || atMax}
                  onCheckedChange={() => toggle(o.id)}
                  className="mt-0.5"
                />
                <OptionText label={o.label} sublabel={o.sublabel ?? undefined} />
              </label>
            );
          })}
        </div>
      )}

      {/* Answered free-text echo (the input below is hidden when locked, so
          without this a free-text answer would leave the card looking blank). */}
      {locked && usingFreeText && (
        <p className="text-foreground border-line-soft rounded-[var(--radius)] border bg-panel-elevated/40 px-3 py-2 text-[12.5px] italic">
          “{freeText}”
        </p>
      )}

      {/* Free-text override */}
      {!locked && (
        <div className="space-y-1.5">
          <Input
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            placeholder="Or describe it in words (overrides the picks)…"
            aria-label="Free-text override"
            className="border-line-soft h-9 text-[12.5px]"
            onKeyDown={(e) => {
              if (e.key === "Enter" && valid) {
                e.preventDefault();
                confirm();
              }
            }}
          />
        </div>
      )}

      {/* Confirm */}
      {!locked && (
        <div className="flex items-center justify-between gap-2 border-t border-line-soft pt-3">
          <span className="text-muted-foreground text-[11px]">
            {usingFreeText
              ? "Sending your description"
              : count > 0
                ? `${count} selected`
                : single
                  ? "Pick one"
                  : `Pick at least ${Math.max(1, min)}`}
          </span>
          <Button
            size="sm"
            onClick={confirm}
            disabled={!valid || pending}
            aria-busy={pending}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r text-white"
          >
            {pending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <SendHorizonal className="size-4" aria-hidden />
            )}
            Confirm
          </Button>
        </div>
      )}
    </section>
  );
}

function OptionText({ label, sublabel }: { label: string; sublabel?: string }) {
  return (
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[13px] font-medium text-foreground">{label}</span>
      {sublabel && (
        <span className="text-muted-foreground block truncate font-mono text-[11px]">
          {sublabel}
        </span>
      )}
    </span>
  );
}
