"use client";

import { CostDashboard } from "@/components/app/cost-dashboard";
import { BudgetHub } from "@/components/app/budget-hub";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ScopeChip } from "@/components/app/scope-indicator";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { hasPermission } from "@/lib/auth/permissions";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

export default function CostPage() {
  const session = useSession({ required: true });
  const { scope, level, isOrgWide, bindings, managedBusinessUnitIds } = useAccessScope();

  // T-9.2-10: defense-in-depth client gate — server enforces
  // require_permission("cost:view") authoritatively (RLS + permission
  // dependency). Tightened from artifact:view to cost:view per matrix
  // (Phase 6: developer has artifact:view but NOT cost:view).
  if (!hasPermission(session, "cost:view")) {
    return (
      <RestrictedAccess description="Cost visibility requires the cost:view permission. Ask your admin for access." />
    );
  }

  const unitBindings = bindings.filter((b) => b.kind === "business_unit");
  const projectBindings = bindings.filter((b) => b.kind === "project");
  const scopeName = isOrgWide
    ? null
    : level === "business_unit"
      ? (managedBusinessUnitIds.length === 1
          ? unitBindings.find((b) => b.scopeId === managedBusinessUnitIds[0])?.scopeName
          : undefined) ?? `${managedBusinessUnitIds.length} ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`
      : projectBindings.length === 1
        ? projectBindings[0]!.scopeName
        : `${projectBindings.length} projects`;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      {/* Editorial page header — mirrors the audit log surface */}
      <header
        className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div>
          <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
            <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
            Observe
          </div>
          <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
            Cost
          </h1>

          {scope !== null && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <ScopeChip
                kind={isOrgWide ? "organization" : level}
                name={scopeName}
                size="sm"
              />
            </div>
          )}

          <p className="text-muted-foreground mt-2 max-w-[560px] text-[14px]">
            {/* Spend figures are scope-filtered server-side (app/api/cost),
                so the totals below are the viewer's own. Saying "your tenant's
                spend" to a Business Unit Admin would misdescribe a number that
                excludes every sibling unit. */}
            {isOrgWide
              ? "Your organisation's LLM spend by agent and model, across every business unit."
              : level === "business_unit"
                ? `LLM spend by agent and model for the ${
                    managedBusinessUnitIds.length === 1
                      ? BUSINESS_UNIT_LABEL.toLowerCase()
                      : `${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`
                  } you administer. Other ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} are excluded from every total here.`
                : "LLM spend by agent and model for the projects you're assigned to."}
          </p>
        </div>
      </header>

      <CostDashboard />
      <BudgetHub />
    </div>
  );
}
