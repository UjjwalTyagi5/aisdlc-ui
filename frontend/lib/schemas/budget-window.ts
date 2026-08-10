import { z } from "zod";

/**
 * The period a monthly cost cap is valid for — shared by Business Units and
 * projects, which carry the identical three fields (`monthlyBudgetUsd` plus
 * these two) and must agree on what they mean.
 *
 * Calendar dates (`YYYY-MM-DD`), not timestamps: a budget window is a business
 * period someone types into a form ("this runs to the end of Q3"), and an
 * instant would invite a timezone question that has no useful answer here.
 *
 * Both ends are independently optional, and all four combinations are
 * meaningful:
 *   - neither  → the cap is open-ended (the default, and the common case)
 *   - start    → the cap takes effect then; before that there is no cap
 *   - end      → the cap lapses after that date
 *   - both     → a closed window, e.g. a funded phase of work
 *
 * A lapsed window does NOT zero the cap or stop anything — like everything
 * else in the budget model here, it is reported rather than enforced (see
 * `budgetWindowState` below, and the warn-don't-block rule in
 * components/app/budget-hub.tsx).
 */
export const BudgetWindowFields = {
  /** Inclusive first day the cap applies, `YYYY-MM-DD`. Null = no start bound. */
  budgetStartDate: z.string().nullable().optional(),
  /** Inclusive last day the cap applies, `YYYY-MM-DD`. Null = no end bound. */
  budgetEndDate: z.string().nullable().optional(),
};

export const BudgetWindow = z.object(BudgetWindowFields);
export type BudgetWindow = z.infer<typeof BudgetWindow>;

export type BudgetWindowState = "none" | "scheduled" | "active" | "expired";

/** `YYYY-MM-DD` for a Date, in local time — `toISOString()` would shift the day. */
export function toDateInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Where "now" sits relative to a budget window. String comparison is correct
 * and deliberate here: `YYYY-MM-DD` sorts lexicographically the same way it
 * sorts chronologically, so this needs no Date parsing and no timezone.
 */
export function budgetWindowState(
  window: BudgetWindow,
  today: string = toDateInput(new Date()),
): BudgetWindowState {
  const { budgetStartDate: start, budgetEndDate: end } = window;
  if (!start && !end) return "none";
  if (end && today > end) return "expired";
  if (start && today < start) return "scheduled";
  return "active";
}

/** Human-readable window, e.g. "1 Jul 2026 – 30 Sep 2026" / "from 1 Jul 2026". */
export function formatBudgetWindow(window: BudgetWindow): string | null {
  const { budgetStartDate: start, budgetEndDate: end } = window;
  if (!start && !end) return null;
  const fmt = (d: string) =>
    new Date(`${d}T00:00:00`).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  if (start && end) return `${fmt(start)} – ${fmt(end)}`;
  if (start) return `from ${fmt(start)}`;
  return `until ${fmt(end!)}`;
}

/** Validation shared by every form that edits a window. Null when valid. */
export function budgetWindowError(window: BudgetWindow): string | null {
  const { budgetStartDate: start, budgetEndDate: end } = window;
  if (start && end && end < start) return "End date must be on or after the start date";
  return null;
}
