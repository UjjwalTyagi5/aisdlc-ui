"use client";

import * as React from "react";
import Link from "next/link";
import {
  Building2,
  FolderKanban,
  Globe2,
  Lock,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import { PageTitle } from "@/components/app/page-title";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PersonaBadge } from "@/components/app/scope-indicator";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useAllCustomRoles, resolveRoleLabel } from "@/hooks/use-assignable-roles";
import { PERMISSION_CATALOG } from "@/lib/auth/permission-catalog";
import {
  AGENT_OWNERSHIP,
  ROLE_META,
  scopeTierConflicts,
  type Involvement,
  type PlatformRole,
} from "@/lib/roles";
import { PHASE_LABEL, PHASE_ORDER } from "@/lib/agents";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { ScopeBinding } from "@/lib/schemas/access-scope";

/**
 * My access — "what am I allowed to do, and where".
 *
 * Every other RBAC surface on the platform answers that question for SOMEONE
 * ELSE: the people directory and its detail view are admin surfaces gated on
 * `member:manage`, and Roles & Access needs the same. So the one person who
 * could never see their own effective permissions was the contributor — the
 * person most likely to be confused by a page that renders without the button
 * they expected, or a project list shorter than a colleague's.
 *
 * Open to every role by design. There is nothing to gate: it discloses only the
 * viewer's own bindings and their own permission set, both of which their
 * session already carries. Read-only — changing an assignment is Roles & Access,
 * and pointing at it from here is the whole affordance.
 */

const RISE = {
  animationName: "rise",
  animationDuration: "0.55s",
  animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  animationFillMode: "both",
} as const;

/** What each involvement level actually lets you do, in one line. */
const INVOLVEMENT_MEANING: Record<Involvement, string> = {
  owner: "Chat with it, and approve its consequential actions and sign-offs.",
  primary: "Its primary human user — you run it and you approve its gates.",
  build: "You do the hands-on work; its owner approves the consequential parts.",
  requests: "You can ask it for something, but neither run nor approve it.",
  use: "You can chat with it and run its safe capabilities only.",
  none: "No access.",
};

const INVOLVEMENT_TONE: Record<Involvement, string> = {
  owner: "text-brand-bright",
  primary: "text-brand-bright",
  build: "text-foreground",
  requests: "text-muted-foreground",
  use: "text-muted-foreground",
  none: "text-muted-foreground/50",
};

/** Flat id → catalog entry, so a session permission can be explained. */
const PERM_BY_ID = new Map(
  PERMISSION_CATALOG.flatMap((g) =>
    g.perms.map((p) => [p.id, { ...p, group: g.group }] as const),
  ),
);

function asPlatformRole(name: string): PlatformRole | null {
  return name in ROLE_META ? (name as PlatformRole) : null;
}

export default function MyAccessPage() {
  const session = useSession({ required: true });
  const { role, scope, isOrgWide, allBindings, managedBusinessUnitIds, managedProjectIds, isLoading } =
    useAccessScope();
  const customRoles = useAllCustomRoles();
  const roleLabel = React.useCallback(
    (name: string) => resolveRoleLabel(name, customRoles),
    [customRoles],
  );

  // Memoised because the `?? []` fallback would otherwise mint a new array
  // every render and re-run the permission grouping below on each one.
  const permissions = React.useMemo(() => session.permissions ?? [], [session.permissions]);
  const isWildcard = permissions.includes("admin:*");

  const unitBindings = allBindings.filter((b) => b.kind === "business_unit");
  const projectBindings = allBindings.filter((b) => b.kind === "project");

  // Separation of duties is per-scope: holding governance in one unit and
  // delivery in another is legitimate, so the check runs once per scope rather
  // than once per person ([[per-scope-tier-separation]]).
  const conflicts = React.useMemo(() => {
    const byScope = new Map<string, string[]>();
    for (const b of allBindings) {
      byScope.set(b.scopeId, [...(byScope.get(b.scopeId) ?? []), b.role]);
    }
    return new Set(
      scopeTierConflicts([...byScope.entries()].map(([scopeId, roles]) => ({ scopeId, roles }))),
    );
  }, [allBindings]);

  // Explain every permission the session actually carries. The wildcard is
  // rendered as itself rather than expanded into the full catalogue: "you hold
  // everything" is the honest description, and listing 30 chips implies a
  // finite enumeration that isn't what `admin:*` means.
  const explained = React.useMemo(
    () =>
      permissions
        .filter((p) => p !== "admin:*")
        .map((id) => PERM_BY_ID.get(id) ?? { id, label: id, grants: undefined, group: "Other" })
        .sort((a, b) => a.group.localeCompare(b.group) || a.label.localeCompare(b.label)),
    [permissions],
  );
  const permGroups = React.useMemo(() => {
    const byGroup = new Map<string, typeof explained>();
    for (const p of explained) byGroup.set(p.group, [...(byGroup.get(p.group) ?? []), p]);
    return [...byGroup.entries()];
  }, [explained]);

  const agentRows = React.useMemo(() => {
    if (!role) return [];
    return PHASE_ORDER.map((phase) => ({
      phase,
      label: PHASE_LABEL[phase],
      level: AGENT_OWNERSHIP[role][phase],
    })).filter((r) => r.level !== "none");
  }, [role]);

  const meta = role ? ROLE_META[role] : null;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <header className="flex flex-col items-start gap-1" style={RISE}>
        <PageTitle>My access</PageTitle>
        <div className="flex flex-wrap items-center gap-2">
          <PersonaBadge role={role} showScope />
          {isWildcard && (
            <Badge variant="secondary" className="font-mono text-[10.5px]">
              admin:* — organization-wide
            </Badge>
          )}
        </div>
        <p className="text-muted-foreground mt-2 max-w-[640px] text-[14px]">
          Exactly what you can do on this platform and where. A role is a binding of person, scope
          and role — so the same account can govern one{" "}
          {BUSINESS_UNIT_LABEL.toLowerCase()} and contribute to a project in another. Roles are
          granted by an admin on{" "}
          <span className="text-foreground">Roles &amp; Access</span>; this page only reports them.
        </p>
      </header>

      {isLoading ? (
        <LoadingState variant="card" />
      ) : (
        <>
          {/* ── Reach ────────────────────────────────────────────────────── */}
          <section className="space-y-3" style={{ ...RISE, animationDelay: "0.04s" }}>
            <h2 className="font-display text-[15px] font-bold tracking-[-0.01em]">Your reach</h2>

            {isOrgWide ? (
              <div className="border-brand-bright/30 bg-brand-bright/5 flex items-start gap-3 rounded-2xl border p-4">
                <Globe2 className="text-brand-bright mt-0.5 size-4 shrink-0" aria-hidden />
                <div>
                  <p className="text-[13.5px] font-medium">The whole organisation</p>
                  <p className="text-muted-foreground mt-1 text-[12.5px]">
                    Your role is bound at the organization scope, so every{" "}
                    {BUSINESS_UNIT_LABEL.toLowerCase()} and project is visible to you. Nothing on
                    this platform is filtered out of your view.
                  </p>
                </div>
              </div>
            ) : allBindings.length === 0 ? (
              <EmptyState
                icon={Lock}
                title="You aren't assigned to anything yet"
                description={`You have a platform account but no ${BUSINESS_UNIT_LABEL.toLowerCase()} or project membership, so most screens will be empty. Ask a ${BUSINESS_UNIT_LABEL} Admin to add you.`}
              />
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                <ScopeGroupCard
                  icon={Building2}
                  title={`${BUSINESS_UNIT_LABEL_PLURAL} you belong to`}
                  bindings={unitBindings}
                  managedIds={managedBusinessUnitIds}
                  conflicts={conflicts}
                  roleLabel={roleLabel}
                  hrefFor={(b) => `/workspaces/${b.scopeId}`}
                  emptyText={`No ${BUSINESS_UNIT_LABEL.toLowerCase()} membership. You may still belong to individual projects.`}
                />
                <ScopeGroupCard
                  icon={FolderKanban}
                  title="Projects you belong to"
                  bindings={projectBindings}
                  managedIds={managedProjectIds}
                  conflicts={conflicts}
                  roleLabel={roleLabel}
                  hrefFor={(b) => `/projects/${b.scopeId}`}
                  emptyText="No project membership yet."
                />
              </div>
            )}

            {/* The boundary, said out loud. Absence of a menu item is not an
                explanation, and a scoped viewer comparing notes with a
                colleague deserves one. */}
            {scope !== null && !isOrgWide && allBindings.length > 0 && (
              <p className="border-line-soft text-muted-foreground rounded-lg border border-dashed px-4 py-3 text-[12.5px]">
                <Lock className="mr-1.5 -mt-0.5 inline size-3.5" aria-hidden />
                Everything outside these scopes is hidden from you — not merely read-only.
                Dashboards, approvals, costs, traces and the audit trail all count only what is
                listed above, which is why your totals can differ from a colleague&apos;s on the
                same screen.
              </p>
            )}
          </section>

          {/* ── Effective permissions ────────────────────────────────────── */}
          <section className="space-y-3" style={{ ...RISE, animationDelay: "0.08s" }}>
            <h2 className="font-display text-[15px] font-bold tracking-[-0.01em]">
              What you can do
            </h2>

            {isWildcard ? (
              <div className="border-line-soft bg-panel-elevated rounded-2xl border p-4">
                <p className="text-[13px] font-medium">Every permission on the platform</p>
                <p className="text-muted-foreground mt-1 text-[12.5px]">
                  You hold the <span className="font-mono">admin:*</span> wildcard, which passes
                  every permission check rather than enumerating one. It does not, however, make
                  you an agent owner: approvals still route to the role that owns each agent, and a
                  fallback approval is always recorded as a fallback.
                </p>
              </div>
            ) : permGroups.length === 0 ? (
              <EmptyState
                icon={Lock}
                title="No permissions resolved"
                description="Your session carries no permissions, so every action is unavailable. This usually means your role assignment hasn't finished — ask your admin to check it."
              />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {permGroups.map(([group, perms]) => (
                  <div
                    key={group}
                    className="border-line-soft bg-panel-elevated space-y-2 rounded-2xl border p-4"
                  >
                    <p className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
                      {group}
                    </p>
                    <ul className="space-y-2">
                      {perms.map((p) => (
                        <li key={p.id}>
                          <p className="text-[12.5px] font-medium">{p.label}</p>
                          {p.grants && (
                            <p className="text-muted-foreground mt-0.5 text-[11.5px] leading-snug">
                              {p.grants}
                            </p>
                          )}
                          <p className="text-muted-foreground/70 mt-0.5 font-mono text-[10px]">
                            {p.id}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ── Agent access ─────────────────────────────────────────────── */}
          <section className="space-y-3" style={{ ...RISE, animationDelay: "0.12s" }}>
            <h2 className="font-display text-[15px] font-bold tracking-[-0.01em]">
              Your agent access
            </h2>

            {meta?.governanceOnly ? (
              <div className="border-line-soft bg-panel-elevated flex items-start gap-3 rounded-2xl border p-4">
                <ShieldCheck className="text-brand-bright mt-0.5 size-4 shrink-0" aria-hidden />
                <div>
                  <p className="text-[13.5px] font-medium">None — and that is deliberate</p>
                  <p className="text-muted-foreground mt-1 text-[12.5px]">
                    Governance roles have no agent access at all. You set structure, limits and
                    visibility; you do not run agents or approve delivery work, so nobody can
                    govern the work they themselves produced. To unblock a stalled gate, reassign
                    the agent&apos;s owner rather than approving it yourself.
                  </p>
                </div>
              </div>
            ) : agentRows.length === 0 ? (
              <p className="text-muted-foreground text-[13px]">
                No agent access resolved for your role.
              </p>
            ) : (
              <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-2xl border">
                <ul className="divide-line-soft divide-y">
                  {agentRows.map((r) => (
                    <li
                      key={r.phase}
                      className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5"
                    >
                      <span className="text-[12.5px] font-medium">{r.label}</span>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            className={cn(
                              "font-mono text-[11px] capitalize",
                              INVOLVEMENT_TONE[r.level],
                            )}
                          >
                            {r.level}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent side="left" className="max-w-[280px]">
                          {INVOLVEMENT_MEANING[r.level]}
                        </TooltipContent>
                      </Tooltip>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-muted-foreground text-[12.5px]">
              Agent access applies within the projects listed above — never outside them. The full
              role × agent matrix is on{" "}
              <Link
                href="/admin/access/roles"
                className="text-foreground underline underline-offset-2"
              >
                Roles &amp; Access → Built-in roles
              </Link>
              , if you can open it.
            </p>
          </section>
        </>
      )}
    </div>
  );
}

// ─── Scope group card ─────────────────────────────────────────────────────────

function ScopeGroupCard({
  icon: Icon,
  title,
  bindings,
  managedIds,
  conflicts,
  roleLabel,
  hrefFor,
  emptyText,
}: {
  icon: typeof Building2;
  title: string;
  bindings: ScopeBinding[];
  managedIds: string[];
  conflicts: Set<string>;
  roleLabel: (name: string) => string;
  hrefFor: (b: ScopeBinding) => string;
  emptyText: string;
}) {
  return (
    <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-2xl border">
      <div className="border-line-soft flex items-center gap-2 border-b px-4 py-3">
        <Icon className="text-muted-foreground size-4 shrink-0" aria-hidden />
        <span className="font-display text-[13.5px] font-semibold tracking-tight">{title}</span>
        <span className="text-muted-foreground ml-auto font-mono text-[11px]">
          {bindings.length}
        </span>
      </div>

      {bindings.length === 0 ? (
        <p className="text-muted-foreground px-4 py-5 text-[12.5px]">{emptyText}</p>
      ) : (
        <ul className="divide-line-soft divide-y">
          {bindings.map((b) => {
            const platformRole = asPlatformRole(b.role);
            const governance = platformRole !== null && ROLE_META[platformRole].tier === "governance";
            const administers = managedIds.includes(b.scopeId);
            return (
              <li key={`${b.kind}-${b.scopeId}-${b.role}`} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href={hrefFor(b)}
                    className="hover:text-brand-bright text-[13px] font-medium underline-offset-2 hover:underline"
                  >
                    {b.scopeName}
                  </Link>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10.5px]",
                      governance
                        ? "border-brand-bright/35 bg-brand-bright/10 text-brand-bright"
                        : "border-line-soft bg-surface-1 text-muted-foreground",
                    )}
                  >
                    {governance ? (
                      <ShieldCheck className="size-3" aria-hidden />
                    ) : (
                      <Wrench className="size-3" aria-hidden />
                    )}
                    {roleLabel(b.role)}
                  </span>
                  {administers && (
                    <Badge variant="secondary" className="font-mono text-[9.5px]">
                      You administer
                    </Badge>
                  )}
                  {b.status !== "active" && (
                    <span className="text-muted-foreground font-mono text-[9.5px] tracking-wider uppercase">
                      {b.status}
                    </span>
                  )}
                  {conflicts.has(b.scopeId) && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge variant="destructive" className="font-mono text-[9.5px]">
                          Tier conflict
                        </Badge>
                      </TooltipTrigger>
                      <TooltipContent side="left" className="max-w-[260px]">
                        In this one scope you hold both a governance and a delivery role, which
                        would let you approve your own work. Roles in <em>different</em> scopes are
                        fine — ask an admin to review this one.
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
                {b.parentName && (
                  <p className="text-muted-foreground mt-0.5 text-[11.5px]">
                    in {b.parentName}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
