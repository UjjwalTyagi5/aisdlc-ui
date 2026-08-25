"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Building2,
  Lock,
  Eye,
  Search,
  ShieldCheck,
  Upload,
  UserPlus,
  Users as UsersIcon,
  X,
} from "lucide-react";

import { PageTitle } from "@/components/app/page-title";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
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
import { AssignBusinessUnitRoleDialog } from "@/components/app/assign-bu-role-dialog";
import { ChangeAppointmentDialog } from "@/components/app/change-appointment-dialog";
import { BulkOnboardDialog } from "@/components/app/bulk-onboard-dialog";
import { RequestCrossBuMemberDialog } from "@/components/app/request-cross-bu-member-dialog";
import { listProjects } from "@/lib/api/projects";
import { OnboardUserDialog } from "@/components/app/onboard-user-dialog";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { listCrossBuGrants, listUserDirectory, revokeCrossBuGrant } from "@/lib/api/users";
import { qk } from "@/lib/api/query-keys";
import { awaitsBusinessUnitRole, ROLE_META, type PlatformRole } from "@/lib/roles";
import { RoleHistoryDialog } from "@/components/app/role-history-dialog";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { resolveRoleLabel, useAllCustomRoles } from "@/hooks/use-assignable-roles";
import type { DirectoryEntry } from "@/lib/schemas/user-directory";

/**
 * Users & Roles — who is in the organisation, and what each of them holds.
 *
 * TWO PEOPLE ONBOARD SOMEONE, and this page is where the handover between them
 * happens (PRD §36, §15.2–§15.3):
 *
 *   Organization Admin  admits people and says whether each one RUNS a business
 *                       unit or WORKS in one. Two answers, and nothing more —
 *                       the eleven working roles are not theirs to guess at.
 *   Business Unit Admin picks up every Contributor placed in their unit and
 *                       says what that person actually does, from the built-in
 *                       roles or one they compose.
 *
 * READ ORG-WIDE, WRITE INSIDE YOUR OWN UNIT. Both admins above see every person
 * in the organisation; only the rows in units the viewer administers carry a
 * role control, and every other row says "view only" on its face. A Business
 * Unit Admin has to be able to FIND someone in another unit — they borrow
 * contributors across units, and the borrow dialog takes an email precisely
 * because it cannot offer a picker — so a directory that stopped at their own
 * boundary left that address unfindable anywhere in the product.
 *
 * A Project Admin is scoped to the unit they belong to: `member:manage` is
 * theirs over their projects' rosters, not over the organisation's people.
 * `scopeUserDirectory` (lib/mock/user-directory-fixtures.ts) draws both lines,
 * and both runtimes apply it — app/api/admin/users/route.ts and the MSW handler.
 *
 * Reading is not the only boundary — the write endpoints check
 * `canManageBusinessUnit` independently (see app/api/workspaces/[id]/members/**),
 * so a row in view is still not a row you may edit.
 */

/**
 * A role chip, tinted by tier so a governance binding is legible without
 * reading the label.
 *
 * `placeholder` is passed in rather than inferred from the role, because
 * `contributor` means two different things in the two columns it appears in.
 * As an org-level APPOINTMENT it is a real answer — this person works in a
 * unit — and printing "no role yet" there would report a decision that was
 * made as one that wasn't. As a binding INSIDE a unit it is the absence of a
 * role, and dressing it up as one hides the gap the queue exists to close.
 */
function RoleChip({
  label,
  role,
  placeholder = false,
}: {
  label: string;
  role: PlatformRole | null;
  placeholder?: boolean;
}) {
  const governance = role !== null && !placeholder && ROLE_META[role].tier === "governance";
  return (
    <span
      className={cn(
        "inline-flex w-fit items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10.5px]",
        placeholder
          ? "border-warning/35 bg-warning/10 text-warning border-dashed"
          : governance
            ? "border-brand-bright/35 bg-brand-bright/10 text-brand-bright"
            : "border-line-soft bg-surface-1 text-muted-foreground",
      )}
      title={placeholder ? "No role yet" : governance ? "Governance role" : "Delivery role"}
    >
      {governance && <ShieldCheck className="size-3" aria-hidden />}
      {label}
    </span>
  );
}

function asPlatformRole(name: string): PlatformRole | null {
  return name in ROLE_META ? (name as PlatformRole) : null;
}

export default function UsersPage() {
  const session = useSession({ required: true });
  const viewerRole = effectivePlatformRole(session);
  const searchParams = useSearchParams();
  const buFilter = searchParams.get("bu");
  // `?awaiting=1` — where the "someone needs a role" notification lands.
  const awaitingOnly = searchParams.get("awaiting") === "1";

  const [query, setQuery] = React.useState("");
  const [onboarding, setOnboarding] = React.useState(false);
  const [bulkOpen, setBulkOpen] = React.useState(false);
  const [borrowOpen, setBorrowOpen] = React.useState(false);
  const queryClient = useQueryClient();
  const [assigning, setAssigning] = React.useState<DirectoryEntry | null>(null);
  const [viewingHistory, setViewingHistory] = React.useState<DirectoryEntry | null>(null);
  const [reappointing, setReappointing] = React.useState<DirectoryEntry | null>(null);

  const canManage = hasPermission(session, "member:manage");
  const isOrgAdmin = viewerRole === "org_admin";
  const isBuAdmin = viewerRole === "bu_admin";

  const { managedBusinessUnitIds } = useAccessScope();
  const workspacesQ = useWorkspaces();
  const units = React.useMemo(
    () => (workspacesQ.data ?? []).filter((w) => w.status === "active"),
    [workspacesQ.data],
  );

  // ONE query. The old page fanned out over every unit plus every project —
  // roughly a dozen requests, each scope-filtered on its own, which is why it
  // could never show a Business Unit Admin the whole organisation.
  const directoryQ = useQuery({
    queryKey: qk.users.directory(),
    queryFn: listUserDirectory,
    staleTime: 60_000,
  });

  /**
   * Cross-unit loans touching a unit this viewer administers.
   *
   * Surfaced on Users rather than only on the project, because the two admins
   * involved read it from opposite ends: the borrowing side already sees the
   * guest on their project's roster, while the LENDING side has no other place
   * to learn that one of their people is working somewhere they cannot see.
   * Ownership stays with the parent unit, so ending the loan has to live where
   * that unit's admin already stands.
   */
  /** The projects a unit admin can borrow INTO — theirs, server-scoped. */
  const projectsQ = useQuery({
    queryKey: ["users-page", "projects"],
    queryFn: () => listProjects({ pageSize: 200 }),
    enabled: isBuAdmin,
    staleTime: 60_000,
  });

  const grantsQ = useQuery({
    queryKey: ["cross-bu-grants"],
    queryFn: listCrossBuGrants,
    enabled: isBuAdmin || isOrgAdmin,
    staleTime: 60_000,
  });
  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);
  const loansByIdentity = React.useMemo(() => {
    const m = new Map<string, typeof grants>();
    for (const g of grants) m.set(g.identityId, [...(m.get(g.identityId) ?? []), g]);
    return m;
  }, [grants]);

  const revokeLoan = useMutation({
    mutationFn: (g: { identityId: string; projectId: string }) => revokeCrossBuGrant(g),
    onSuccess: () => {
      toast.success("Loan ended");
      queryClient.invalidateQueries({ queryKey: ["cross-bu-grants"] });
      queryClient.invalidateQueries({ queryKey: qk.users.directory() });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const allCustomRoles = useAllCustomRoles();
  const roleLabel = React.useCallback(
    (name: string) => resolveRoleLabel(name, allCustomRoles),
    [allCustomRoles],
  );

  const rows = React.useMemo(() => directoryQ.data ?? [], [directoryQ.data]);

  /**
   * May the viewer change what this person holds?
   *
   * Gated on the viewer's ROLE, not on their managed units. An Organization
   * Admin's scope contains every unit, so a units-only test would hand them the
   * delivery-role picker for the whole organisation — the exact thing this
   * redesign takes away from them. They get the appointment dialog instead.
   */
  const canAssignRoleFor = React.useCallback(
    (entry: DirectoryEntry) =>
      isBuAdmin &&
      // A guest is another unit's person. Their role here was fixed by the
      // approval that lent them, and changing it is not this admin's to do —
      // it would rewrite a decision the lending unit made.
      !entry.isGuest &&
      // The Organization Admin holds a row in every unit, so they turn up in a
      // unit admin's list — but they are appointed from above, not managed from
      // inside. Their tier would make any delivery role a separation-of-duties
      // clash anyway; the button should not be offered in the first place.
      entry.orgRole !== "org_admin" &&
      entry.businessUnitId !== null &&
      managedBusinessUnitIds.includes(entry.businessUnitId),
    [isBuAdmin, managedBusinessUnitIds],
  );

  /** People in the viewer's own units who are still holding the placeholder —
   *  the queue the notification points at. */
  const awaiting = React.useMemo(
    () => rows.filter((r) => r.awaitingRole && canAssignRoleFor(r)),
    [rows, canAssignRoleFor],
  );

  const filterUnit = React.useMemo(
    () => (buFilter ? (units.find((u) => String(u.id) === buFilter) ?? null) : null),
    [buFilter, units],
  );

  /**
   * Two org-wide sentinel groups sit alongside real business units: the
   * Organization Admin belongs to none (authority isn't a membership row),
   * and someone can be onboarded before a unit is chosen for them. Neither
   * is a business unit, but both need somewhere to render — a null
   * businessUnitId is a real, distinct fact, not "ungrouped".
   */
  const ORG_WIDE_KEY = "__org_wide__";
  const UNPLACED_KEY = "__unplaced__";

  const groupKeyFor = React.useCallback(
    (r: DirectoryEntry) =>
      r.businessUnitId ?? (r.orgRole === "org_admin" ? ORG_WIDE_KEY : UNPLACED_KEY),
    [],
  );

  const filtered = React.useMemo(() => {
    let list = rows;
    if (buFilter) {
      list = list.filter(
        (r) =>
          r.businessUnitId === buFilter || r.bindings.some((b) => b.businessUnitId === buFilter),
      );
    }
    if (awaitingOnly) list = list.filter((r) => r.awaitingRole);

    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (r) =>
        r.displayName.toLowerCase().includes(q) ||
        (r.email ?? "").toLowerCase().includes(q) ||
        (r.businessUnitName ?? "").toLowerCase().includes(q) ||
        roleLabel(r.unitRole ?? r.orgRole)
          .toLowerCase()
          .includes(q) ||
        r.bindings.some(
          (b) => b.name.toLowerCase().includes(q) || roleLabel(b.role).toLowerCase().includes(q),
        ),
    );
  }, [rows, buFilter, awaitingOnly, query, roleLabel]);

  /**
   * Sorted by business unit rather than one flat, mixed roster — same rows,
   * same per-row capabilities (role controls, loans, view-only), just
   * organised the way an admin actually thinks about who's where. A
   * Business Unit Admin's OWN unit(s) sort first and carry a "Your business
   * unit" badge — the people they can act on, ahead of the ones they can
   * only borrow from — everything else (including the two org-wide
   * sentinel groups) sorts alphabetically after.
   */
  const groups = React.useMemo(() => {
    const byKey = new Map<
      string,
      { key: string; name: string; isMine: boolean; entries: DirectoryEntry[] }
    >();
    for (const r of filtered) {
      const key = groupKeyFor(r);
      const name =
        key === ORG_WIDE_KEY
          ? "Every business unit"
          : key === UNPLACED_KEY
            ? `No ${BUSINESS_UNIT_LABEL.toLowerCase()} yet`
            : (r.businessUnitName ?? key);
      const existing = byKey.get(key);
      if (existing) {
        existing.entries.push(r);
      } else {
        byKey.set(key, {
          key,
          name,
          isMine: managedBusinessUnitIds.includes(key),
          entries: [r],
        });
      }
    }
    return [...byKey.values()].sort((a, b) => {
      // Mine first (Business Unit Admin only — an Org Admin manages none of
      // these, so this comparison is a no-op for them and falls through to
      // the alphabetical/sentinel ordering below).
      if (a.isMine !== b.isMine) return a.isMine ? -1 : 1;
      // The two sentinel groups always sort last, in a fixed order — they
      // are not business units, so alphabetising them among real ones would
      // place "Every business unit" or "No business unit yet" arbitrarily
      // between two actual unit names depending on what those happen to be
      // called.
      const rank = (k: string) => (k === ORG_WIDE_KEY ? 1 : k === UNPLACED_KEY ? 2 : 0);
      const ra = rank(a.key);
      const rb = rank(b.key);
      if (ra !== rb) return ra - rb;
      return a.name.localeCompare(b.name);
    });
  }, [filtered, groupKeyFor, managedBusinessUnitIds]);

  // Users is an administrator surface: the Organization Admin runs it, a
  // Business Unit Admin works their own unit's half of it. Builders never see
  // it — it names every colleague's email and role.
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
            <PageTitle>Users &amp; Roles</PageTitle>
            {/* No explanatory paragraph — see components/app/page-title.tsx.
                The one thing a Business Unit Admin cannot infer from the table
                is why most of it is read-only, and that is said on the rows it
                applies to. The chip is here for them too: an org-wide list is
                the surprising part of their copy of this page, and unlabelled
                it reads as a scope leak. */}
            {/* The chip has to match what is actually in front of them, and the
                two non-org-admin readers are looking at different things: a unit
                admin reads the organisation but writes in one unit, a project
                admin reads one unit. Naming an org-wide scope to the second
                would be worse than saying nothing. */}
            {!isOrgAdmin && (
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <ScopeChip
                  kind={isBuAdmin ? "organization" : "business_unit"}
                  name={null}
                  size="sm"
                />
                <span className="text-muted-foreground text-[11.5px]">
                  {isBuAdmin
                    ? `Everyone in the organization. You assign roles in your ${
                        managedBusinessUnitIds.length === 1
                          ? BUSINESS_UNIT_LABEL.toLowerCase()
                          : BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()
                      } — every other row is read-only.`
                    : `Everyone in the ${BUSINESS_UNIT_LABEL.toLowerCase()} you work in. Roles here are its admin's to set — your project's own roster is on the project.`}
                </span>
              </div>
            )}
          </div>

          {/* The Business Unit Admin's own way in. They staff their unit from
              here, and the person they need is sometimes in another unit —
              sending them into a project's Members screen to ask for one would
              mean leaving the page where they do the rest of this job. */}
          {isBuAdmin && (
            <Button
              variant="outline"
              className="border-line-soft gap-2"
              onClick={() => setBorrowOpen(true)}
            >
              <Building2 className="size-4" aria-hidden />
              Borrow from another {BUSINESS_UNIT_LABEL.toLowerCase()}
            </Button>
          )}

          {isOrgAdmin && (
            <div className="flex flex-wrap items-center gap-2">
              {/* Secondary, and deliberately so: a roster arrives a few times a
                  year and one person arrives most weeks. */}
              <Button
                variant="outline"
                className="border-line-soft gap-2"
                onClick={() => setBulkOpen(true)}
              >
                <Upload className="size-4" aria-hidden />
                Bulk upload
              </Button>
              <Button className="gap-2" onClick={() => setOnboarding(true)}>
                <UserPlus className="size-4" aria-hidden />
                Onboard someone
              </Button>
            </div>
          )}
        </div>
      </header>

      {/* The handover, made into work rather than a notification that scrolls
          away. This is the whole reason a Business Unit Admin opens this page. */}
      {awaiting.length > 0 && (
        <section className="border-warning/30 bg-warning/5 space-y-3 rounded-xl border p-4">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-[13px] font-semibold">
              {awaiting.length} {awaiting.length === 1 ? "person needs" : "people need"} a role from
              you
            </h2>
            <span className="text-muted-foreground font-mono text-[10.5px] tracking-wider uppercase">
              Awaiting assignment
            </span>
          </div>
          <p className="text-muted-foreground text-[12px]">
            Onboarded into your{" "}
            {managedBusinessUnitIds.length === 1
              ? BUSINESS_UNIT_LABEL.toLowerCase()
              : BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}{" "}
            and holding no permissions until you say what they do.
          </p>
          <ul className="flex flex-col gap-2">
            {awaiting.map((p) => (
              <li
                key={p.userId}
                className="border-line-soft bg-surface-1 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2"
              >
                <span
                  className="bg-surface-2 text-foreground/80 flex size-7 shrink-0 items-center justify-center rounded-full font-mono text-[10.5px]"
                  aria-hidden
                >
                  {p.initials}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] font-medium">{p.displayName}</span>
                  <span className="text-muted-foreground block truncate text-[11px]">
                    {p.email} · {p.businessUnitName}
                  </span>
                </span>
                <Button
                  size="sm"
                  className="h-7 px-2.5 text-[11px]"
                  onClick={() => setAssigning(p)}
                >
                  Assign role
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-sm">
          <Search
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search people, units or roles…"
            className="border-line-soft bg-surface-1 pl-9"
            aria-label="Search the people directory"
          />
        </div>

        {(buFilter || awaitingOnly) && (
          <span className="border-brand-bright/35 bg-brand-bright/10 text-brand-bright inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px]">
            {awaitingOnly
              ? "Awaiting a role"
              : `${BUSINESS_UNIT_LABEL}: ${filterUnit?.displayName ?? buFilter}`}
            <Link
              href="/users"
              aria-label="Clear the filter"
              className="hover:text-foreground transition-colors"
            >
              <X className="size-3" aria-hidden />
            </Link>
          </span>
        )}
      </div>

      {directoryQ.isLoading ? (
        <LoadingState variant="list" rows={5} />
      ) : directoryQ.isError ? (
        <ApiErrorState
          title="Couldn't load the people directory"
          onRetry={() => void directoryQ.refetch()}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={UsersIcon}
          title={query ? "No one matches that search" : "No people yet"}
          description={
            query
              ? "Try a different name, email, business unit or role."
              : isOrgAdmin
                ? "Onboard someone to get started."
                : "Nobody has been onboarded into the organisation yet."
          }
        />
      ) : (
        <div className="flex flex-col gap-5">
          {groups.map((group) => (
            <div key={group.key} className="flex flex-col gap-2">
              {/* One header per business unit — the count and (for a Business
                  Unit Admin) the "yours" badge are the two facts that used to
                  require reading every row in a mixed list to work out. */}
              <div className="flex flex-wrap items-center gap-2 px-1">
                <h2 className="text-[13px] font-semibold">{group.name}</h2>
                <span className="text-muted-foreground font-mono text-[11px]">
                  {group.entries.length} {group.entries.length === 1 ? "person" : "people"}
                </span>
                {group.isMine && (
                  <span className="border-brand-bright/35 bg-brand-bright/10 text-brand-bright inline-flex items-center gap-1 rounded-full border px-1.5 py-px font-mono text-[9.5px] tracking-wider uppercase">
                    Your {BUSINESS_UNIT_LABEL.toLowerCase()}
                  </span>
                )}
              </div>

              <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border">
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow className="border-line-soft hover:bg-transparent">
                        <TableHead className="text-muted-foreground font-mono text-[10.5px] tracking-widest uppercase">
                          Person
                        </TableHead>
                        {/* WHAT they are, then WHERE they work. The first column
                            used to print the org-level appointment and so stayed on
                            "Contributor" after a unit admin had made them a
                            Developer; the second listed the unit binding next to the
                            project ones, which is where that change actually showed
                            up. One column per question fixes both. */}
                        <TableHead className="text-muted-foreground font-mono text-[10.5px] tracking-widest uppercase">
                          Role
                        </TableHead>
                        <TableHead className="text-muted-foreground font-mono text-[10.5px] tracking-widest uppercase">
                          Project roles
                        </TableHead>
                        <TableHead className="text-muted-foreground w-32 text-right font-mono text-[10.5px] tracking-widest uppercase">
                          <span className="sr-only">Actions</span>
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {group.entries.map((r) => {
                        const assignable = canAssignRoleFor(r);
                        // Only the project work. Their role in the unit is the column
                        // to the left; repeating it here made the two columns
                        // disagree about which one answered "what is this person".
                        const projectBindings = r.bindings.filter((b) => b.scope === "project");
                        // What they are: the unit role once assigned, the appointment
                        // until then. `contributor` is the placeholder either way.
                        const roleName = r.unitRole ?? r.orgRole;
                        const isPlaceholder = awaitsBusinessUnitRole(roleName);

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

                            <TableCell className="py-3 align-top">
                              <div className="flex flex-col items-start gap-1">
                                <RoleChip
                                  label={isPlaceholder ? "No role yet" : roleLabel(roleName)}
                                  role={asPlatformRole(roleName)}
                                  placeholder={isPlaceholder}
                                />
                                <span className="text-muted-foreground flex flex-wrap items-center gap-1.5 text-[11.5px]">
                                  {r.businessUnitName ?? (
                                    <em className="not-italic opacity-70">
                                      {r.orgRole === "org_admin"
                                        ? "Every " + BUSINESS_UNIT_LABEL.toLowerCase()
                                        : `No ${BUSINESS_UNIT_LABEL.toLowerCase()} yet`}
                                    </em>
                                  )}
                                  {/* Working here, belonging elsewhere. The unit name
                                above is theirs, not yours — without this chip
                                the row reads as a member of another unit having
                                leaked into your list. */}
                                  {r.isGuest && (
                                    <span className="border-brand-bright/35 bg-brand-bright/10 text-brand-bright inline-flex items-center gap-1 rounded-full border px-1.5 py-px font-mono text-[9.5px] tracking-wider uppercase">
                                      <Building2 className="size-2.5" aria-hidden />
                                      guest
                                    </span>
                                  )}
                                </span>
                              </div>
                            </TableCell>

                            <TableCell className="py-3 align-top">
                              {/* Loans first: a person working in another unit is the
                            fact this admin cannot learn anywhere else. */}
                              {(loansByIdentity.get(r.identityId) ?? []).map((g) => (
                                <span
                                  key={g.id}
                                  className="mb-1 flex flex-wrap items-center gap-1.5 text-[11.5px]"
                                >
                                  <Building2 className="text-brand-bright size-3" aria-hidden />
                                  <span className="text-muted-foreground">
                                    {g.projectName} · {g.targetWorkspaceName}
                                  </span>
                                  <RoleChip
                                    label={roleLabel(g.role)}
                                    role={asPlatformRole(g.role)}
                                  />
                                  {g.lentByYou && (
                                    <button
                                      type="button"
                                      disabled={revokeLoan.isPending}
                                      onClick={() =>
                                        revokeLoan.mutate({
                                          identityId: g.identityId,
                                          projectId: g.projectId,
                                        })
                                      }
                                      className="text-muted-foreground hover:text-destructive underline underline-offset-2 disabled:opacity-50"
                                    >
                                      End loan
                                    </button>
                                  )}
                                </span>
                              ))}
                              {projectBindings.length === 0 &&
                              (loansByIdentity.get(r.identityId) ?? []).length >
                                0 ? null : projectBindings.length === 0 ? (
                                <span className="text-muted-foreground text-[11.5px]">
                                  {/* Not an empty cell. The governance tier is never
                                on a project, so "none" is the rule holding
                                rather than data missing. */}
                                  {r.orgRole === "org_admin" || roleName === "bu_admin"
                                    ? "Governs, doesn't deliver"
                                    : "—"}
                                </span>
                              ) : (
                                <div className="flex flex-col gap-1">
                                  {projectBindings.map((b) => (
                                    <span
                                      key={`${b.id}:${b.role}`}
                                      className="flex flex-wrap items-center gap-1.5"
                                    >
                                      <span className="text-muted-foreground text-[11.5px]">
                                        {b.name}
                                      </span>
                                      <RoleChip
                                        label={roleLabel(b.role)}
                                        role={asPlatformRole(b.role)}
                                      />
                                    </span>
                                  ))}
                                </div>
                              )}
                            </TableCell>

                            <TableCell className="py-3 text-right align-top">
                              {/* WHO CHANGED THIS PERSON'S ROLE, AND WHEN. Beside the
                            control that changes it, because the question is asked
                            in the same breath as the change is considered. Shown to
                            whoever may manage roles here — the server refuses a
                            history for anybody outside the caller's units, so this
                            is an affordance rather than the boundary. */}
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => setViewingHistory(r)}
                                aria-label={`Role history for ${r.displayName}`}
                                title="Role history"
                                className="mr-1 h-7 w-7 align-middle"
                              >
                                <Eye className="size-3.5" aria-hidden />
                              </Button>
                              {isOrgAdmin && r.orgRole !== "org_admin" ? (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => setReappointing(r)}
                                  aria-label={`Manage the role for ${r.displayName}`}
                                  className="border-line-soft h-7 px-2 text-[11px]"
                                >
                                  {/* Same words as the Business Unit Admin's button
                                on the same column. "Appointment" named the
                                internal concept rather than the action, and it
                                was the only button on this page that did. */}
                                  Manage role
                                </Button>
                              ) : assignable ? (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => setAssigning(r)}
                                  aria-label={`Assign a role to ${r.displayName}`}
                                  className="border-line-soft h-7 px-2 text-[11px]"
                                >
                                  {r.awaitingRole ? "Assign role" : "Change role"}
                                </Button>
                              ) : (
                                /* Not a disabled button. A greyed-out control reads as
                             "try again later"; this is never going to work from
                             here, and saying which unit owns it says where it
                             would. */
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Badge
                                      variant="outline"
                                      className="text-muted-foreground gap-1 font-mono text-[9.5px]"
                                    >
                                      <Lock className="size-2.5" aria-hidden />
                                      view only
                                    </Badge>
                                  </TooltipTrigger>
                                  <TooltipContent side="left" className="max-w-[240px]">
                                    {r.isGuest
                                      ? `On loan from ${r.businessUnitName}, who still own them. Their role here was set by the approval that lent them.`
                                      : r.orgRole === "org_admin"
                                        ? "The Organization Admin is appointed org-wide, not from inside a business unit."
                                        : r.businessUnitName
                                          ? `${r.businessUnitName}'s admin assigns roles for this person.`
                                          : `They belong to no ${BUSINESS_UNIT_LABEL.toLowerCase()} yet — the Organization Admin places them.`}
                                  </TooltipContent>
                                </Tooltip>
                              )}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {isOrgAdmin && (
        <>
          <OnboardUserDialog
            open={onboarding}
            onOpenChange={setOnboarding}
            businessUnits={units.map((u) => ({ id: u.id, displayName: u.displayName }))}
            onOnboarded={() => void directoryQ.refetch()}
          />
          <BulkOnboardDialog
            open={bulkOpen}
            onOpenChange={setBulkOpen}
            businessUnits={units.map((u) => ({ id: u.id, displayName: u.displayName }))}
            onFinished={() => void directoryQ.refetch()}
          />
        </>
      )}

      {reappointing && (
        <ChangeAppointmentDialog
          open
          onOpenChange={(o) => !o && setReappointing(null)}
          userId={reappointing.userId}
          displayName={reappointing.displayName}
          currentRole={reappointing.orgRole}
          currentBusinessUnitId={reappointing.businessUnitId}
          businessUnits={units.map((u) => ({ id: u.id, displayName: u.displayName }))}
        />
      )}

      {isBuAdmin && (
        <RequestCrossBuMemberDialog
          open={borrowOpen}
          onOpenChange={setBorrowOpen}
          projects={(projectsQ.data?.items ?? []).map((p) => ({ id: String(p.id), name: p.name }))}
          onRaised={() => queryClient.invalidateQueries({ queryKey: ["governance-approvals"] })}
        />
      )}

      {viewingHistory && (
        <RoleHistoryDialog
          userId={viewingHistory.userId}
          displayName={viewingHistory.displayName}
          open
          onOpenChange={(o) => !o && setViewingHistory(null)}
        />
      )}

      {assigning && assigning.businessUnitId && (
        <AssignBusinessUnitRoleDialog
          open
          onOpenChange={(o) => !o && setAssigning(null)}
          userId={assigning.userId}
          displayName={assigning.displayName}
          businessUnitId={assigning.businessUnitId}
          businessUnitName={assigning.businessUnitName ?? assigning.businessUnitId}
          currentRole={
            assigning.bindings.find((b) => b.businessUnitId === assigning.businessUnitId)?.role ??
            null
          }
          allBindings={assigning.bindings.map((b) => ({
            scopeId: b.businessUnitId ?? b.id,
            scopeName: b.name,
            role: b.role,
          }))}
        />
      )}
    </div>
  );
}
