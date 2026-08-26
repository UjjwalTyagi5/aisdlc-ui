"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  Boxes,
  ChevronRight,
  Coins,
  FolderKanban,
  Loader2,
  Pencil,
  Plug,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import {
  getWorkspace,
  updateWorkspace,
  requestBudgetIncrease,
} from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

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
  const isOrgAdmin = role === "org_admin";
  const canSetActive = isOrgAdmin;

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

  const ws = wsQ.data;

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

      {/* People, models and connectors are governed on the screens that own
          them — this unit page carried a second copy of all three. A member
          list here could only ever show the slice of a person that belongs to
          this unit, and the grant cards duplicated decisions that are made
          once, per model and per connector, for every unit at a time. */}
      {ws && <ManagedElsewhere workspaceId={id} unitName={ws.displayName} />}
    </div>
  );
}

// ─── Managed elsewhere ────────────────────────────────────────────────────────
/**
 * Three links out, for the three things a unit has that this screen no longer
 * edits.
 *
 * This is deliberately signposting, not a summary: a count here would be a
 * fourth place the same number is stated, and the header above already carries
 * the member and project totals. What was missing when the sections came off
 * was the answer to "then where?" — so that, and only that, is what it gives.
 *
 * People go to the directory filtered to this unit; a person is one row with
 * bindings in several scopes, and the unit-local list could never say that.
 * Model and connector grants are per-model and per-connector decisions that
 * name every unit at once, so they belong on the model and connector screens
 * rather than being re-entered unit by unit.
 */
function ManagedElsewhere({
  workspaceId,
  unitName,
}: {
  workspaceId: string;
  unitName: string;
}) {
  const links = [
    {
      href: `/users?bu=${encodeURIComponent(workspaceId)}`,
      icon: Users,
      title: "Members",
      blurb: `Everyone bound to ${unitName}, and the roles they hold — in the people directory, filtered to this unit.`,
    },
    {
      href: "/admin/models",
      icon: Boxes,
      title: "Models",
      blurb: `Which models this ${BUSINESS_UNIT_LABEL.toLowerCase()} may use is decided per model, on the models screen.`,
    },
    {
      href: "/integrations",
      icon: Plug,
      title: "Connectors",
      blurb: `Open a connector to see and change which ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} hold it.`,
    },
  ];

  return (
    <section
      className="border-line-soft bg-panel-elevated overflow-hidden rounded-2xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_24px_-8px_oklch(0_0_0_/_0.28)]"
      style={{ ...RISE, animationDelay: "0.06s" }}
    >
      <div className="border-line-soft border-b px-6 py-4">
        <span className="font-display text-[15px] font-bold tracking-[-0.01em]">
          Managed elsewhere
        </span>
      </div>
      <ul>
        {links.map(({ href, icon: Icon, title, blurb }) => (
          <li key={href}>
            <Link
              href={href}
              className="border-line-soft hover:bg-surface-1/50 flex items-center gap-4 border-b px-6 py-4 transition-colors last:border-b-0 [li:last-child_&]:border-b-0"
            >
              <Icon className="text-brand-bright size-4 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold">{title}</span>
                <span className="text-muted-foreground block text-[12px]">{blurb}</span>
              </span>
              <ChevronRight className="text-muted-foreground size-4 shrink-0" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </section>
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
          <span className="font-display text-[15px] font-bold tracking-[-0.01em]">Total budget</span>
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
              placeholder="New total cap"
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
