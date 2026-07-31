"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ShieldCheck, UserPlus, Users, ChevronDown, Check } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { AccessTabs } from "@/components/app/access-tabs";
import { OnboardPersonDialog } from "@/components/app/onboard-person-dialog";
import { ProjectAccessPanel } from "@/components/app/project-access-panel";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { qk } from "@/lib/api/query-keys";
import {
  assignAccessRole,
  listAccessMembers,
  listAccessRoles,
  listAccessWorkspaces,
  revokeAccessRole,
} from "@/lib/api/access";
import { onboardPerson } from "@/lib/api/onboarding";
import { listProjects } from "@/lib/api/projects";
import { useAssignableRoles } from "@/hooks/use-assignable-roles";
import type { AccessMember, AccessRole } from "@/lib/schemas/access";

/** Brand-tinted badge for the all-powerful admin role; calm outline for the rest. */
function roleBadgeClass(roleName: string): string {
  return roleName === "admin"
    ? "border-primary/40 bg-primary/10 text-primary"
    : "border-line-soft text-foreground";
}

const RISE = {
  animationName: "rise",
  animationDuration: "0.6s",
  animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  animationFillMode: "both" as const,
};

export default function AccessPage() {
  const session = useSession({ required: true });
  // Phase 6: gate on member:manage (matrix) instead of legacy role === "admin".
  // delivery_lead has member:manage and must reach this surface (Task 1 aligns backend).
  if (!hasPermission(session, "member:manage")) {
    return (
      <RestrictedAccess description="Managing members requires the member:manage permission." />
    );
  }
  return <AccessPageInner />;
}

/**
 * Which scope's assignments the page is editing. A role is a binding of
 * (person, scope, role), so the screen that assigns roles has to be able to
 * name the scope — it previously only ever offered Business Units, leaving
 * project bindings editable nowhere but inside the project itself.
 */
type AssignmentScope = "business_unit" | "project";

function AccessPageInner() {
  const queryClient = useQueryClient();
  const [scope, setScope] = React.useState<AssignmentScope>("business_unit");
  const [workspaceId, setWorkspaceId] = React.useState<string | null>(null);
  const [projectId, setProjectId] = React.useState<string | null>(null);
  const [addMemberOpen, setAddMemberOpen] = React.useState(false);
  const inviteRoleOptions = useAssignableRoles("business_unit");

  const workspacesQ = useQuery({
    queryKey: qk.access.workspaces(),
    queryFn: listAccessWorkspaces,
  });

  const projectsQ = useQuery({
    queryKey: ["access", "projects"],
    queryFn: () => listProjects({ pageSize: 200 }),
    staleTime: 60_000,
    enabled: scope === "project",
  });
  const projects = React.useMemo(
    () => (projectsQ.data?.items ?? []).filter((p) => !p.archived),
    [projectsQ.data],
  );

  // Default to the first project once loaded, mirroring the unit selector.
  React.useEffect(() => {
    if (scope === "project" && !projectId && projects.length > 0) {
      setProjectId(projects[0]!.id);
    }
  }, [scope, projectId, projects]);

  const activeProject = projects.find((p) => p.id === projectId);
  const rolesQ = useQuery({
    queryKey: qk.access.roles(),
    queryFn: listAccessRoles,
  });

  // Default to the first workspace once loaded.
  React.useEffect(() => {
    if (!workspaceId && workspacesQ.data && workspacesQ.data.length > 0) {
      setWorkspaceId(workspacesQ.data[0]!.id);
    }
  }, [workspaceId, workspacesQ.data]);

  const membersQ = useQuery({
    queryKey: qk.access.members(workspaceId ?? "—"),
    queryFn: () => listAccessMembers(workspaceId!),
    enabled: !!workspaceId && scope === "business_unit",
    placeholderData: (prev) => prev,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: qk.access.members(workspaceId ?? "—"),
    });

  const assignM = useMutation({
    mutationFn: assignAccessRole,
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not assign role"),
  });
  const revokeM = useMutation({
    mutationFn: revokeAccessRole,
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not revoke role"),
  });

  const roles = React.useMemo(() => rolesQ.data ?? [], [rolesQ.data]);
  const roleByName = React.useMemo(() => new Map(roles.map((r) => [r.name, r] as const)), [roles]);

  const [addOpen, setAddOpen] = React.useState(false);

  const workspaceName = workspacesQ.data?.find((w) => w.id === workspaceId)?.name ?? "";

  const toggleRole = (member: AccessMember, roleName: string, next: boolean) => {
    if (!workspaceId) return;
    const input = { userId: member.userId, workspaceId, roleName };
    if (next) {
      assignM.mutate(input, {
        onSuccess: () => toast.success(`Granted ${labelFor(roleByName, roleName)}`),
      });
    } else {
      revokeM.mutate(input, {
        onSuccess: () => toast.success(`Removed ${labelFor(roleByName, roleName)}`),
      });
    }
  };

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      {/* Editorial header */}
      <header
        className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end"
        style={RISE}
      >
        <div>
          <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
            <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
            Govern
          </div>
          <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
            Roles &amp; Access
          </h1>
          <p className="text-muted-foreground mt-2 max-w-[580px] text-[14px]">
            The single place roles are defined and assigned, in either scope a
            role can be bound to. Separation of duties is enforced{" "}
            <span className="text-foreground">within</span> a scope, not across
            them — the same person may govern one {BUSINESS_UNIT_LABEL.toLowerCase()} and deliver
            on a project in another. Every grant and revoke is written to the
            audit trail.
          </p>
        </div>

        {/* Scope, then the thing in it — a role is a binding of person, scope and role. */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <Label className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
              Scope
            </Label>
            <Select value={scope} onValueChange={(v) => setScope(v as AssignmentScope)}>
              <SelectTrigger className="border-line-soft h-9 w-44" aria-label="Select scope">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="business_unit">{BUSINESS_UNIT_LABEL}</SelectItem>
                <SelectItem value="project">Project</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {scope === "business_unit" ? (
            <div className="flex flex-col gap-1.5">
              <Label className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
                {BUSINESS_UNIT_LABEL}
              </Label>
              <Select value={workspaceId ?? undefined} onValueChange={(v) => setWorkspaceId(v)}>
                <SelectTrigger
                  className="border-line-soft h-9 w-60"
                  aria-label={`Select ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
                >
                  <SelectValue placeholder={`Select ${BUSINESS_UNIT_LABEL.toLowerCase()}…`} />
                </SelectTrigger>
                <SelectContent>
                  {(workspacesQ.data ?? []).map((w) => (
                    <SelectItem key={w.id} value={w.id}>
                      {w.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
                Project
              </Label>
              <Select value={projectId ?? undefined} onValueChange={(v) => setProjectId(v)}>
                <SelectTrigger className="border-line-soft h-9 w-60" aria-label="Select project">
                  <SelectValue placeholder="Select project…" />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
      </header>

      {/* One screen, three views (PRD §33.1) */}
      <AccessTabs />

      {/* Roles reference strip */}
      <RolesReference roles={roles} loading={rolesQ.isLoading} />

      {/* Members panel — one per scope; project bindings are single-role. */}
      {scope === "project" ? (
        projectsQ.isLoading ? (
          <LoadingState variant="table" rows={6} />
        ) : !projectId || !activeProject ? (
          <EmptyState
            icon={Users}
            title="No projects yet"
            description="Create a project first — project roles are bound to a project that exists."
          />
        ) : (
          <ProjectAccessPanel projectId={activeProject.id} projectName={activeProject.name} />
        )
      ) : membersQ.isError ? (
        <ApiErrorState
          title="Couldn't load people"
          description={membersQ.error instanceof Error ? membersQ.error.message : undefined}
          onRetry={() => membersQ.refetch()}
        />
      ) : membersQ.isLoading || !workspaceId ? (
        <LoadingState variant="table" rows={6} />
      ) : (
        <section
          className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_20px_-8px_oklch(0_0_0_/_0.35)]"
          style={{ ...RISE, animationDelay: "0.05s" }}
        >
          {/* Panel header */}
          <div className="border-line-soft flex items-center justify-between gap-2 border-b px-5 py-3.5">
            <div className="flex items-center gap-2">
              <span className="font-display text-[13.5px] font-bold tracking-[-0.01em]">
                People
              </span>
              <span className="text-muted-foreground font-mono text-[10.5px]">
                {(membersQ.data ?? []).length} in {workspaceName}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                disabled={!workspaceId}
                onClick={() => setAddMemberOpen(true)}
              >
                <UserPlus className="size-4" aria-hidden />
                Add member
              </Button>
              <Button size="sm" onClick={() => setAddOpen(true)} className="gap-1.5">
                <UserPlus className="size-4" aria-hidden />
                Assign role
              </Button>
            </div>
          </div>

          {(membersQ.data ?? []).length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={Users}
                title="No one has access yet"
                description={`Assign a role to add the first person to this ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
              />
            </div>
          ) : (
            <ul>
              {(membersQ.data ?? []).map((m) => (
                <MemberRow
                  key={m.userId}
                  member={m}
                  roles={roles}
                  roleByName={roleByName}
                  busy={assignM.isPending || revokeM.isPending}
                  onToggleRole={toggleRole}
                />
              ))}
            </ul>
          )}
        </section>
      )}

      <AssignDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        roles={roles}
        disabled={!workspaceId}
        onSubmit={(userId, roleName) => {
          if (!workspaceId) return;
          assignM.mutate(
            { userId, workspaceId, roleName },
            {
              onSuccess: () => {
                toast.success(`Assigned ${labelFor(roleByName, roleName)} to ${userId}`);
                setAddOpen(false);
              },
            },
          );
        }}
      />

      {workspaceId && (
        <OnboardPersonDialog
          open={addMemberOpen}
          onOpenChange={setAddMemberOpen}
          roleOptions={inviteRoleOptions}
          title="Add a member"
          description={`Onboards a new or existing person and grants them a role in this ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
          onSubmit={async (input) => {
            const result = await onboardPerson({
              email: input.email,
              displayName: input.displayName,
              workspaceId,
              role: input.roleName,
            });
            toast.success(`${result.displayName} onboarded to ${workspaceName}`);
            invalidate();
          }}
        />
      )}
    </div>
  );
}

function labelFor(roleByName: Map<string, AccessRole>, name: string): string {
  return roleByName.get(name)?.label ?? name;
}

// ───────────────────────── Roles reference strip ─────────────────────────

function RolesReference({ roles, loading }: { roles: AccessRole[]; loading: boolean }) {
  return (
    <div
      className="border-line-soft bg-surface-1 rounded-xl border px-5 py-4"
      style={{ ...RISE, animationDelay: "0.02s" }}
    >
      <div className="text-muted-foreground mb-3 flex items-center gap-2 font-mono text-[10px] tracking-widest uppercase">
        <ShieldCheck className="size-3.5" aria-hidden />
        Roles &amp; what they grant
      </div>
      {loading ? (
        <div className="text-muted-foreground text-sm">Loading roles…</div>
      ) : (
        <TooltipProvider delayDuration={150}>
          <div className="flex flex-wrap gap-2">
            {roles.map((r) => (
              <Tooltip key={r.name}>
                <TooltipTrigger asChild>
                  <span
                    className={cn(
                      "inline-flex cursor-default items-center gap-1.5 rounded-md border px-2.5 py-1 font-mono text-[11px]",
                      roleBadgeClass(r.name),
                    )}
                  >
                    {r.name === "admin" && <ShieldCheck className="size-3" aria-hidden />}
                    {r.label}
                  </span>
                </TooltipTrigger>
                <TooltipContent
                  side="bottom"
                  className="border-brand-bright/30 bg-panel-elevated text-foreground ring-brand-bright/10 relative max-w-[280px] overflow-hidden rounded-lg border p-3 shadow-[0_12px_30px_-12px_oklch(0_0_0_/_0.45)] ring-1"
                >
                  {/* Faint brand wash — warms the panel without harming contrast */}
                  <span
                    aria-hidden
                    className="from-brand-bright/[0.1] pointer-events-none absolute inset-0 bg-gradient-to-b to-transparent"
                  />
                  <div className="relative">
                    <p className="text-foreground mb-2 text-[12px] font-medium leading-snug">
                      {r.description}
                    </p>
                    <div className="text-brand-bright mb-1.5 font-mono text-[9px] font-semibold tracking-widest uppercase">
                      Grants
                    </div>
                    <ul className="text-foreground/85 grid gap-y-0.5 font-mono text-[10.5px] leading-relaxed">
                      {r.permissions.map((p) => (
                        <li key={p} className="flex items-center gap-1.5">
                          <span className="text-brand-bright" aria-hidden>
                            ·
                          </span>
                          {p}
                        </li>
                      ))}
                    </ul>
                  </div>
                </TooltipContent>
              </Tooltip>
            ))}
          </div>
        </TooltipProvider>
      )}
    </div>
  );
}

// ───────────────────────── Member row ─────────────────────────

function MemberRow({
  member,
  roles,
  roleByName,
  busy,
  onToggleRole,
}: {
  member: AccessMember;
  roles: AccessRole[];
  roleByName: Map<string, AccessRole>;
  busy: boolean;
  onToggleRole: (m: AccessMember, roleName: string, next: boolean) => void;
}) {
  const displayName = member.name ?? member.email ?? member.userId;
  return (
    <li className="border-line-soft hover:bg-surface-1 flex items-center gap-4 border-b px-5 py-3 last:border-b-0">
      {/* Identity */}
      <Avatar className="size-9 shrink-0">
        <AvatarFallback className="bg-secondary font-mono text-[11px] font-semibold">
          {member.initials}
        </AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold">{displayName}</div>
        <div className="text-muted-foreground truncate font-mono text-[11px]">
          {member.email ?? member.userId}
        </div>
      </div>

      {/* Current roles */}
      <div className="hidden flex-wrap items-center justify-end gap-1.5 sm:flex sm:max-w-[46%]">
        {member.roles.length === 0 ? (
          <span className="text-muted-foreground font-mono text-[11px]">no roles</span>
        ) : (
          member.roles.map((rn) => (
            <Badge
              key={rn}
              variant="outline"
              className={cn("font-mono text-[10px]", roleBadgeClass(rn))}
            >
              {roleByName.get(rn)?.label ?? rn}
            </Badge>
          ))
        )}
      </div>

      {/* Manage roles */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="border-line-soft shrink-0 gap-1"
            disabled={busy}
          >
            Manage
            <ChevronDown className="size-3.5" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuLabel className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
            Roles for {member.name ?? member.userId}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {roles.map((r) => {
            const held = member.roles.includes(r.name);
            return (
              <DropdownMenuCheckboxItem
                key={r.name}
                checked={held}
                onSelect={(e) => e.preventDefault()}
                onCheckedChange={(c) => onToggleRole(member, r.name, c)}
                className="gap-2"
              >
                <span className="flex flex-1 items-center gap-1.5">
                  {r.name === "admin" && (
                    <ShieldCheck className="text-primary size-3" aria-hidden />
                  )}
                  <span className="font-medium">{r.label}</span>
                </span>
                {held && <Check className="text-primary size-3.5" aria-hidden />}
              </DropdownMenuCheckboxItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

// ───────────────────────── Assign dialog ─────────────────────────

function AssignDialog({
  open,
  onOpenChange,
  roles,
  disabled,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  roles: AccessRole[];
  disabled: boolean;
  onSubmit: (userId: string, roleName: string) => void;
}) {
  const [userId, setUserId] = React.useState("");
  const [roleName, setRoleName] = React.useState<string>("");

  React.useEffect(() => {
    if (open) {
      setUserId("");
      setRoleName(roles[0]?.name ?? "");
    }
  }, [open, roles]);

  const canSubmit = userId.trim().length > 0 && roleName.length > 0 && !disabled;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display tracking-[-0.01em]">Assign a role</DialogTitle>
          <DialogDescription>
            Grant a role to a person in this {BUSINESS_UNIT_LABEL.toLowerCase()}. Use their user ID or
            email (as it appears in your identity provider).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="access-user" className="text-xs">
              User ID or email
            </Label>
            <Input
              id="access-user"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="auth0|abc123  ·  person@company.com"
              className="border-line-soft font-mono text-sm"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="access-role" className="text-xs">
              Role
            </Label>
            <Select value={roleName} onValueChange={setRoleName}>
              <SelectTrigger id="access-role" className="border-line-soft">
                <SelectValue placeholder="Select a role…" />
              </SelectTrigger>
              <SelectContent>
                {roles.map((r) => (
                  <SelectItem key={r.name} value={r.name}>
                    <span className="flex items-center gap-2">
                      <span className="font-medium">{r.label}</span>
                      <span className="text-muted-foreground font-mono text-[10px]">{r.name}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit} onClick={() => onSubmit(userId.trim(), roleName)}>
            Assign role
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
