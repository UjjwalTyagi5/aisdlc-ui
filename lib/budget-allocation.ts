/**
 * Roll-up arithmetic for the budget cascade: Organization ⊇ Business Unit ⊇
 * project (PRD §34.5).
 *
 * The rule this encodes is **warn, don't block**. Over-allocation is a real
 * signal — someone should know that a unit has handed out more cap than it
 * holds — but it is not an error, because it is routinely correct: projects
 * rarely all run at their cap in the same month, and a hard limit would just
 * push people into setting caps they don't mean. Nothing here throws or
 * refuses; it returns a number for the UI to explain.
 *
 * Projects with no cap of their own (`monthlyBudgetUsd == null`) inherit the
 * unit's and are deliberately excluded from `allocatedUsd` — counting them as
 * zero would understate the position, and counting them as the full unit cap
 * would overstate it. They are reported separately as `inheritingCount` so the
 * UI can say the allocated figure is a floor, not a total.
 */

export interface BudgetAllocation {
  /** The parent Business Unit's cap; null = no cap set, so nothing to exceed. */
  unitBudgetUsd: number | null;
  /** Sum of the explicit caps already given to projects in this unit. */
  allocatedUsd: number;
  /** Projects in the unit with no cap of their own — they inherit. */
  inheritingCount: number;
  /** Allocated + whatever is being proposed right now. */
  totalUsd: number;
  /** How far past the unit cap `totalUsd` sits; 0 when within it. */
  overBy: number;
  isOver: boolean;
  /** Cap left to allocate, or null when the unit has no cap. */
  remainingUsd: number | null;
}

/**
 * @param projects  every project in the parent unit, EXCLUDING the one being
 *                  edited — a project's own current cap must not count against
 *                  the proposal that replaces it.
 * @param proposedUsd  the cap being entered now; null when none is set.
 */
export function budgetAllocation(
  unitBudgetUsd: number | null | undefined,
  projects: Array<{ monthlyBudgetUsd?: number | null; archived?: boolean }>,
  proposedUsd: number | null,
): BudgetAllocation {
  const live = projects.filter((p) => !p.archived);
  const allocatedUsd = live.reduce((sum, p) => sum + (p.monthlyBudgetUsd ?? 0), 0);
  const inheritingCount = live.filter(
    (p) => p.monthlyBudgetUsd === null || p.monthlyBudgetUsd === undefined,
  ).length;
  const totalUsd = allocatedUsd + (proposedUsd ?? 0);
  const cap = unitBudgetUsd ?? null;
  const overBy = cap !== null && totalUsd > cap ? totalUsd - cap : 0;

  return {
    unitBudgetUsd: cap,
    allocatedUsd,
    inheritingCount,
    totalUsd,
    overBy,
    isOver: overBy > 0,
    remainingUsd: cap === null ? null : Math.max(0, cap - totalUsd),
  };
}
