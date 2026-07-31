"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { MoreVertical, Trash2, UserPlus, Users } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { OnboardPersonDialog } from "@/components/app/onboard-person-dialog";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import {
  addProjectMember,
  listProjectMembers,
  removeProjectMember,
  updateProjectMemberRole,
} from "@/lib/api/project-members";
import { qk } from "@/lib/api/query-keys";
import { AGENT_OWNERSHIP, ROLE_META, type Involvement, type PlatformRole } from "@/lib/roles";
import { PHASE_LABEL } from "@/lib/agents";
import { agentsForTrack } from "@/lib/tracks";
import { getProject } from "@/lib/api/projects";
import type { ProjectId } from "@/lib/schemas";
import { resolveRoleLabel, useAllCustomRoles, useAssignableRoles } from "@/hooks/use-assignable-roles";

/**
 * Project Members — PRD §32.1 and §15.4.
 *
 * The Project Admin onboards people to the project and assigns their
 * project-scoped role (BA, Architect, Developer, QA/Tester, Security
 * Engineer, DevOps Engineer, Data Engineer, Scrum Master, or a custom set).
 * This is a binding of (person, THIS project, role) — `lib/mock/
 * project-membership-fixtures.ts` — distinct from Business Unit membership.
 *
 * Each member is shown with the agents their role actually drives, derived
 * from the ownership matrix (§14.7) — so "who covers Security on this
 * project?" is answerable at a glance. Agent access is never stored
 * separately: it's derived from this role binding at read time.
 */

const INVOLVEMENT_LABEL: Record<Involvement, string> = {
  owner: "owns",
  primary: "owns",
  build: "builds",
  requests: "requests",
  use: "uses",
  none: "—",
};

function asPlatformRole(name: string): PlatformRole | null {
  return name in ROLE_META ? (name as PlatformRole) : null;
}

export default function ProjectMembersPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const session = useSession({ required: true });
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = React.useState(false);

  const canManage = hasPermission(session, "member:manage");
  const contributorRoles = useAssignableRoles("project");
  const allCustomRoles = useAllCustomRoles();
  const roleLabel = React.useCallback(
    (name: string) => resolveRoleLabel(name, allCustomRoles),
    [allCustomRoles],
  );

  const projectQ = useQuery({
    queryKey: qk.projects.detail(id),
    queryFn: () => getProject(id),
  });

  const membersQ = useQuery({
    queryKey: qk.projectMembers.list(id),
    queryFn: () => listProjectMembers(id),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: qk.projectMembers.list(id) });

  const addMutation = useMutation({
    mutationFn: (input: { email: string; displayName?: string; roleName: string }) =>
      addProjectMember(id, input),
    onSuccess: (member) => {
      toast.success(
        member.status === "invited"
          ? `${member.identity.displayName} onboarded and invited`
          : `${member.identity.displayName} added to this project`,
      );
      invalidate();
    },
  });

  const removeMutation = useMutation({
    mutationFn: (membershipId: string) => removeProjectMember(id, membershipId),
    onSuccess: () => {
      toast.success("Removed from project");
      invalidate();
    },
    onError: (err) =>
      toast.error("Couldn't remove member", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const roleMutation = useMutation({
    mutationFn: ({ membershipId, roleName }: { membershipId: string; roleName: string }) =>
      updateProjectMemberRole(id, membershipId, roleName),
    onSuccess: (member) => {
      toast.success(`Role updated to ${roleLabel(member.role)}`);
      invalidate();
    },
    onError: (err) =>
      toast.error("Couldn't update role", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const members = membersQ.data ?? [];
  const roster = projectQ.data ? agentsForTrack(projectQ.data.track) : [];

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold tracking-tight">Members</h2>
          <p className="text-muted-foreground mt-1 max-w-2xl text-[13px]">
            Who works on this project and which agents their role drives. A role
            is a binding of person, scope and role — the same person can hold a
            different role on another project.
          </p>
        </div>

        {canManage && (
          <Button className="gap-2" onClick={() => setAddOpen(true)}>
            <UserPlus className="size-4" aria-hidden />
            Add member
          </Button>
        )}
      </div>

      {membersQ.isLoading || projectQ.isLoading ? (
        <LoadingState variant="list" rows={4} />
      ) : members.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No members yet"
          description="Add people to this project and assign the role they hold here."
          action={
            canManage ? (
              <Button onClick={() => setAddOpen(true)} className="gap-2">
                <UserPlus className="size-4" aria-hidden />
                Add member
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ul className="border-line-soft bg-panel-elevated divide-line-soft divide-y overflow-hidden rounded-xl border">
          {members.map((m) => {
            const role = asPlatformRole(m.role);
            const meta = role ? ROLE_META[role] : null;

            // Agents this member drives on THIS project's track (§14.7).
            const drives = role
              ? roster.filter((p) => {
                  const inv = AGENT_OWNERSHIP[role][p];
                  return inv === "owner" || inv === "primary" || inv === "build";
                })
              : [];

            return (
              <li key={m.membershipId} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span
                  className="bg-surface-2 text-foreground/80 flex size-8 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-medium"
                  aria-hidden
                >
                  {m.identity.initials}
                </span>

                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="block text-[13px] font-medium">
                      {m.identity.displayName}
                    </span>
                    {m.status === "invited" && (
                      <span className="border-line-soft text-muted-foreground rounded-full border px-1.5 py-px font-mono text-[9.5px] uppercase tracking-wider">
                        invited
                      </span>
                    )}
                  </span>
                  {m.identity.email && (
                    <span className="text-muted-foreground block truncate text-[11.5px]">
                      {m.identity.email}
                    </span>
                  )}
                </span>

                <span className="shrink-0 text-right">
                  <span
                    className={cn(
                      "block text-[12.5px] font-medium",
                      meta?.tier === "governance" && "text-brand-bright",
                    )}
                  >
                    {roleLabel(m.role)}
                  </span>
                  {drives.length > 0 ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="text-muted-foreground block cursor-help font-mono text-[10.5px]">
                          {INVOLVEMENT_LABEL[
                            AGENT_OWNERSHIP[role!][drives[0]!]
                          ]}{" "}
                          {drives.length} {drives.length === 1 ? "agent" : "agents"}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="left" className="max-w-[240px]">
                        {drives.map((p) => PHASE_LABEL[p]).join(", ")}
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    <span className="text-muted-foreground/60 block font-mono text-[10.5px]">
                      {meta?.governanceOnly ? "governance only" : "no agents"}
                    </span>
                  )}
                </span>

                {canManage && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="size-7" aria-label="Member actions">
                        <MoreVertical className="size-4" aria-hidden />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuSub>
                        <DropdownMenuSubTrigger>Change role</DropdownMenuSubTrigger>
                        <DropdownMenuPortal>
                          <DropdownMenuSubContent>
                            {contributorRoles.map((r) => (
                              <DropdownMenuItem
                                key={r.value}
                                disabled={r.value === m.role}
                                onSelect={() =>
                                  roleMutation.mutate({ membershipId: m.membershipId, roleName: r.value })
                                }
                              >
                                {r.label}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuSubContent>
                        </DropdownMenuPortal>
                      </DropdownMenuSub>
                      <DropdownMenuItem
                        onSelect={() => removeMutation.mutate(m.membershipId)}
                        className="text-destructive focus:text-destructive"
                      >
                        <Trash2 className="size-4" aria-hidden />
                        Remove from project
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {!canManage && (
        <p className="text-muted-foreground text-[12.5px]">
          Adding and removing members is the Project Admin&apos;s action. Role
          definitions live on{" "}
          <a href="/admin/access" className="text-brand-bright underline underline-offset-2">
            Roles &amp; Access
          </a>
          .
        </p>
      )}

      <OnboardPersonDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        roleOptions={contributorRoles}
        title="Add a project member"
        description="Assign a contributor role — they get that role's agent access on this project automatically."
        onSubmit={(input) => addMutation.mutateAsync(input)}
      />
    </div>
  );
}
