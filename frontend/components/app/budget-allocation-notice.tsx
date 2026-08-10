"use client";

import { AlertTriangle, Info } from "lucide-react";

import { cn } from "@/lib/utils";
import type { BudgetAllocation } from "@/lib/budget-allocation";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

const usd = (n: number) =>
  n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/**
 * How a proposed project cap sits against its Business Unit's.
 *
 * Warns, never blocks — see lib/budget-allocation.ts for why. The tone is
 * `warning`, not `destructive`, and the copy says what the position *is*
 * rather than telling anyone to fix it: over-allocation is often the intended
 * shape of a portfolio, and an admin reading this already knows their own
 * plan better than this component does.
 *
 * Silent when the unit has no cap — there is nothing to be over.
 */
export function BudgetAllocationNotice({
  allocation,
  className,
}: {
  allocation: BudgetAllocation;
  className?: string;
}) {
  const { unitBudgetUsd, allocatedUsd, inheritingCount, totalUsd, overBy, isOver, remainingUsd } =
    allocation;
  if (unitBudgetUsd === null) return null;

  const unit = BUSINESS_UNIT_LABEL.toLowerCase();
  const Icon = isOver ? AlertTriangle : Info;

  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border px-3 py-2",
        isOver ? "border-warning/30 bg-warning/10" : "border-line-soft bg-surface-1",
        className,
      )}
    >
      <Icon
        className={cn("mt-0.5 size-3.5 shrink-0", isOver ? "text-warning" : "text-muted-foreground")}
        aria-hidden
      />
      <div className="min-w-0 space-y-0.5">
        <p className={cn("font-mono text-[11px]", isOver ? "text-warning" : "text-muted-foreground")}>
          {isOver ? (
            <>
              {usd(totalUsd)} allocated against a {usd(unitBudgetUsd)} {unit} cap —{" "}
              <span className="font-semibold">{usd(overBy)} over</span>.
            </>
          ) : (
            <>
              {usd(totalUsd)} of the {unit}&apos;s {usd(unitBudgetUsd)} allocated ·{" "}
              {usd(remainingUsd ?? 0)} left.
            </>
          )}
        </p>
        <p className="text-muted-foreground font-mono text-[10px]">
          {isOver
            ? "Allowed — projects rarely all run at their cap in the same month. Nothing is blocked."
            : `${usd(allocatedUsd)} already committed to other projects.`}
          {inheritingCount > 0 &&
            ` ${inheritingCount} project${inheritingCount === 1 ? "" : "s"} with no cap of ${
              inheritingCount === 1 ? "its" : "their"
            } own inherit the ${unit}'s, so the real figure runs higher.`}
        </p>
      </div>
    </div>
  );
}
