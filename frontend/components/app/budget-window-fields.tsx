"use client";

import { CalendarRange } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import {
  budgetWindowState,
  formatBudgetWindow,
  type BudgetWindow,
} from "@/lib/schemas/budget-window";

/**
 * The start/end date pair that scopes a cost cap to a period.
 *
 * One component for every place a budget is edited — the Business Unit create
 * dialog, the unit's budget card, the project create dialog and the project's
 * cost page — because a window typed in one place and read in another has to
 * mean the same thing, and four hand-rolled date pairs is how that stops being
 * true.
 *
 * Both ends stay optional: most budgets are open-ended, and forcing a date
 * would make people invent one.
 */
export function BudgetWindowFieldsInput({
  start,
  end,
  onStartChange,
  onEndChange,
  disabled,
  error,
  className,
}: {
  start: string;
  end: string;
  onStartChange: (v: string) => void;
  onEndChange: (v: string) => void;
  disabled?: boolean;
  error?: string | null;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5">
          <span className="text-muted-foreground font-mono text-[10px] tracking-wider uppercase">
            From
          </span>
          <Input
            type="date"
            value={start}
            disabled={disabled}
            onChange={(e) => onStartChange(e.target.value)}
            className="border-line-soft bg-surface-1 h-8 w-[9.5rem] font-mono text-[12px]"
          />
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-muted-foreground font-mono text-[10px] tracking-wider uppercase">
            To
          </span>
          <Input
            type="date"
            value={end}
            disabled={disabled}
            onChange={(e) => onEndChange(e.target.value)}
            className="border-line-soft bg-surface-1 h-8 w-[9.5rem] font-mono text-[12px]"
          />
        </label>
      </div>
      {error ? (
        <p className="text-destructive font-mono text-[10.5px]">{error}</p>
      ) : (
        <p className="text-muted-foreground font-mono text-[10.5px]">
          Optional — leave both blank for an open-ended budget.
        </p>
      )}
    </div>
  );
}

/**
 * Read-only summary of a window, with the one state worth calling out.
 *
 * Expiry is reported, never enforced: an expired window does not zero the cap
 * or stop a run, so this is a `warning` tone rather than `destructive` — it
 * tells an admin to revisit the number, it does not announce an outage.
 */
export function BudgetWindowSummary({
  window,
  className,
}: {
  window: BudgetWindow;
  className?: string;
}) {
  const label = formatBudgetWindow(window);
  if (!label) return null;
  const state = budgetWindowState(window);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-mono text-[10.5px]",
        state === "expired"
          ? "text-warning"
          : state === "scheduled"
            ? "text-info"
            : "text-muted-foreground",
        className,
      )}
    >
      <CalendarRange className="size-3" aria-hidden />
      {label}
      {state === "expired" && " · expired"}
      {state === "scheduled" && " · not yet in effect"}
    </span>
  );
}
