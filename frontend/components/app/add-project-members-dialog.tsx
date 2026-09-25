"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Trash2, UserPlus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RoleAgentPreview } from "@/components/app/role-agent-preview";
import { addProjectMember, updateProjectMemberAgents } from "@/lib/api/project-members";
import { listWorkspaceMembers } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { PHASE_LABEL } from "@/lib/agents";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { DeliveryTrack, Phase, ProjectId } from "@/lib/schemas";
import { resolveRoleLabel, useAllCustomRoles, type AssignableRole } from "@/hooks/use-assignable-roles";

/** One person staged for this project, exactly as the create-project dialog stages them. */
interface StagedMember {
  email: string;
  displayName?: string;
  roleName: string;
  extraAgents: Phase[];
}

export interface AddProjectMembersDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: ProjectId;
  /** Decides which agents a role reaches — the roster differs per track. */
  track: DeliveryTrack;
  /** The project's Business Unit, whose roster fills the picker. */
  workspaceId?: string | null;
  roleOptions: readonly AssignableRole[];
  /** Already on the project: offered neither in the picker nor by email. */
  existingEmails: readonly string[];
  onAdded: () => void;
}

/**
 * Add people to an EXISTING project, the same way they are added while it is
 * being created.
 *
 * WHY IT IS NOT THE PLAIN ONBOARD DIALOG. This page used
 * `OnboardPersonDialog` — email, name, role, done. That form is right for org
 * and Business Unit scope, and wrong here for two reasons a Project Admin
 * meets immediately: the people they want are almost always already in the
 * unit (so typing their address is both work and a chance to mistype it), and
 * "BA" does not say which agents the person will be able to open. Creating a
 * project answers both — pick from the roster, see the role's agents, grant
 * extras in the same breath — and there was no reason for the answer to
 * disappear once the project existed.
 *
 * Several people are staged and saved together, as at creation. Each one is a
 * separate write, so a failure is reported per person and only the ones that
 * failed stay staged — a half-finished save never looks like a whole one.
 */
export function AddProjectMembersDialog({
  open,
  onOpenChange,
  projectId,
  track,
  workspaceId,
  roleOptions,
  existingEmails,
  onAdded,
}: AddProjectMembersDialogProps) {
  const allCustomRoles = useAllCustomRoles();
  const [staged, setStaged] = React.useState<StagedMember[]>([]);
  const [roleName, setRoleName] = React.useState(roleOptions[0]?.value ?? "");
  const [extraAgents, setExtraAgents] = React.useState<Phase[]>([]);
  const [email, setEmail] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setStaged([]);
      setRoleName(roleOptions[0]?.value ?? "");
      setExtraAgents([]);
      setEmail("");
    }
  }, [open, roleOptions]);

  const rosterQ = useQuery({
    queryKey: qk.workspaces.members(workspaceId ?? ""),
    queryFn: () => listWorkspaceMembers(workspaceId ?? ""),
    enabled: open && !!workspaceId,
  });

  const taken = React.useMemo(() => {
    const set = new Set(existingEmails.map((e) => e.toLowerCase()));
    for (const s of staged) set.add(s.email.toLowerCase());
    return set;
  }, [existingEmails, staged]);

  // A roster row without an email cannot be staged — every write here is keyed by
  // address — so those are dropped rather than offered and refused on save.
  const pickable = (rosterQ.data ?? []).flatMap((m) =>
    m.email && !taken.has(m.email.toLowerCase())
      ? [{ userId: m.userId, email: m.email, displayName: m.displayName }]
      : [],
  );

  const stage = (address: string, displayName?: string) => {
    const value = address.trim();
    if (!value) return;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) {
      toast.error("That doesn't look like an email address");
      return;
    }
    if (taken.has(value.toLowerCase())) {
      toast.error("Already on this project", { description: value });
      return;
    }
    setStaged((prev) => [...prev, { email: value, displayName, roleName, extraAgents }]);
    setEmail("");
    // Extras belong to the person just staged, not to the next one: leaving them
    // ticked silently grants the same extra access to everybody added after.
    setExtraAgents([]);
  };

  const save = async () => {
    if (staged.length === 0) return;
    setSaving(true);
    const failed: { row: StagedMember; reason: string }[] = [];
    let added = 0;
    for (const row of staged) {
      try {
        const member = await addProjectMember(projectId, {
          email: row.email,
          displayName: row.displayName,
          roleName: row.roleName,
        });
        if (row.extraAgents.length > 0) {
          await updateProjectMemberAgents(projectId, member.membershipId, row.extraAgents);
        }
        added += 1;
      } catch (err) {
        failed.push({ row, reason: err instanceof Error ? err.message : "unknown error" });
      }
    }
    setSaving(false);
    onAdded();

    if (added > 0) {
      toast.success(`${added} ${added === 1 ? "person" : "people"} added to this project`);
    }
    if (failed.length === 0) {
      onOpenChange(false);
      return;
    }
    // Only the failures stay, so pressing Save again retries exactly them.
    setStaged(failed.map((f) => f.row));
    toast.error(`${failed.length} could not be added`, {
      description: failed.map((f) => `${f.row.email}: ${f.reason}`).join("; "),
    });
  };

  const pickerPlaceholder = !workspaceId
    ? `This project has no ${BUSINESS_UNIT_LABEL.toLowerCase()} roster`
    : rosterQ.isLoading
      ? "Loading members…"
      : pickable.length === 0
        ? `No more ${BUSINESS_UNIT_LABEL.toLowerCase()} members to add`
        : `Add an existing ${BUSINESS_UNIT_LABEL.toLowerCase()} member…`;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add project members</DialogTitle>
          <DialogDescription>
            Each person gets their role&apos;s agent access on this project. Grant extra
            agents here if the role&apos;s own roster is not enough.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {staged.length > 0 && (
            <ul className="flex flex-col gap-1.5">
              {staged.map((s) => (
                <li
                  key={s.email}
                  className="border-line-soft bg-panel-elevated flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate text-[12.5px]">
                    {s.email}
                    {s.extraAgents.length > 0 && (
                      <span className="text-brand-bright ml-1.5 font-mono text-[10.5px]">
                        +{s.extraAgents.map((p) => PHASE_LABEL[p]).join(", ")}
                      </span>
                    )}
                  </span>
                  <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
                    {resolveRoleLabel(s.roleName, allCustomRoles)}
                  </Badge>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-6 shrink-0"
                    aria-label={`Remove ${s.email}`}
                    onClick={() => setStaged((prev) => prev.filter((x) => x.email !== s.email))}
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <div className="flex items-center gap-2">
            <Select
              value=""
              onValueChange={(picked) => {
                const member = pickable.find((m) => m.email === picked);
                stage(picked, member?.displayName ?? undefined);
              }}
              disabled={!workspaceId || rosterQ.isLoading || pickable.length === 0}
            >
              <SelectTrigger
                aria-label={`Add an existing ${BUSINESS_UNIT_LABEL.toLowerCase()} member`}
                className="border-line-soft bg-panel-elevated flex-1"
              >
                <SelectValue placeholder={pickerPlaceholder} />
              </SelectTrigger>
              <SelectContent>
                {pickable.map((m) => (
                  <SelectItem key={m.userId} value={m.email}>
                    {m.displayName ?? m.email}{" "}
                    <span className="text-muted-foreground">· {m.email}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={roleName} onValueChange={setRoleName}>
              <SelectTrigger
                aria-label="Role"
                className="border-line-soft bg-panel-elevated w-44 shrink-0"
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
          </div>

          <RoleAgentPreview
            roleName={roleName}
            track={track}
            extra={extraAgents}
            onToggleExtra={(phase) =>
              setExtraAgents((prev) =>
                prev.includes(phase) ? prev.filter((p) => p !== phase) : [...prev, phase],
              )
            }
          />

          <div className="flex items-center gap-2">
            <Input
              placeholder="Or invite someone new by email — name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  stage(email);
                }
              }}
              className="border-line-soft bg-panel-elevated text-sm"
            />
            <Button type="button" variant="outline" onClick={() => stage(email)}>
              Add
            </Button>
          </div>
          <p className="text-muted-foreground text-[11px]">
            Pick someone already in this {BUSINESS_UNIT_LABEL.toLowerCase()}, or invite a new
            email — new people are onboarded automatically, no separate invite step.
          </p>
        </div>

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={save} disabled={staged.length === 0 || saving}>
            {saving ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <UserPlus className="size-4" aria-hidden />
            )}
            {staged.length === 0
              ? "Save"
              : `Save ${staged.length} ${staged.length === 1 ? "member" : "members"}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
