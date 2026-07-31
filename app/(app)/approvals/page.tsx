"use client";

import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { hasPermission } from "@/lib/auth/permissions";
import { ApprovalQueue } from "@/components/app/approval-queue";
import { PersonaBadge, ScopeChip } from "@/components/app/scope-indicator";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

export default function ApprovalsPage() {
  const session = useSession({ required: true });
  const { scope, role, level, isOrgWide, bindings, managedBusinessUnitIds } = useAccessScope();

  // artifact:view is the floor for agent-run approval gates — approver roles
  // all hold it, and stakeholders without it see an empty queue since no gate
  // routes to them. workspace:manage is the separate floor for governance
  // approvals (project creation, model credentials), held by org_admin/
  // bu_admin, who hold neither artifact:view nor any delivery-tier permission
  // (PRD §14.8's governance tier never touches agent runs) — so either one
  // earns access to this page. Backend stays authoritative either way.
  const canSeeQueue =
    hasPermission(session, "artifact:view") || hasPermission(session, "workspace:manage");
  if (!canSeeQueue) {
    return (
      <RestrictedAccess description="Approvals require the artifact:view or workspace:manage permission. Ask your admin for access." />
    );
  }

  // Name the scope the queue is drawn from. A multi-scope viewer gets a count
  // rather than an arbitrary first name — picking one would misrepresent the
  // queue as narrower than it is.
  const unitBindings = bindings.filter((b) => b.kind === "business_unit");
  const projectBindings = bindings.filter((b) => b.kind === "project");
  const scopeName = isOrgWide
    ? null
    : level === "business_unit"
      ? (managedBusinessUnitIds.length === 1
          ? unitBindings.find((b) => b.scopeId === managedBusinessUnitIds[0])?.scopeName
          : undefined) ?? `${managedBusinessUnitIds.length} business units`
      : projectBindings.length === 1
        ? projectBindings[0]!.scopeName
        : `${projectBindings.length} projects`;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <header
        className="flex flex-col items-start gap-1"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
          <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
          Act
        </div>
        <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
          Approvals
        </h1>

        {/* An approval queue is the one screen where an unstated boundary is
            actively dangerous: "you're all caught up" must be legible as "in
            Payments", not as "in the organisation". */}
        {scope !== null && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <ScopeChip kind={isOrgWide ? "organization" : level} name={scopeName} size="sm" />
            <PersonaBadge role={role} />
          </div>
        )}

        <p className="text-muted-foreground mt-2 max-w-[560px] text-[14px]">
          {isOrgWide
            ? "Everything across the organisation that's waiting on your decision — phase sign-offs, agent questions and governance requests, routed to your role."
            : level === "business_unit"
              ? `Everything in your ${BUSINESS_UNIT_LABEL.toLowerCase()} that's waiting on your decision. Approvals from other ${BUSINESS_UNIT_LABEL.toLowerCase()}s are not shown and are not yours to make.`
              : "Everything across the projects you're assigned to that's waiting on your decision — phase sign-offs and agent questions, routed to your role."}
        </p>
      </header>

      <ApprovalQueue />
    </div>
  );
}
