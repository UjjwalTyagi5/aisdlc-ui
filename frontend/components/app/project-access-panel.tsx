"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2, UserPlus, Users } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
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
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { OnboardPersonDialog } from "@/components/app/onboard-person-dialog";
import { qk } from "@/lib/api/query-keys";
import {
  addProjectMember,
  listProjectMembers,
  removeProjectMember,
  updateProjectMemberRole,
} from "@/lib/api/project-members";
import { toRoleOption, useAssignableRoles } from "@/hooks/use-assignable-roles";
import { ROLE_META } from "@/lib/roles";
import type { ProjectId } from "@/lib/schemas";
import type { ProjectMember } from "@/lib/schemas/project-membership";

/**
 * Project-scope assignments for Roles & Access.
 *
 * Business-Unit membership is multi-role (a checkbox list); a project binding
 * is deliberately single-role — one person does one job on one project — so
 * this panel is a row of Selects rather than a copy of the BU member list.
 *
 * The same person appears here under a different role than they hold in their
 * Business Unit, and under a different role again on the next project. That
 * is the point: a role is a binding of (person, scope, role).
 */
export function ProjectAccessPanel({
  projectId,
  projectName,
}: {
  projectId: ProjectId;
  projectName: string;
}) {
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = React.useState(false);

  // Project Admin is assignable *here* but not from the project's own Members
  // page: appointing a project's admin is a governance act, so it belongs on
  // the governance screen. `useAssignableRoles("project")` is the contributor
  // set (plus project-scoped custom roles) and deliberately excludes it.
  const contributorRoles = useAssignableRoles("project");
  const roleOptions = React.useMemo(
    () => [toRoleOption("project_admin"), ...contributorRoles],
    [contributorRoles],
  );
  const labelFor = React.useCallback(
    (name: string) => roleOptions.find((r) => r.value === name)?.label ?? name.replace(/_/g, " "),
    [roleOptions],
  );

  const membersQ = useQuery({
    queryKey: qk.projectMembers.list(projectId),
    queryFn: () => listProjectMembers(projectId),
    placeholderData: (prev) => prev,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: qk.projectMembers.list(projectId) });

  const roleM = useMutation({
    mutationFn: ({ membershipId, roleName }: { membershipId: string; roleName: string }) =>
      updateProjectMemberRole(projectId, membershipId, roleName),
    onSuccess: (m) => {
      toast.success(`${m.identity.displayName} is now ${labelFor(m.role)} on ${projectName}`);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not change role"),
  });

  const removeM = useMutation({
    mutationFn: (membershipId: string) => removeProjectMember(projectId, membershipId),
    onSuccess: () => {
      toast.success(`Removed from ${projectName}`);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not remove member"),
  });

  const members = membersQ.data ?? [];
  const busy = roleM.isPending || removeM.isPending;

  if (membersQ.isError) {
    return (
      <ApiErrorState
        title="Couldn't load this project's people"
        description={membersQ.error instanceof Error ? membersQ.error.message : undefined}
        onRetry={() => membersQ.refetch()}
      />
    );
  }

  if (membersQ.isLoading) return <LoadingState variant="table" rows={5} />;

  return (
    <section className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_20px_-8px_oklch(0_0_0_/_0.35)]">
      <div className="border-line-soft flex items-center justify-between gap-2 border-b px-5 py-3.5">
        <div className="flex items-center gap-2">
          <span className="font-display text-[13.5px] font-bold tracking-[-0.01em]">People</span>
          <span className="text-muted-foreground font-mono text-[10.5px]">
            {members.length} on {projectName}
          </span>
        </div>
        <Button size="sm" className="gap-1.5" onClick={() => setAddOpen(true)}>
          <UserPlus className="size-4" aria-hidden />
          Add to project
        </Button>
      </div>

      {members.length === 0 ? (
        <div className="p-6">
          <EmptyState
            icon={Users}
            title="No one is on this project yet"
            description="Add a person and give them the role they hold here — it is independent of their Business Unit role."
          />
        </div>
      ) : (
        <ul>
          {members.map((m) => (
            <ProjectMemberRow
              key={m.membershipId}
              member={m}
              roleOptions={roleOptions}
              busy={busy}
              onRoleChange={(roleName) =>
                roleM.mutate({ membershipId: m.membershipId, roleName })
              }
              onRemove={() => removeM.mutate(m.membershipId)}
            />
          ))}
        </ul>
      )}

      <OnboardPersonDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        roleOptions={roleOptions}
        title={`Add to ${projectName}`}
        description="Onboards a new or existing person and gives them a role on this project only."
        onSubmit={async (input) => {
          const created = await addProjectMember(projectId, {
            email: input.email,
            displayName: input.displayName,
            roleName: input.roleName,
          });
          toast.success(`${created.identity.displayName} added as ${labelFor(created.role)}`);
          invalidate();
        }}
      />
    </section>
  );
}

function ProjectMemberRow({
  member,
  roleOptions,
  busy,
  onRoleChange,
  onRemove,
}: {
  member: ProjectMember;
  roleOptions: { value: string; label: string }[];
  busy: boolean;
  onRoleChange: (roleName: string) => void;
  onRemove: () => void;
}) {
  const governance =
    member.role in ROLE_META &&
    ROLE_META[member.role as keyof typeof ROLE_META].tier === "governance";

  return (
    <li className="border-line-soft hover:bg-surface-1 flex items-center gap-4 border-b px-5 py-3 last:border-b-0">
      <Avatar className="size-9 shrink-0">
        <AvatarFallback className="bg-secondary font-mono text-[11px] font-semibold">
          {member.identity.initials}
        </AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold">{member.identity.displayName}</div>
        <div className="text-muted-foreground truncate font-mono text-[11px]">
          {member.identity.email ?? member.identity.ssoSubject}
          {member.status !== "active" && (
            <span className="text-muted-foreground/70"> · {member.status}</span>
          )}
        </div>
      </div>

      <Select value={member.role} onValueChange={onRoleChange} disabled={busy}>
        <SelectTrigger
          className={cn("border-line-soft h-9 w-52 shrink-0", governance && "border-brand-bright/40")}
          aria-label={`Role for ${member.identity.displayName} on this project`}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {roleOptions.map((r) => (
            <SelectItem key={r.value} value={r.value}>
              {r.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-destructive shrink-0"
        disabled={busy}
        onClick={onRemove}
        aria-label={`Remove ${member.identity.displayName} from this project`}
      >
        <Trash2 className="size-4" aria-hidden />
      </Button>
    </li>
  );
}
