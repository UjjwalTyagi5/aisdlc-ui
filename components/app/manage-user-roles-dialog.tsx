"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, ShieldCheck, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { assignAccessRole, listAccessWorkspaces, revokeAccessRole } from "@/lib/api/access";
import { qk } from "@/lib/api/query-keys";
import { useAssignableRoles } from "@/hooks/use-assignable-roles";
import { ROLE_META, scopeTierConflicts, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

export interface UserRoleBinding {
  /** Scope id — a Business Unit or project. */
  scopeId: string;
  scopeName: string;
  role: string;
}

/**
 * Give one person a role, or take one away.
 *
 * WHY THIS LIVES ON USERS. A role is a binding of (person, scope, role), and
 * you arrive at it thinking about the PERSON — "Priya joined Payments, what
 * should she hold?" The old Assignments screen inverted that: you picked a
 * scope first, then hunted the person inside it, and the answer to "what does
 * Priya hold across the organisation" was on a different page entirely. This
 * screen already lists every person with every binding they have; assigning
 * belongs next to it.
 *
 * Roles & Access keeps what it is actually for — defining what a role MEANS,
 * built-in or custom. Defining and assigning are different jobs.
 */
export function ManageUserRolesDialog({
  open,
  onOpenChange,
  userId,
  displayName,
  bindings,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId: string;
  displayName: string;
  /** Every (scope, role) this person already holds. */
  bindings: UserRoleBinding[];
}) {
  const queryClient = useQueryClient();
  const [workspaceId, setWorkspaceId] = React.useState<string>("");
  const [roleName, setRoleName] = React.useState<string>("");

  const workspacesQ = useQuery({
    queryKey: qk.access.workspaces(),
    queryFn: listAccessWorkspaces,
    enabled: open,
  });
  const roleOptions = useAssignableRoles("business_unit");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["users"] });
    queryClient.invalidateQueries({ queryKey: ["access"] });
  };

  const assign = useMutation({
    mutationFn: () => assignAccessRole({ userId, workspaceId, roleName }),
    onSuccess: () => {
      toast.success(`Granted ${ROLE_META[roleName as PlatformRole]?.label ?? roleName}`);
      setRoleName("");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const revoke = useMutation({
    mutationFn: (b: UserRoleBinding) =>
      revokeAccessRole({ userId, workspaceId: b.scopeId, roleName: b.role }),
    onSuccess: () => {
      toast.success("Role removed");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  /**
   * Separation of duties, checked before the grant rather than after.
   *
   * The conflict is WITHIN one scope — holding a governance and a delivery
   * role in the same Business Unit would let someone approve their own work.
   * Across scopes it is fine and common, so this only looks at the roles
   * already held in the unit being granted into.
   */
  const conflict = React.useMemo(() => {
    if (!workspaceId || !roleName) return null;
    const held = bindings.filter((b) => b.scopeId === workspaceId).map((b) => b.role);
    // Asked of the scope as it WOULD be, not as it is — the question is
    // whether granting this role creates the conflict.
    const clashing = scopeTierConflicts([{ scopeId: workspaceId, roles: [...held, roleName] }]);
    if (clashing.length === 0) return null;
    const newTier = ROLE_META[roleName as PlatformRole]?.tier;
    return held.find((r) => ROLE_META[r as PlatformRole]?.tier !== newTier) ?? null;
  }, [workspaceId, roleName, bindings]);

  const alreadyHeld = bindings.some((b) => b.scopeId === workspaceId && b.role === roleName);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Roles for {displayName}</DialogTitle>
          <DialogDescription>
            A role is held in a scope. The same person can govern one{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} and deliver on a project in another.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* What they hold now — revocable in place, so removing a role never
              means finding it on another screen. */}
          <div className="space-y-2">
            <Label className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
              Holds now
            </Label>
            {bindings.length === 0 ? (
              <p className="text-muted-foreground text-[12.5px]">No roles yet.</p>
            ) : (
              <ul className="divide-line-soft border-line-soft divide-y rounded-lg border">
                {bindings.map((b) => (
                  <li
                    key={`${b.scopeId}:${b.role}`}
                    className="flex items-center gap-2 px-3 py-2 text-[12.5px]"
                  >
                    <ShieldCheck className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">
                      {ROLE_META[b.role as PlatformRole]?.label ?? b.role}
                      <span className="text-muted-foreground"> in {b.scopeName}</span>
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(b)}
                      aria-label={`Remove ${ROLE_META[b.role as PlatformRole]?.label ?? b.role} in ${b.scopeName}`}
                      className="text-muted-foreground hover:text-destructive h-6 shrink-0 px-1.5 text-[11px]"
                    >
                      <X className="size-3" aria-hidden />
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="border-line-soft space-y-3 rounded-lg border border-dashed p-3">
            <Label className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
              Grant another
            </Label>

            <div className="grid gap-2 sm:grid-cols-2">
              <Select value={workspaceId} onValueChange={setWorkspaceId}>
                <SelectTrigger aria-label={`Choose a ${BUSINESS_UNIT_LABEL.toLowerCase()}`}>
                  <SelectValue placeholder={BUSINESS_UNIT_LABEL} />
                </SelectTrigger>
                <SelectContent>
                  {(workspacesQ.data ?? []).map((w) => (
                    <SelectItem key={w.id} value={w.id}>
                      {w.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={roleName} onValueChange={setRoleName}>
                <SelectTrigger aria-label="Choose a role">
                  <SelectValue placeholder="Role" />
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

            {conflict && (
              <p className="text-destructive text-[11.5px]">
                They already hold{" "}
                {ROLE_META[conflict as PlatformRole]?.label ?? conflict} here. Governance and
                delivery in one scope would let them approve their own work.
              </p>
            )}
            {alreadyHeld && !conflict && (
              <p className="text-muted-foreground text-[11.5px]">They already hold this here.</p>
            )}

            <Button
              size="sm"
              disabled={!workspaceId || !roleName || Boolean(conflict) || alreadyHeld || assign.isPending}
              onClick={() => assign.mutate()}
            >
              {assign.isPending && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
              Grant role
            </Button>
          </div>

          <p className="text-muted-foreground text-[11.5px]">
            <Badge variant="outline" className="mr-1 font-mono text-[9.5px]">
              audited
            </Badge>
            Every grant and revoke is written to the audit trail.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
