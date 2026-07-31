"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  Coins,
  FolderKanban,
  Loader2,
  Pencil,
  Search,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LoadingState } from "@/components/ui/loading-state";
import { Switch } from "@/components/ui/switch";
import { ActiveBadge } from "@/components/app/active-badge";
import { ChangeBuAdminDialog } from "@/components/app/change-bu-admin-dialog";
import { EditBuDetailsDialog } from "@/components/app/edit-bu-details-dialog";
import {
  BudgetWindowFieldsInput,
  BudgetWindowSummary,
} from "@/components/app/budget-window-fields";
import { budgetWindowError, type BudgetWindow } from "@/lib/schemas/budget-window";
import { OutOfScope } from "@/components/auth/scope-empty-state";
import { useRawSession } from "@/components/auth/session-provider";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { listOrgMembers } from "@/lib/api/access";
import {
  listWorkspaceMembers,
  addWorkspaceMember,
  removeWorkspaceMember,
  updateWorkspaceMemberRole,
  getWorkspace,
  updateWorkspace,
  requestBudgetIncrease,
} from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { ROLE_META, ROLE_ORDER, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { resolveRoleLabel, useAllCustomRoles, useCustomRolesForScope } from "@/hooks/use-assignable-roles";

interface RoleRow {
  name: string;
  label: string;
  desc: string;
}

// ─── Role catalog ─────────────────────────────────────────────────────────────
// All twelve built-in roles (PRD §33.1 plus Scrum Master) — for label
// lookups. An existing member's role must render as a proper label no matter
// who's viewing, even a role the current viewer isn't allowed to grant.
const ALL_BUILTIN_ROLES: RoleRow[] = ROLE_ORDER.map((r) => ({
  name: r,
  label: ROLE_META[r].label,
  desc: ROLE_META[r].oneLiner,
}));
const BUILTIN_ROLES_BY_NAME = new Map(ALL_BUILTIN_ROLES.map((r) => [r.name, r]));

/**
 * Assignable built-in roles — the delivery-tier builders a Business Unit
 * owner always grants, plus `bu_admin` itself but ONLY for an Org Admin.
 * Appointing/replacing a business unit's admin is the Org Admin's call (PRD
 * §15.2 — "Org Admin creates business units and appoints their admins"), not
 * something the sitting BU Admin can do to themselves or a peer. Merged with
 * any business_unit-scoped custom role by the caller (see `roles` below).
 */
function assignableBuiltinRoles(canAppointBuAdmin: boolean): RoleRow[] {
  const delivery = ALL_BUILTIN_ROLES.filter((r) => ROLE_META[r.name as PlatformRole].tier === "delivery");
  return canAppointBuAdmin ? [BUILTIN_ROLES_BY_NAME.get("bu_admin")!, ...delivery] : delivery;
}

type RoleName = string;

// ─── Avatar colors (deterministic by userId) ──────────────────────────────────
const AVATAR_PALETTE = [
  "bg-[oklch(0.52_0.19_30)]",
  "bg-[oklch(0.48_0.15_240)]",
  "bg-[oklch(0.46_0.14_160)]",
  "bg-[oklch(0.48_0.13_290)]",
  "bg-[oklch(0.44_0.12_100)]",
  "bg-[oklch(0.50_0.17_55)]",
];
function avatarBg(uid: string) {
  let n = 0;
  for (let i = 0; i < uid.length; i++) n += uid.charCodeAt(i);
  return AVATAR_PALETTE[n % AVATAR_PALETTE.length]!;
}

type FilterTab = "all" | "members" | "not-added";

// ─── Combined member view ──────────────────────────────────────────────────────
interface RowData {
  userId: string;
  email: string | null;
  initials: string;
  isMember: boolean;
  roleName: string | null;
}

const RISE = {
  animationName: "rise",
  animationDuration: "0.55s",
  animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  animationFillMode: "both",
} as const;

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function WorkspaceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const session = useRawSession();
  const queryClient = useQueryClient();
  const canManage = hasPermission(session, "workspace:manage");
  const role = effectivePlatformRole(session);
  const customBuRoles = useCustomRolesForScope("business_unit");
  const allCustomRoles = useAllCustomRoles();
  const roleLabel = React.useCallback(
    (name: string) => resolveRoleLabel(name, allCustomRoles),
    [allCustomRoles],
  );
  const roles = React.useMemo<RoleRow[]>(
    () => [
      ...assignableBuiltinRoles(role === "org_admin"),
      ...customBuRoles.map((c) => ({ name: c.value, label: c.label, desc: c.description ?? "" })),
    ],
    [role, customBuRoles],
  );
  const wsQ = useQuery({
    queryKey: qk.workspaces.detail(id),
    queryFn: () => getWorkspace(id),
    staleTime: 60_000,
  });

  // The budget cascade (PRD §34.5): the Org Admin may leave a unit's cap unset
  // at creation, and the unit's own Admin then sets the first one — nobody
  // needs approval to fill in a blank, because there is no prior figure to
  // negotiate against. Once a cap exists, raising it is a governance decision
  // and the BU Admin goes back to requesting it (lib/governance.ts).
  const budgetSet = (wsQ.data?.monthlyBudgetUsd ?? null) !== null;
  const canEditBudgetDirectly = role === "org_admin" || (role === "bu_admin" && !budgetSet);
  const canRequestBudgetIncrease = role === "bu_admin" && budgetSet;
  // Active/inactive is the Org Admin's label about a unit, not one the unit
  // administers for itself — the API rejects it from anyone else.
  const canSetActive = role === "org_admin";
  const membersQ = useQuery({
    queryKey: qk.workspaces.members(id),
    queryFn: () => listWorkspaceMembers(id),
    staleTime: 30_000,
  });
  const orgQ = useQuery({
    queryKey: ["access", "org-members"],
    queryFn: listOrgMembers,
    staleTime: 120_000,
  });

  const invalidateMembers = () => {
    queryClient.invalidateQueries({ queryKey: qk.workspaces.members(id) });
    queryClient.invalidateQueries({ queryKey: qk.workspaces.all() });
  };

  const addMutation = useMutation({
    mutationFn: ({
      userId,
      roleName,
      email,
      initials,
    }: {
      userId: string;
      roleName: string;
      email?: string | null;
      initials?: string;
    }) => addWorkspaceMember(id, { userId, roleName, email, initials }),
    onSuccess: (m) => {
      toast.success(`Added ${m.email ?? m.userId} as ${roleLabel(m.roleName)}`);
      invalidateMembers();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't add member"),
  });

  const removeMutation = useMutation({
    mutationFn: (userId: string) => removeWorkspaceMember(id, userId),
    onSuccess: (_d, userId) => {
      const row = rows.find((r) => r.userId === userId);
      toast.success(`Removed ${row?.email ?? userId}`);
      invalidateMembers();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't remove member"),
  });

  const roleMutation = useMutation({
    mutationFn: ({ userId, roleName }: { userId: string; roleName: string }) =>
      updateWorkspaceMemberRole(id, userId, { roleName }),
    onSuccess: (_d, { roleName }) => {
      toast.success(`Role updated to ${roleLabel(roleName)}`);
      invalidateMembers();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't update role"),
  });

  const budgetMutation = useMutation({
    mutationFn: (patch: {
      monthlyBudgetUsd: number | null;
      budgetStartDate: string | null;
      budgetEndDate: string | null;
    }) => updateWorkspace(id, patch),
    onSuccess: () => {
      toast.success("Budget updated");
      queryClient.invalidateQueries({ queryKey: qk.workspaces.detail(id) });
      queryClient.invalidateQueries({ queryKey: qk.workspaces.all() });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't update budget"),
  });

  const activeMutation = useMutation({
    mutationFn: (isActive: boolean) => updateWorkspace(id, { isActive }),
    onSuccess: (w) => {
      toast.success(
        `${w.displayName} marked ${w.isActive ? "active" : "inactive"}`,
        { description: "This is a label only — nothing is restricted by it." },
      );
      queryClient.invalidateQueries({ queryKey: qk.workspaces.detail(id) });
      queryClient.invalidateQueries({ queryKey: qk.workspaces.all() });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't update active status"),
  });

  const budgetIncreaseMutation = useMutation({
    mutationFn: (body: { requestedAmountUsd: number; reason?: string }) =>
      requestBudgetIncrease(id, body),
    onSuccess: () => {
      toast.info("Sent for approval", {
        description: "Your Org Admin needs to approve this before the budget changes.",
      });
    },
    onError: (e) =>
      toast.error("Couldn't send request", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  const [editOpen, setEditOpen] = React.useState(false);
  const [filter, setFilter] = React.useState<FilterTab>("all");
  const [search, setSearch] = React.useState("");

  const memberMap = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const mem of membersQ.data ?? []) m.set(mem.userId, mem.roleName);
    return m;
  }, [membersQ.data]);

  const rows: RowData[] = React.useMemo(() => {
    const orgUsers = orgQ.data ?? [];
    // Start with org users; enrich with membership data
    const byId = new Map<string, RowData>();
    for (const u of orgUsers) {
      byId.set(u.userId, {
        userId: u.userId,
        email: u.email,
        initials: u.initials,
        isMember: memberMap.has(u.userId),
        roleName: memberMap.get(u.userId) ?? null,
      });
    }
    // Ensure workspace members who aren't in orgUsers (e.g. OIDC-only) also appear
    for (const mem of membersQ.data ?? []) {
      if (!byId.has(mem.userId)) {
        byId.set(mem.userId, {
          userId: mem.userId,
          email: mem.email ?? null,
          initials: mem.initials,
          isMember: true,
          roleName: mem.roleName,
        });
      }
    }
    return Array.from(byId.values()).sort((a, b) => {
      // Members first, then alphabetical by email
      if (a.isMember !== b.isMember) return a.isMember ? -1 : 1;
      return (a.email ?? a.userId).localeCompare(b.email ?? b.userId);
    });
  }, [orgQ.data, membersQ.data, memberMap]);

  const filtered = React.useMemo(() => {
    let out = rows;
    if (filter === "members") out = out.filter((r) => r.isMember);
    if (filter === "not-added") out = out.filter((r) => !r.isMember);
    if (search.trim()) {
      const q = search.toLowerCase();
      out = out.filter(
        (r) => r.email?.toLowerCase().includes(q) || r.userId.toLowerCase().includes(q),
      );
    }
    return out;
  }, [rows, filter, search]);

  const memberCount = rows.filter((r) => r.isMember).length;
  const notAddedCount = rows.filter((r) => !r.isMember).length;

  const ws = wsQ.data;
  const isLoading = wsQ.isLoading || membersQ.isLoading || orgQ.isLoading;
  const mutationBusy = addMutation.isPending || removeMutation.isPending || roleMutation.isPending;

  // A sibling unit answers 404 (see app/api/workspaces/[id]/route.ts), so this
  // path is now reachable by URL-guessing as well as by a genuinely missing id.
  // The out-of-scope state explains the boundary instead of implying the unit
  // was archived, which would be a misleading guess in the common case.
  if (wsQ.isError) {
    return <OutOfScope kind="business unit" />;
  }

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8" style={RISE}>
      {/* Back navigation */}
      <button
        onClick={() => router.push("/workspaces")}
        className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 font-mono text-[11px] tracking-[0.12em] uppercase transition-colors"
      >
        <ArrowLeft className="size-3.5" aria-hidden />
        {BUSINESS_UNIT_LABEL_PLURAL}
      </button>

      {/* Workspace header */}
      {ws ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="font-display text-[32px] font-bold leading-none tracking-[-0.03em]">
                {ws.displayName}
              </h1>
              <ActiveBadge isActive={ws.isActive} />
            </div>
            <p className="text-muted-foreground font-mono text-[11px] tracking-wider uppercase">
              {[ws.businessUnit, ws.slug, ws.costCenter].filter(Boolean).join(" · ")}
            </p>
            <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11.5px]">
              <span className="flex items-center gap-1.5">
                <Users className="size-3.5" aria-hidden />
                {ws.memberCount} members
              </span>
              <span className="flex items-center gap-1.5">
                <FolderKanban className="size-3.5" aria-hidden />
                {ws.projectCount} projects
              </span>
              <span className="flex items-center gap-1.5">
                <Coins className="size-3.5" aria-hidden />
                {ws.monthlySpendUsd.toLocaleString(undefined, {
                  style: "currency",
                  currency: "USD",
                  maximumFractionDigits: 0,
                })}{" "}
                /mo
              </span>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {canManage && (
              <Button
                size="sm"
                variant="outline"
                className="border-line-soft h-9 font-mono text-[11px]"
                onClick={() => setEditOpen(true)}
              >
                <Pencil className="size-3.5" aria-hidden />
                Edit details
              </Button>
            )}
            {canSetActive && (
              <label className="border-line-soft bg-surface-1 flex shrink-0 items-center gap-2.5 rounded-lg border px-3 py-2">
                <span className="text-muted-foreground font-mono text-[10.5px] tracking-[0.12em] uppercase">
                  Active
                </span>
                <Switch
                  checked={ws.isActive}
                  disabled={activeMutation.isPending}
                  onCheckedChange={(v) => activeMutation.mutate(v)}
                  aria-label={`${ws.displayName} is active`}
                />
              </label>
            )}
          </div>

          {canManage && (
            <EditBuDetailsDialog workspace={ws} open={editOpen} onOpenChange={setEditOpen} />
          )}
        </div>
      ) : (
        <div className="h-20 animate-pulse rounded-xl bg-muted/30" />
      )}

      {/* Who runs this unit — PRD §15.2 */}
      {ws && (
        <BuAdminCard
          workspaceId={id}
          workspaceName={ws.displayName}
          adminName={ws.buAdminName ?? null}
          canChange={canSetActive}
        />
      )}

      {/* Budget section */}
      {ws && (
        <BudgetCard
          spend={ws.monthlySpendUsd}
          budget={ws.monthlyBudgetUsd ?? null}
          window={{
            budgetStartDate: ws.budgetStartDate ?? null,
            budgetEndDate: ws.budgetEndDate ?? null,
          }}
          canEditDirectly={canEditBudgetDirectly}
          canRequestIncrease={canRequestBudgetIncrease}
          saving={budgetMutation.isPending}
          onSave={(patch) => budgetMutation.mutate(patch)}
          requesting={budgetIncreaseMutation.isPending}
          onRequestIncrease={(v, reason) =>
            budgetIncreaseMutation.mutate({ requestedAmountUsd: v, reason })
          }
        />
      )}

      {/* Members section */}
      <section
        className="border-line-soft bg-panel-elevated overflow-hidden rounded-2xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_24px_-8px_oklch(0_0_0_/_0.28)]"
        style={{ ...RISE, animationDelay: "0.06s" }}
      >
        {/* Section header */}
        <div className="border-line-soft flex flex-col gap-3 border-b px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span className="font-display text-[15px] font-bold tracking-[-0.01em]">Members</span>

            {/* Filter tabs */}
            <div className="border-line-soft bg-surface-1 flex rounded-lg border p-0.5">
              {(
                [
                  { key: "all", label: "All", count: rows.length },
                  { key: "members", label: `In ${BUSINESS_UNIT_LABEL.toLowerCase()}`, count: memberCount },
                  { key: "not-added", label: "Not added", count: notAddedCount },
                ] as const
              ).map(({ key, label, count }) => (
                <button
                  key={key}
                  onClick={() => setFilter(key)}
                  className={cn(
                    "rounded-md px-2.5 py-1 font-mono text-[11px] font-semibold transition-all",
                    filter === key
                      ? "bg-panel-elevated text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {label}
                  <span
                    className={cn(
                      "ml-1.5 rounded-full px-1.5 py-0.5 text-[9px]",
                      filter === key ? "bg-muted text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {count}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Search */}
          <div className="relative w-full sm:w-56">
            <Search className="text-muted-foreground absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2" aria-hidden />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search members…"
              className="border-line-soft h-8 pl-8 font-mono text-[12px]"
            />
          </div>
        </div>

        {/* Table header */}
        <div className="border-line-soft border-b px-6 py-2">
          <div className="grid grid-cols-[1fr_160px_120px] items-center gap-4">
            <span className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
              Person
            </span>
            <span className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
              {BUSINESS_UNIT_LABEL} role
            </span>
            <span className="sr-only">Actions</span>
          </div>
        </div>

        {/* Rows */}
        {isLoading ? (
          <div className="px-6 py-8">
            <LoadingState variant="table" rows={5} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
            <Users className="text-muted-foreground size-8" aria-hidden />
            <p className="font-display text-sm font-semibold">
              {search ? "No members match that search" : "No results"}
            </p>
            {search && (
              <button
                onClick={() => setSearch("")}
                className="text-muted-foreground hover:text-foreground font-mono text-[12px] underline underline-offset-2"
              >
                Clear search
              </button>
            )}
          </div>
        ) : (
          <ul>
            {filtered.map((row, i) => (
              <MemberRow
                key={row.userId}
                row={row}
                roles={roles}
                canManage={canManage}
                busy={mutationBusy}
                index={i}
                onAdd={(roleName) =>
                  addMutation.mutate({
                    userId: row.userId,
                    roleName,
                    email: row.email,
                    initials: row.initials,
                  })
                }
                onRemove={() => removeMutation.mutate(row.userId)}
                onChangeRole={(roleName) => roleMutation.mutate({ userId: row.userId, roleName })}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// ─── Business Unit admin card ─────────────────────────────────────────────────
/**
 * Who runs this unit, and the Org Admin's control to change it.
 *
 * The Members list below can already move an existing member into `bu_admin`,
 * but that path answers "what role does this person have" — it doesn't surface
 * the fact that the unit has exactly one admin, and it can't appoint someone
 * who isn't in the unit yet. This card answers the question the Org Admin
 * actually arrives with: who is accountable for this unit, and how do I hand it
 * to someone else.
 */
function BuAdminCard({
  workspaceId,
  workspaceName,
  adminName,
  canChange,
}: {
  workspaceId: string;
  workspaceName: string;
  adminName: string | null;
  canChange: boolean;
}) {
  const [open, setOpen] = React.useState(false);

  return (
    <section
      className="border-line-soft bg-panel-elevated rounded-2xl border px-6 py-5 shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_24px_-8px_oklch(0_0_0_/_0.28)]"
      style={{ ...RISE, animationDelay: "0.03s" }}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="text-brand-bright size-4" aria-hidden />
          <span className="font-display text-[15px] font-bold tracking-[-0.01em]">
            {ROLE_META.bu_admin.label}
          </span>
        </div>
        {canChange && (
          <Button
            size="sm"
            variant="outline"
            className="border-line-soft h-7 font-mono text-[11px]"
            onClick={() => setOpen(true)}
          >
            {adminName ? "Change admin" : "Appoint admin"}
          </Button>
        )}
      </div>

      <p className="text-muted-foreground mt-3 font-mono text-[12px]">
        {adminName ? (
          <span className="text-foreground font-semibold">{adminName}</span>
        ) : (
          "No admin appointed yet."
        )}
      </p>

      {canChange && (
        <ChangeBuAdminDialog
          workspaceId={workspaceId}
          workspaceName={workspaceName}
          currentAdminName={adminName}
          open={open}
          onOpenChange={setOpen}
        />
      )}
    </section>
  );
}


// ─── Budget card ──────────────────────────────────────────────────────────────
function fmtUsd(n: number, max = 2): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: max });
}

function BudgetCard({
  spend,
  budget,
  window,
  canEditDirectly,
  canRequestIncrease,
  saving,
  onSave,
  requesting,
  onRequestIncrease,
}: {
  spend: number;
  budget: number | null;
  /** Period the cap is valid for — see lib/schemas/budget-window.ts. */
  window: BudgetWindow;
  /** Org Admin, or the unit's own Admin setting the FIRST cap — takes effect
   *  immediately, no approval needed. */
  canEditDirectly: boolean;
  /** BU Admin changing a cap that already exists; sends the Org Admin above
   *  them a governance approval instead (lib/governance.ts). */
  canRequestIncrease: boolean;
  saving: boolean;
  onSave: (patch: {
    monthlyBudgetUsd: number | null;
    budgetStartDate: string | null;
    budgetEndDate: string | null;
  }) => void;
  requesting: boolean;
  onRequestIncrease: (v: number, reason?: string) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [value, setValue] = React.useState<string>(budget !== null ? String(budget) : "");
  const [start, setStart] = React.useState(window.budgetStartDate ?? "");
  const [end, setEnd] = React.useState(window.budgetEndDate ?? "");
  const [requestOpen, setRequestOpen] = React.useState(false);
  const [requestValue, setRequestValue] = React.useState("");
  const [requestReason, setRequestReason] = React.useState("");

  React.useEffect(() => {
    setValue(budget !== null ? String(budget) : "");
  }, [budget]);

  React.useEffect(() => {
    setStart(window.budgetStartDate ?? "");
    setEnd(window.budgetEndDate ?? "");
  }, [window.budgetStartDate, window.budgetEndDate]);

  const windowError = budgetWindowError({ budgetStartDate: start, budgetEndDate: end });

  const pct = budget && budget > 0 ? Math.min(100, (spend / budget) * 100) : 0;
  const over = budget !== null && budget > 0 && spend >= budget;
  const warn = budget !== null && budget > 0 && !over && spend >= 0.8 * budget;
  const barTone = over ? "bg-destructive" : warn ? "bg-warning" : "bg-brand-bright";

  const commit = () => {
    const trimmed = value.trim();
    const parsed = trimmed === "" ? 0 : Number(trimmed);
    if (Number.isNaN(parsed) || parsed < 0) {
      toast.error("Enter a valid non-negative amount");
      return;
    }
    if (windowError) {
      toast.error(windowError);
      return;
    }
    // Clearing the cap clears its window too — a validity period for a budget
    // that no longer exists is a date range about nothing.
    const cleared = parsed === 0;
    onSave({
      monthlyBudgetUsd: cleared ? null : parsed,
      budgetStartDate: cleared ? null : start || null,
      budgetEndDate: cleared ? null : end || null,
    });
    setEditing(false);
  };

  const submitRequest = () => {
    const parsed = Number(requestValue.trim());
    if (!Number.isFinite(parsed) || parsed <= 0) {
      toast.error("Enter a valid positive amount");
      return;
    }
    onRequestIncrease(parsed, requestReason.trim() || undefined);
    setRequestOpen(false);
    setRequestValue("");
    setRequestReason("");
  };

  return (
    <section
      className="border-line-soft bg-panel-elevated rounded-2xl border px-6 py-5 shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_24px_-8px_oklch(0_0_0_/_0.28)]"
      style={{ ...RISE, animationDelay: "0.04s" }}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Coins className="text-brand-bright size-4" aria-hidden />
          <span className="font-display text-[15px] font-bold tracking-[-0.01em]">Monthly budget</span>
          {over && (
            <Badge variant="destructive" className="text-[10px]">Over budget</Badge>
          )}
          {warn && (
            <span className="text-warning font-mono text-[10.5px] font-semibold">80% used</span>
          )}
        </div>

        {canEditDirectly && !editing && (
          <Button
            size="sm"
            variant="outline"
            className="border-line-soft h-7 font-mono text-[11px]"
            onClick={() => setEditing(true)}
          >
            {budget !== null ? "Edit budget" : "Set budget"}
          </Button>
        )}
        {canRequestIncrease && !requestOpen && (
          <Button
            size="sm"
            variant="outline"
            className="border-line-soft h-7 font-mono text-[11px]"
            onClick={() => setRequestOpen(true)}
          >
            Request increase
          </Button>
        )}
      </div>

      {editing ? (
        <div className="mt-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground font-mono text-[13px]">$</span>
            <Input
              type="number"
              min={0}
              step="1"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="0 = no cap"
              className="border-line-soft h-8 w-40 font-mono text-[12px]"
              autoFocus
            />
            <span className="text-muted-foreground font-mono text-[11px]">/ month</span>
            <Button
              size="sm"
              className="from-brand-gradient-from to-brand-gradient-to h-7 bg-gradient-to-br px-3 font-semibold text-white"
              disabled={saving || windowError !== null}
              onClick={commit}
            >
              {saving ? <Loader2 className="size-3.5 animate-spin" /> : "Save"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 w-7 p-0"
              onClick={() => setEditing(false)}
            >
              <X className="size-3.5" aria-hidden />
            </Button>
          </div>
          <BudgetWindowFieldsInput
            start={start}
            end={end}
            onStartChange={setStart}
            onEndChange={setEnd}
            error={windowError}
          />
        </div>
      ) : requestOpen ? (
        <div className="mt-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground font-mono text-[13px]">$</span>
            <Input
              type="number"
              min={0}
              step="1"
              value={requestValue}
              onChange={(e) => setRequestValue(e.target.value)}
              placeholder="New monthly cap"
              className="border-line-soft h-8 w-40 font-mono text-[12px]"
              autoFocus
            />
            <span className="text-muted-foreground font-mono text-[11px]">/ month</span>
          </div>
          <Input
            value={requestReason}
            onChange={(e) => setRequestReason(e.target.value)}
            placeholder="Reason (optional) — why does this business unit need more?"
            className="border-line-soft h-8 text-[12px]"
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              className="from-brand-gradient-from to-brand-gradient-to h-7 bg-gradient-to-br px-3 font-semibold text-white"
              disabled={requesting}
              aria-busy={requesting}
              onClick={submitRequest}
            >
              {requesting ? <Loader2 className="size-3.5 animate-spin" /> : "Send to Org Admin"}
            </Button>
            <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => setRequestOpen(false)}>
              <X className="size-3.5" aria-hidden />
            </Button>
          </div>
          <p className="text-muted-foreground font-mono text-[10.5px]">
            Your Org Admin needs to approve this before the budget changes.
          </p>
        </div>
      ) : (
        <div className="mt-4 space-y-2">
          <div className="flex items-baseline justify-between font-mono text-[12px]">
            <span className="text-foreground font-semibold">{fmtUsd(spend)} used</span>
            <span className="text-muted-foreground">
              {budget !== null && budget > 0 ? `of ${fmtUsd(budget)} / mo` : "No budget set (unlimited)"}
            </span>
          </div>
          {budget !== null && budget > 0 && (
            <div className="bg-surface-1 border-line-soft h-2 overflow-hidden rounded-full border">
              <div
                className={cn("h-full rounded-full transition-all", barTone)}
                style={{ width: `${pct}%` }}
              />
            </div>
          )}
          <BudgetWindowSummary window={window} />
          {over && (
            <p className="text-destructive font-mono text-[10.5px]">
              New runs are blocked until the budget is raised or the month resets.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

// ─── Member row ───────────────────────────────────────────────────────────────
function MemberRow({
  row,
  roles,
  canManage,
  busy,
  index,
  onAdd,
  onRemove,
  onChangeRole,
}: {
  row: RowData;
  roles: RoleRow[];
  canManage: boolean;
  busy: boolean;
  index: number;
  onAdd: (role: string) => void;
  onRemove: () => void;
  onChangeRole: (role: string) => void;
}) {
  const [addingRole, setAddingRole] = React.useState<RoleName>("developer");
  const [isAdding, setIsAdding] = React.useState(false);
  const [confirmRemove, setConfirmRemove] = React.useState(false);
  const allCustomRoles = useAllCustomRoles();
  const roleLabel = React.useCallback(
    (name: string) => resolveRoleLabel(name, allCustomRoles),
    [allCustomRoles],
  );

  const displayName = row.email ?? row.userId;

  return (
    <li
      className={cn(
        "border-line-soft grid grid-cols-[1fr_160px_120px] items-center gap-4 border-b px-6 py-3.5 last:border-b-0",
        "transition-colors duration-150",
        row.isMember ? "hover:bg-surface-1/50" : "bg-muted/5 hover:bg-muted/10",
      )}
      style={{
        animationDelay: `${index * 18}ms`,
      }}
    >
      {/* Identity */}
      <div className="flex min-w-0 items-center gap-3">
        <Avatar className="size-8 shrink-0">
          <AvatarFallback
            className={cn(
              "font-mono text-[11px] font-semibold text-white",
              row.isMember ? avatarBg(row.userId) : "bg-muted text-muted-foreground",
            )}
          >
            {row.initials}
          </AvatarFallback>
        </Avatar>
        <div className="min-w-0">
          <div
            className={cn(
              "truncate text-[13px] font-semibold leading-tight",
              !row.isMember && "text-muted-foreground",
            )}
          >
            {displayName}
          </div>
          {row.email && row.userId !== row.email && (
            <div className="text-muted-foreground truncate font-mono text-[10.5px]">
              {row.userId}
            </div>
          )}
        </div>
      </div>

      {/* Role column */}
      <div>
        {row.isMember && row.roleName ? (
          canManage ? (
            /* Role change dropdown */
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="group inline-flex items-center gap-1.5 rounded-lg border border-transparent px-2.5 py-1 font-mono text-[11.5px] font-semibold transition-all hover:border-line-soft hover:bg-surface-1 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand-bright disabled:opacity-50"
                  disabled={busy}
                >
                  {row.roleName === "admin" && (
                    <ShieldCheck className="text-brand-bright size-3.5 shrink-0" aria-hidden />
                  )}
                  {roleLabel(row.roleName)}
                  <ChevronDown className="text-muted-foreground size-3 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="border-line-soft bg-panel-elevated w-56">
                <DropdownMenuLabel className="text-muted-foreground font-mono text-[9.5px] tracking-widest uppercase">
                  Change {BUSINESS_UNIT_LABEL.toLowerCase()} role
                </DropdownMenuLabel>
                <DropdownMenuSeparator className="bg-line-soft" />
                {roles.map((r) => (
                  <DropdownMenuItem
                    key={r.name}
                    onSelect={() => { if (r.name !== row.roleName) onChangeRole(r.name); }}
                    className="gap-2"
                  >
                    {/* Business Unit Admin (Org Admin's grant only, see
                        assignableRoles) and Project Admin — the fallback
                        approver on every agent for its project (PRD §33.2). */}
                    {(r.name === "bu_admin" || r.name === "project_admin") && (
                      <ShieldCheck className="text-brand-bright size-3.5 shrink-0" aria-hidden />
                    )}
                    <span className="flex-1">
                      <span className="block text-[13px] font-medium">{r.label}</span>
                      <span className="text-muted-foreground block font-mono text-[10px]">{r.desc}</span>
                    </span>
                    {r.name === row.roleName && (
                      <Check className="text-brand-bright size-3.5" aria-hidden />
                    )}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            /* Read-only role badge */
            <span className="inline-flex items-center gap-1.5 font-mono text-[11.5px] font-semibold">
              {(row.roleName === "admin" || row.roleName === "bu_admin") && (
                <ShieldCheck className="text-brand-bright size-3.5" aria-hidden />
              )}
              {roleLabel(row.roleName)}
            </span>
          )
        ) : isAdding ? (
          /* Inline role picker for "add" flow */
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="border-line-soft inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 font-mono text-[11.5px] font-semibold text-foreground focus-visible:outline-none focus-visible:ring-1">
                {roleLabel(addingRole)}
                <ChevronDown className="size-3" aria-hidden />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="border-line-soft bg-panel-elevated w-56">
              {roles.map((r) => (
                <DropdownMenuItem
                  key={r.name}
                  onSelect={() => setAddingRole(r.name)}
                  className="gap-2"
                >
                  <span className="flex-1">
                    <span className="block text-[13px] font-medium">{r.label}</span>
                    <span className="text-muted-foreground block font-mono text-[10px]">{r.desc}</span>
                  </span>
                  {r.name === addingRole && <Check className="text-brand-bright size-3.5" aria-hidden />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <span className="text-muted-foreground font-mono text-[11.5px]">No access</span>
        )}
      </div>

      {/* Actions column */}
      <div className="flex items-center justify-end gap-1.5">
        {row.isMember && canManage ? (
          confirmRemove ? (
            /* Inline confirm */
            <div className="flex items-center gap-1">
              <span className="text-muted-foreground mr-1 font-mono text-[10.5px]">Remove?</span>
              <Button
                size="sm"
                variant="destructive"
                className="h-6 px-2 text-[11px]"
                disabled={busy}
                onClick={() => {
                  setConfirmRemove(false);
                  onRemove();
                }}
              >
                Yes
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 w-6 p-0"
                onClick={() => setConfirmRemove(false)}
              >
                <X className="size-3" aria-hidden />
              </Button>
            </div>
          ) : (
            <Button
              variant="ghost"
              size="icon"
              className="text-muted-foreground hover:text-destructive size-7 shrink-0 opacity-0 transition-all group-hover:opacity-100 [li:hover_&]:opacity-100"
              aria-label={`Remove from ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
              disabled={busy}
              onClick={() => setConfirmRemove(true)}
            >
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          )
        ) : !row.isMember && canManage ? (
          isAdding ? (
            /* Confirm / Cancel add */
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                className="from-brand-gradient-from to-brand-gradient-to h-7 bg-gradient-to-br px-3 font-semibold text-white shadow-[0_2px_8px_-2px_oklch(0.6_0.2_35_/_0.5)]"
                disabled={busy}
                onClick={() => {
                  setIsAdding(false);
                  onAdd(addingRole);
                }}
              >
                {busy ? <Loader2 className="size-3.5 animate-spin" /> : "Add"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 w-7 p-0"
                onClick={() => { setIsAdding(false); setAddingRole("developer"); }}
              >
                <X className="size-3.5" aria-hidden />
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="border-line-soft h-7 gap-1.5 font-mono text-[11px]"
              disabled={busy}
              onClick={() => setIsAdding(true)}
            >
              <UserPlus className="size-3.5" aria-hidden />
              Add
            </Button>
          )
        ) : null}
      </div>
    </li>
  );
}
