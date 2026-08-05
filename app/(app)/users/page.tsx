"use client";

import * as React from "react";
import Link from "next/link";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Search, ShieldCheck, UserPlus, Users as UsersIcon } from "lucide-react";

import { PageTitle } from "@/components/app/page-title";
import { ManageUserRolesDialog } from "@/components/app/manage-user-roles-dialog";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSession } from "@/hooks/use-session";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useAccessScope } from "@/hooks/use-access-scope";
import { ScopeChip } from "@/components/app/scope-indicator";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { listWorkspaceMembers } from "@/lib/api/workspaces";
import { listProjectMembers } from "@/lib/api/project-members";
import { listProjects } from "@/lib/api/projects";
import { onboardPerson } from "@/lib/api/onboarding";
import { qk } from "@/lib/api/query-keys";
import { ROLE_META, ROLE_ORDER, scopeTierConflicts, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { OnboardPersonDialog } from "@/components/app/onboard-person-dialog";
import {
  resolveRoleLabel,
  toRoleOption,
  useAllCustomRoles,
  useCustomRolesForScope,
  type AssignableRole,
} from "@/hooks/use-assignable-roles";

// Every built-in role an Org Admin may appoint someone into from this
// directory — governance tier plus every delivery-tier contributor. Merged
// with any business_unit-scoped custom role below (not `custom` itself,
// which is a placeholder, not a real role).
const INVITE_BUILTIN_ROLES = ROLE_ORDER.filter((r) => r !== "custom");

/**
 * Users — the organisation-wide people directory (PRD §36, §15.2).
 *
 * Organization Admin: invite people, deactivate leavers, reset sign-in
 * factors, and see where each person belongs.
 * Business Unit Admin: view only, scoped to its own unit (PRD §35).
 *
 * Shows every role a person holds, across every scope — Business Units AND
 * projects. A role is a binding of (person, scope, role): the same person
 * can be Developer in one Business Unit and Architect in another, or Project
 * Admin on one project and BA on another (PRD §33.1). Click a name for the
 * full breakdown, including permissions and agent-access levels.
 *
 * The directory is composed client-side from the existing per-unit and
 * per-project member endpoints; no new list API is introduced (the
 * click-through detail view does introduce one — see app/(app)/users/[id]).
 */

interface DirectoryBinding {
  scope: "workspace" | "project";
  id: string;
  name: string;
  /** For a project binding, its parent Business Unit — used to nest it. */
  parentId?: string;
  parentName?: string;
  role: string;
}

interface DirectoryRow {
  userId: string;
  displayName: string;
  email: string | null;
  initials: string;
  /** Every (scope, role) binding this person holds. */
  bindings: DirectoryBinding[];
}

/**
 * One Business Unit's worth of a person's bindings: the role they hold in the
 * unit itself (if any), plus their role on each project inside it.
 *
 * Grouping this way is the whole point of the page — the same person can be
 * BU Admin of Payments, Project Admin on a Lending project and BA on another,
 * and a flat chip row makes that unreadable past two or three bindings.
 */
interface ScopeGroup {
  unitId: string;
  unitName: string;
  /** Roles held on the Business Unit itself. */
  unitRoles: string[];
  projects: { id: string; name: string; roles: string[] }[];
  /** True when this one unit mixes governance and delivery — see below. */
  conflict: boolean;
}

/** Projects whose parent Business Unit we couldn't resolve. */
const UNGROUPED = "__ungrouped__";

function groupBindings(bindings: DirectoryBinding[]): ScopeGroup[] {
  const byUnit = new Map<string, ScopeGroup>();
  const group = (id: string, name: string) => {
    let g = byUnit.get(id);
    if (!g) {
      g = { unitId: id, unitName: name, unitRoles: [], projects: [], conflict: false };
      byUnit.set(id, g);
    }
    return g;
  };

  for (const b of bindings) {
    if (b.scope === "workspace") {
      group(b.id, b.name).unitRoles.push(b.role);
    } else {
      const g = group(b.parentId ?? UNGROUPED, b.parentName ?? "Other projects");
      const existing = g.projects.find((p) => p.id === b.id);
      if (existing) existing.roles.push(b.role);
      else g.projects.push({ id: b.id, name: b.name, roles: [b.role] });
    }
  }

  // Separation of duties is a per-scope rule: holding a governance role in one
  // unit and a delivery role in another is legitimate, so the check runs once
  // per scope rather than once per person (lib/roles.ts::scopeTierConflicts).
  for (const g of byUnit.values()) {
    const conflicts = scopeTierConflicts([
      { scopeId: g.unitId, roles: g.unitRoles },
      ...g.projects.map((p) => ({ scopeId: p.id, roles: p.roles })),
    ]);
    g.conflict = conflicts.length > 0;
    g.projects.sort((a, b2) => a.name.localeCompare(b2.name));
  }

  return [...byUnit.values()].sort((a, b) => {
    if (a.unitId === UNGROUPED) return 1;
    if (b.unitId === UNGROUPED) return -1;
    return a.unitName.localeCompare(b.unitName);
  });
}

/** Maps a stored role name onto the platform's twelve roles, when it is one. */
function asPlatformRole(name: string): PlatformRole | null {
  return name in ROLE_META ? (name as PlatformRole) : null;
}

/**
 * A single role held in a single scope. Governance roles carry the shield and
 * the brand tint so the tier a binding sits in is legible without reading the
 * label — which is what makes a mixed-tier *person* scannable now that mixing
 * across scopes is legitimate.
 */
function RoleChip({ label, role }: { label: string; role: PlatformRole | null }) {
  const governance = role !== null && ROLE_META[role].tier === "governance";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10.5px]",
        governance
          ? "border-brand-bright/35 bg-brand-bright/10 text-brand-bright"
          : "border-line-soft bg-surface-1 text-muted-foreground",
      )}
      title={governance ? "Governance role" : "Delivery role"}
    >
      {governance && <ShieldCheck className="size-3" aria-hidden />}
      {label}
    </span>
  );
}

export default function UsersPage() {
  const session = useSession({ required: true });
  const role = effectivePlatformRole(session);
  const [query, setQuery] = React.useState("");
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [managing, setManaging] = React.useState<DirectoryRow | null>(null);
  const queryClient = useQueryClient();

  const canManage = hasPermission(session, "member:manage");
  const isOrgAdmin = role === "org_admin";

  // Custom roles scoped to a business unit are offerable in the invite picker
  // too — merged with the built-in list (see hooks/use-assignable-roles.ts).
  const customBuRoles = useCustomRolesForScope("business_unit");
  const inviteRoleOptions: AssignableRole[] = React.useMemo(
    () => [...INVITE_BUILTIN_ROLES.map(toRoleOption), ...customBuRoles],
    [customBuRoles],
  );
  // A binding's role can be ANY custom role (business_unit- or
  // project-scoped) — label resolution needs the full list, not just the
  // invite picker's business_unit-scoped subset.
  const allCustomRoles = useAllCustomRoles();
  const roleLabel = React.useCallback(
    (name: string) => resolveRoleLabel(name, allCustomRoles),
    [allCustomRoles],
  );

  const { scope, level, isOrgWide, managedBusinessUnitIds } = useAccessScope();

  // `useWorkspaces()` is already scope-filtered server-side, so the per-unit
  // member queries below can only ever join units the viewer may read — which
  // is what finally makes the "scoped to your own Business Unit" note at the
  // bottom of this page true. It previously said that while listing every unit
  // in the organisation, because /api/workspaces returned all of them.
  const workspacesQ = useWorkspaces();
  const units = React.useMemo(
    () => (workspacesQ.data ?? []).filter((w) => w.status === "active"),
    [workspacesQ.data],
  );

  // One query per unit; the directory is the join of their member lists.
  const memberQueries = useQueries({
    queries: units.map((u) => ({
      queryKey: qk.workspaces.members(u.id),
      queryFn: () => listWorkspaceMembers(u.id),
      staleTime: 60_000,
    })),
  });

  const projectsQ = useQuery({
    queryKey: ["users-directory", "projects"],
    queryFn: () => listProjects({ pageSize: 200 }),
    staleTime: 60_000,
  });
  const projects = React.useMemo(() => projectsQ.data?.items ?? [], [projectsQ.data]);

  // One query per project; folded into the same directory join as the
  // per-unit member lists, mirroring that exact pattern.
  const projectMemberQueries = useQueries({
    queries: projects.map((p) => ({
      queryKey: qk.projectMembers.list(p.id),
      queryFn: () => listProjectMembers(p.id),
      staleTime: 60_000,
    })),
  });

  const loading =
    workspacesQ.isLoading ||
    projectsQ.isLoading ||
    memberQueries.some((q) => q.isLoading) ||
    projectMemberQueries.some((q) => q.isLoading);

  const memberQueriesUpdatedAt = memberQueries.map((q) => q.dataUpdatedAt).join(",");
  const projectMemberQueriesUpdatedAt = projectMemberQueries.map((q) => q.dataUpdatedAt).join(",");

  const rows = React.useMemo<DirectoryRow[]>(() => {
    const byUser = new Map<string, DirectoryRow>();
    const getRow = (userId: string, displayName: string, email: string | null, initials: string) => {
      const existing = byUser.get(userId);
      if (existing) return existing;
      const created: DirectoryRow = { userId, displayName, email, initials, bindings: [] };
      byUser.set(userId, created);
      return created;
    };

    memberQueries.forEach((q, i) => {
      const unit = units[i];
      if (!unit || !q.data) return;
      for (const m of q.data) {
        const row = getRow(m.userId, m.displayName ?? m.email ?? m.userId, m.email ?? null, m.initials);
        row.bindings.push({ scope: "workspace", id: unit.id, name: unit.displayName, role: m.roleName });
      }
    });

    projectMemberQueries.forEach((q, i) => {
      const project = projects[i];
      if (!project || !q.data) return;
      const parentUnit = units.find((u) => u.id === project.workspaceId);
      for (const m of q.data) {
        const row = getRow(
          m.identity.ssoSubject,
          m.identity.displayName,
          m.identity.email,
          m.identity.initials,
        );
        row.bindings.push({
          scope: "project",
          id: project.id,
          name: project.name,
          parentId: parentUnit?.id,
          parentName: parentUnit?.displayName,
          role: m.role,
        });
      }
    });

    return [...byUser.values()].sort((a, b) =>
      a.displayName.localeCompare(b.displayName),
    );
    // memberQueries/projectMemberQueries are recreated every render by
    // useQueries — depend on their derived dataUpdatedAt strings instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [units, projects, memberQueriesUpdatedAt, projectMemberQueriesUpdatedAt]);

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.displayName.toLowerCase().includes(q) ||
        (r.email ?? "").toLowerCase().includes(q) ||
        r.bindings.some(
          (b) =>
            b.name.toLowerCase().includes(q) ||
            (b.parentName ?? "").toLowerCase().includes(q) ||
            roleLabel(b.role).toLowerCase().includes(q),
        ),
    );
  }, [rows, query, roleLabel]);

  // Users is an admin surface: Organization Admin manages, Business Unit Admin
  // views its own unit. Builders have no access at all (PRD §35).
  if (!canManage) {
    return (
      <RestrictedAccess description="The people directory is an administrator surface. Ask your Organization Admin if you need access." />
    );
  }

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
        <div className="flex w-full flex-wrap items-end justify-between gap-4">
          <div>
            <PageTitle>Users</PageTitle>

            {scope !== null && (
              <div className="flex flex-wrap items-center gap-2">
                <ScopeChip
                  kind={isOrgWide ? "organization" : level}
                  name={
                    isOrgWide
                      ? null
                      : units.length === 1
                        ? units[0]!.displayName
                        : `${units.length} ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`
                  }
                  size="sm"
                />
              </div>
            )}
          </div>

          {isOrgAdmin && (
            <Button className="gap-2" onClick={() => setInviteOpen(true)}>
              <UserPlus className="size-4" aria-hidden />
              Invite people
            </Button>
          )}
        </div>
      </header>

      {/* Search */}
      <div className="relative max-w-sm">
        <Search
          className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
          aria-hidden
        />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search people, units, projects or roles…"
          className="border-line-soft bg-surface-1 pl-9"
          aria-label="Search the people directory"
        />
      </div>

      {loading ? (
        <LoadingState variant="list" rows={5} />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title={query ? "No one matches that search" : "No people yet"}
          description={
            query
              ? "Try a different name, email, business unit, project or role."
              : "Invite people to the organisation, then assign their roles per scope on Roles & Access."
          }
        />
      ) : (
        <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="border-line-soft hover:bg-transparent">
                  <TableHead className="text-muted-foreground font-mono text-[10.5px] tracking-widest uppercase">
                    Person
                  </TableHead>
                  <TableHead className="text-muted-foreground font-mono text-[10.5px] tracking-widest uppercase">
                    Roles by scope
                  </TableHead>
                  <TableHead className="text-muted-foreground w-24 text-right font-mono text-[10.5px] tracking-widest uppercase">
                    Scopes
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((r) => {
                  const groups = groupBindings(r.bindings);
                  const conflicted = groups.filter((g) => g.conflict);

                  return (
                    <TableRow key={r.userId} className="border-line-soft">
                      <TableCell className="py-3">
                        <div className="flex items-center gap-3">
                          <span
                            className="bg-surface-2 text-foreground/80 flex size-8 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-medium"
                            aria-hidden
                          >
                            {r.initials}
                          </span>
                          <span className="min-w-0">
                            <Link
                              href={`/users/${encodeURIComponent(r.userId)}`}
                              className="hover:text-brand-bright block text-[13px] font-medium underline-offset-2 hover:underline"
                            >
                              {r.displayName}
                            </Link>
                            {r.email && (
                              <span className="text-muted-foreground block truncate text-[11.5px]">
                                {r.email}
                              </span>
                            )}
                          </span>
                        </div>
                      </TableCell>

                      {/* One block per Business Unit, its projects nested
                          beneath — so "BU Admin here, BA over there" reads at
                          a glance instead of as an undifferentiated chip row. */}
                      <TableCell className="py-3">
                        <div className="flex flex-col gap-2.5">
                          {groups.map((g) => (
                            <div key={g.unitId} className="flex flex-col gap-1">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-foreground text-[12.5px] font-medium">
                                  {g.unitName}
                                </span>
                                {g.unitRoles.map((rn) => (
                                  <RoleChip key={rn} label={roleLabel(rn)} role={asPlatformRole(rn)} />
                                ))}
                                {g.conflict && (
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <Badge variant="destructive" className="font-mono text-[9.5px]">
                                        Conflict
                                      </Badge>
                                    </TooltipTrigger>
                                    <TooltipContent side="left" className="max-w-[260px]">
                                      Within this one scope they hold both a
                                      governance and a delivery role, which
                                      would let them approve their own work.
                                      Roles in <em>different</em> scopes are
                                      fine — review on Roles &amp; Access.
                                    </TooltipContent>
                                  </Tooltip>
                                )}
                              </div>

                              {g.projects.length > 0 && (
                                <ul className="border-line-soft ml-1 flex flex-col gap-1 border-l pl-3">
                                  {g.projects.map((p) => (
                                    <li key={p.id} className="flex flex-wrap items-center gap-1.5">
                                      <span className="text-muted-foreground text-[11.5px]">
                                        {p.name}
                                      </span>
                                      {p.roles.map((rn) => (
                                        <RoleChip
                                          key={rn}
                                          label={roleLabel(rn)}
                                          role={asPlatformRole(rn)}
                                        />
                                      ))}
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          ))}
                        </div>
                      </TableCell>

                      <TableCell className="py-3 text-right align-top">
                        <span className="text-muted-foreground block font-mono text-[10.5px]">
                          {groups.length} {groups.length === 1 ? "unit" : "units"}
                        </span>
                        {conflicted.length > 0 && (
                          <span className="text-destructive mt-0.5 block font-mono text-[10px]">
                            {conflicted.length} conflict{conflicted.length === 1 ? "" : "s"}
                          </span>
                        )}
                        {/* Assigning lives on the row it applies to. It used to
                            be a screen of its own that made you pick a scope
                            first and then hunt the person inside it. */}
                        {isOrgAdmin && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setManaging(r)}
                            aria-label={`Manage roles for ${r.displayName}`}
                            className="border-line-soft mt-1.5 h-7 px-2 text-[11px]"
                          >
                            Manage roles
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {managing && (
        <ManageUserRolesDialog
          open
          onOpenChange={(o) => !o && setManaging(null)}
          userId={managing.userId}
          displayName={managing.displayName}
          bindings={managing.bindings.map((b) => ({
            scopeId: b.id,
            scopeName: b.name,
            role: b.role,
          }))}
        />
      )}

      {!isOrgAdmin && (
        <p className="text-muted-foreground text-[12.5px]">
          {units.length === 1
            ? `You are viewing this directory scoped to ${units[0]!.displayName}`
            : `You are viewing this directory scoped to the ${units.length} ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} you administer`}
          . People in {managedBusinessUnitIds.length === 1 ? "other" : "any other"}{" "}
          {BUSINESS_UNIT_LABEL.toLowerCase()} are not listed. Inviting and deactivating people is an
          Organization Admin action.
        </p>
      )}

      {isOrgAdmin && (
        <OnboardPersonDialog
          open={inviteOpen}
          onOpenChange={setInviteOpen}
          roleOptions={inviteRoleOptions}
          workspaceOptions={units.map((u) => ({ id: u.id, displayName: u.displayName }))}
          title={`Invite to the ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`}
          description="Onboards a new or existing person and grants them a role in the chosen Business Unit."
          onSubmit={async (input) => {
            const result = await onboardPerson({
              email: input.email,
              displayName: input.displayName,
              workspaceId: input.workspaceId!,
              role: input.roleName,
            });
            toast.success(`${result.displayName} invited as ${roleLabel(result.role)}`);
            queryClient.invalidateQueries({ queryKey: qk.workspaces.members(input.workspaceId!) });
          }}
        />
      )}
    </div>
  );
}
