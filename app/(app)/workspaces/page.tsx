"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Archive,
  Building2,
  Check,
  Coins,
  FolderKanban,
  MoreVertical,
  Pencil,
  Plus,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { useRawSession } from "@/components/auth/session-provider";
import { hasPermission } from "@/lib/auth/permissions";
import { CreateWorkspaceDialog } from "@/components/app/create-workspace-dialog";
import { ChangeBuAdminDialog } from "@/components/app/change-bu-admin-dialog";
import { EditBuDetailsDialog } from "@/components/app/edit-bu-details-dialog";
import { PersonaBadge, ScopeChip } from "@/components/app/scope-indicator";
import { useWorkspaces, useActiveWorkspace } from "@/hooks/use-workspaces";
import { useAccessScope } from "@/hooks/use-access-scope";
import { archiveWorkspace } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { ActiveBadge } from "@/components/app/active-badge";
import type { Workspace } from "@/lib/schemas";

const RISE = {
  animationName: "rise",
  animationDuration: "0.6s",
  animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  animationFillMode: "both",
} as const;

function usd(n: number): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export default function WorkspacesPage() {
  const session = useRawSession();
  const queryClient = useQueryClient();
  const router = useRouter();
  const searchParams = useSearchParams();

  const workspacesQ = useWorkspaces();
  const { active, setActive } = useActiveWorkspace();
  const { role, isOrgWide, level, scope, managedBusinessUnitIds } = useAccessScope();
  const canManage = hasPermission(session, "workspace:manage");

  // Creating a Business Unit is an Organization Admin action — this page's own
  // copy says so, but the button was gated on `workspace:manage`, which a
  // Business Unit Admin also holds (they manage THEIR unit). Scope is what
  // distinguishes the two: only an org-wide viewer creates a new unit.
  const canCreate = canManage && isOrgWide;

  const [createOpen, setCreateOpen] = React.useState(false);

  // `?new=1` is a deep link (the sidebar switcher uses it), so it has to be
  // gated like any other entry point — it opened the dialog unconditionally
  // before, which meant the URL alone was enough to bypass the check the
  // button next to it performs.
  //
  // Scope resolves asynchronously, so this waits for the answer rather than
  // reading `isOrgWide` while it is still undefined; stripping the param on an
  // unresolved scope would silently swallow a legitimate request.
  const scopeResolved = scope !== null;
  React.useEffect(() => {
    if (searchParams.get("new") !== "1" || !scopeResolved) return;
    if (canCreate) setCreateOpen(true);
    router.replace("/workspaces");
  }, [searchParams, scopeResolved, canCreate, router]);

  const archiveMutation = useMutation({
    mutationFn: (id: string) => archiveWorkspace(id),
    onSuccess: (ws) => {
      toast.success(`Business unit "${ws.displayName}" archived`);
      queryClient.invalidateQueries({ queryKey: qk.workspaces.all() });
    },
    onError: (err) =>
      toast.error("Couldn't archive business unit", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  if (workspacesQ.isLoading) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <LoadingState variant="list" rows={3} />
      </div>
    );
  }

  if (workspacesQ.isError) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ApiErrorState
          title="Couldn't load business units"
          description={
            workspacesQ.error instanceof Error ? workspacesQ.error.message : "Unknown error."
          }
          onRetry={() => workspacesQ.refetch()}
        />
      </div>
    );
  }

  const workspaces = workspacesQ.data ?? [];
  const activeList = workspaces.filter((w) => w.status === "active");
  const archived = workspaces.filter((w) => w.status === "archived");

  return (
    <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
      <header className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end" style={RISE}>
        <div>
          <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
            <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
            Govern
          </div>
          <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
            Business Units
          </h1>

          {scope !== null && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <ScopeChip
                kind={isOrgWide ? "organization" : level}
                name={
                  isOrgWide
                    ? null
                    : activeList.length === 1
                      ? activeList[0]!.displayName
                      : `${activeList.length} of yours`
                }
                size="sm"
              />
              <PersonaBadge role={role} />
            </div>
          )}

          <p className="text-muted-foreground mt-2 max-w-[560px] text-[14px]">
            A business unit isolates a department&apos;s budget, connections and
            data: its own spend cap, its own connected Jira or Azure DevOps
            instance, and no visibility into the others. Only the Organization
            Admin creates one.
            {!isOrgWide && managedBusinessUnitIds.length > 0 && (
              <>
                {" "}
                You&apos;re seeing the{" "}
                {managedBusinessUnitIds.length === 1
                  ? "unit you administer"
                  : `${managedBusinessUnitIds.length} units you administer`}
                .
              </>
            )}
          </p>
        </div>
        {canCreate && (
          <Button
            onClick={() => setCreateOpen(true)}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)]"
          >
            <Plus className="size-4" aria-hidden />
            New business unit
          </Button>
        )}
      </header>

      {activeList.length === 0 ? (
        <Card className="border-line-soft bg-panel-elevated flex flex-col items-center gap-3 rounded-2xl border border-dashed p-10 text-center">
          <Building2 className="text-muted-foreground size-8" aria-hidden />
          <div>
            <p className="font-display text-base font-semibold">
              No business units yet
            </p>
            <p className="text-muted-foreground mt-1 text-[13px]">
              {canManage
                ? "Create the first business unit to give projects, members, connections and budgets a home. A unit and its admin are created together — there is never an ownerless scope."
                : "You haven't been added to a business unit yet. Ask your Organization Admin to invite you."}
            </p>
          </div>
          {canCreate && (
            <Button onClick={() => setCreateOpen(true)} variant="outline" className="border-line-soft">
              <Plus className="size-4" aria-hidden />
              New business unit
            </Button>
          )}
        </Card>
      ) : (
        <ul className="grid items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {activeList.map((ws) => (
            <WorkspaceCard
              key={ws.id}
              workspace={ws}
              isActive={active?.id === ws.id}
              canManage={canManage}
              canChangeAdmin={canCreate}
              onSetActive={() => {
                setActive(ws.id);
                queryClient.invalidateQueries();
                toast.success(`Switched to ${ws.displayName}`);
              }}
              onArchive={() => archiveMutation.mutate(ws.id)}
            />
          ))}
        </ul>
      )}

      {archived.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-muted-foreground font-mono text-[11px] tracking-[0.14em] uppercase">
            Archived
          </h2>
          <ul className="grid items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {archived.map((ws) => (
              <WorkspaceCard key={ws.id} workspace={ws} isActive={false} canManage={false} archived />
            ))}
          </ul>
        </section>
      )}

      <CreateWorkspaceDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}

function Stat({ icon: Icon, label, value, onClick }: { icon: LucideIcon; label: string; value: string; onClick?: () => void }) {
  if (onClick) {
    return (
      <button
        onClick={onClick}
        className="hover:text-foreground flex items-center gap-1.5 transition-colors"
        aria-label={`${value} ${label}`}
      >
        <Icon className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
        <span className="font-mono text-[12px] font-medium">{value}</span>
        <span className="text-muted-foreground text-[11px] underline-offset-2 hover:underline">{label}</span>
      </button>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <Icon className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
      <span className="font-mono text-[12px]">{value}</span>
      <span className="text-muted-foreground text-[11px]">{label}</span>
    </div>
  );
}

function WorkspaceCard({
  workspace: ws,
  isActive,
  canManage,
  archived,
  onSetActive,
  onArchive,
  canChangeAdmin,
}: {
  workspace: Workspace;
  isActive: boolean;
  canManage: boolean;
  /** Org Admin only — see the comment on the menu item below. */
  canChangeAdmin?: boolean;
  archived?: boolean;
  onSetActive?: () => void;
  onArchive?: () => void;
}) {
  const [adminOpen, setAdminOpen] = React.useState(false);
  const [editOpen, setEditOpen] = React.useState(false);

  return (
    <li className="h-full">
      <Card
        className={cn(
          "bg-panel-elevated flex h-full flex-col gap-4 rounded-2xl border p-5 transition-shadow",
          isActive
            ? "border-brand-bright/40 ring-brand-bright/20 ring-1"
            : "border-line-soft hover:shadow-[0_10px_30px_-14px_oklch(0_0_0_/_0.5)]",
          archived && "opacity-70",
        )}
      >
        {/* Card header */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="font-display truncate text-[16px] font-bold tracking-[-0.01em]">
              {ws.displayName}
            </h3>
            <p className="text-muted-foreground mt-0.5 truncate font-mono text-[11px] tracking-wider uppercase">
              {ws.businessUnit ?? ws.slug}
              {ws.costCenter ? ` · ${ws.costCenter}` : ""}
            </p>
            <p className="text-muted-foreground mt-1 flex items-center gap-1.5 truncate text-[12px]">
              <ShieldCheck className="text-brand-bright size-3.5 shrink-0" aria-hidden />
              {ws.buAdminName ?? "No admin appointed"}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {isActive && (
              <span className="text-brand-bright bg-brand-bright/10 border-brand-bright/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase">
                <Check className="size-3" aria-hidden />
                Active
              </span>
            )}
            {canManage && !archived && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="size-7" aria-label="Business unit actions">
                    <MoreVertical className="size-4" aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="border-line-soft bg-panel-elevated">
                  <DropdownMenuItem onSelect={() => setEditOpen(true)}>
                    <Pencil className="size-4" aria-hidden />
                    Edit details
                  </DropdownMenuItem>
                  {/* Appointing a unit's admin is the Org Admin's call, not the
                      sitting admin's — `canManage` alone would show this to the
                      BU Admin, who must not pick their own replacement. */}
                  {canChangeAdmin && (
                    <DropdownMenuItem onSelect={() => setAdminOpen(true)}>
                      <ShieldCheck className="size-4" aria-hidden />
                      {ws.buAdminName ? "Change admin" : "Appoint admin"}
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onSelect={() => onArchive?.()} className="text-destructive">
                    <Archive className="size-4" aria-hidden />
                    Archive business unit
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>

        {/* Rendered without a wrapper: ActiveBadge is null for an active unit,
            and an empty flex child would still claim one of the card's gap-4
            rows — leaving a hole on every card that has nothing to flag. */}
        <ActiveBadge isActive={ws.isActive} className="w-fit text-[10px]" />

        {/* Stats */}
        <div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-4 gap-y-1">
          <Stat icon={Users} label="members" value={String(ws.memberCount)} />
          <Stat icon={FolderKanban} label="projects" value={String(ws.projectCount)} />
          <Stat icon={Coins} label="/mo" value={usd(ws.monthlySpendUsd)} />
        </div>

        {/* CTA row */}
        {!archived && (
          <div className="flex items-center gap-2">
            <Button
              asChild
              size="sm"
              className="from-brand-gradient-from to-brand-gradient-to flex-1 bg-gradient-to-br font-semibold text-white shadow-[0_3px_10px_-4px_oklch(0.6_0.2_35_/_0.5)]"
            >
              <Link href={`/workspaces/${ws.id}`}>
                <Users className="size-3.5" aria-hidden />
                Members ({ws.memberCount})
              </Link>
            </Button>
            {!isActive && (
              <Button
                variant="outline"
                size="sm"
                onClick={onSetActive}
                className="border-line-soft shrink-0"
              >
                Set active
              </Button>
            )}
          </div>
        )}
      </Card>

      {canManage && !archived && (
        <EditBuDetailsDialog workspace={ws} open={editOpen} onOpenChange={setEditOpen} />
      )}

      {canChangeAdmin && (
        <ChangeBuAdminDialog
          workspaceId={String(ws.id)}
          workspaceName={ws.displayName}
          currentAdminName={ws.buAdminName ?? null}
          open={adminOpen}
          onOpenChange={setAdminOpen}
        />
      )}
    </li>
  );
}
