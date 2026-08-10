"use client";

import { PageTitle } from "@/components/app/page-title";
import { CostDashboard } from "@/components/app/cost-dashboard";
import { BudgetHub } from "@/components/app/budget-hub";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ScopeChip } from "@/components/app/scope-indicator";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { hasPermission } from "@/lib/auth/permissions";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

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
          <PageTitle>Cost</PageTitle>

          {/* The chip stays, and matters more here than anywhere: every total
              below is scope-filtered server-side, so a Business Unit Admin's
              figures exclude every sibling. An unlabelled number would read as
              the organisation's. */}
          {scope !== null && (
            <div className="flex flex-wrap items-center gap-2">
              <ScopeChip
                kind={isOrgWide ? "organization" : level}
                name={scopeName}
                size="sm"
              />
            </div>
          )}
        </div>
      </header>

      <CostDashboard />
      <BudgetHub />
    </div>
  );
}
