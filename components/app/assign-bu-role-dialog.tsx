"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, ChevronsUpDown, Loader2, ShieldCheck, Sparkles } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAssignableRolesForBusinessUnit } from "@/hooks/use-assignable-roles";
import { updateWorkspaceMemberRole } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { ROLE_META, scopeTierConflicts, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

export interface AssignBusinessUnitRoleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** SSO subject — what the member endpoints key off. */
  userId: string;
  displayName: string;
  businessUnitId: string;
  businessUnitName: string;
  /** What they hold today in THIS unit — "contributor" means nothing yet. */
  currentRole: string | null;
  /** Every (scope, role) they hold anywhere, for the two refusals below. */
  allBindings: readonly { scopeId: string; scopeName?: string; role: string }[];
  /** Fired after a successful assignment, for callers with their own lists to
   *  refresh (the approvals queue drops the row it just discharged). */
  onAssigned?: () => void;
}

/**
 * The Business Unit Admin's half of onboarding: turning a Contributor into
 * someone with a job.
 *
 * The Organization Admin can put a person in a unit and nothing more; this is
 * where the unit's own admin says what they do, which is the first moment the
 * person holds any permission at all.
 *
 * ONE PICKER, SEARCHABLE — no "compose a role" shortcut. The built-in roles and
 * this unit's own custom roles are the same kind of answer to the same
 * question, so they belong in one list rather than one list plus an escape
 * hatch; a role composed on Roles &amp; Access appears here the moment it is
 * saved. Search is what makes that scale: the list grows as a unit defines its
 * own roles, and a ten-item dropdown that only grows is the thing worth typing
 * into.
 */
export function AssignBusinessUnitRoleDialog({
  open,
  onOpenChange,
  userId,
  displayName,
  businessUnitId,
  businessUnitName,
  currentRole,
  allBindings,
  onAssigned,
}: AssignBusinessUnitRoleDialogProps) {
  const queryClient = useQueryClient();
  const [roleName, setRoleName] = React.useState("");
  const [pickerOpen, setPickerOpen] = React.useState(false);

  const roleOptions = useAssignableRolesForBusinessUnit(businessUnitId);
  const selected = roleOptions.find((r) => r.value === roleName) ?? null;

  React.useEffect(() => {
    if (open) setRoleName(currentRole && currentRole !== "contributor" ? currentRole : "");
  }, [open, currentRole]);

  const assign = useMutation({
    mutationFn: () =>
      updateWorkspaceMemberRole(businessUnitId, userId, {
        roleName,
        roleLabel: selected?.label,
      }),
    onSuccess: () => {
      toast.success(`${displayName} is now ${selected?.label ?? roleName} in ${businessUnitName}`);
      queryClient.invalidateQueries({ queryKey: qk.users.directory() });
      queryClient.invalidateQueries({ queryKey: qk.workspaces.members(businessUnitId) });
      queryClient.invalidateQueries({ queryKey: ["governance-approvals"] });
      queryClient.invalidateQueries({ queryKey: ["access"] });
      onAssigned?.();
      onOpenChange(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  /**
   * Separation of duties, asked of the unit as it WOULD be. Governance and
   * delivery in one scope is what lets someone approve their own work — and
   * the placeholder they are being promoted out of carries no tier, so it is
   * excluded rather than counted as a delivery role that blocks bu_admin.
   */
  const conflict = React.useMemo(() => {
    if (!roleName) return null;
    const held = allBindings
      .filter((b) => String(b.scopeId) === String(businessUnitId) && b.role !== "contributor")
      .map((b) => b.role);
    if (scopeTierConflicts([{ scopeId: businessUnitId, roles: [...held, roleName] }]).length === 0) {
      return null;
    }
    const newTier = ROLE_META[roleName as PlatformRole]?.tier;
    return held.find((r) => ROLE_META[r as PlatformRole]?.tier !== newTier) ?? null;
  }, [roleName, allBindings, businessUnitId]);

  // No "already runs another unit" refusal here: `bu_admin` is not on this
  // picker at all (see BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES), so the clash it
  // guards cannot be reached from this dialog. The rule still lives where the
  // appointment is actually made — the Organization Admin's dialog on Users.
  const blocked = Boolean(conflict);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Role for {displayName}</DialogTitle>
          <DialogDescription className="text-[13px]">
            In {businessUnitName}.{" "}
            {currentRole === "contributor"
              ? "They hold nothing here until you pick one."
              : "Changing this replaces what they hold in this unit."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="assign-role">Role</Label>
            <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
              <PopoverTrigger asChild>
                <Button
                  id="assign-role"
                  type="button"
                  variant="outline"
                  role="combobox"
                  aria-expanded={pickerOpen}
                  className="border-line-soft w-full justify-between font-normal"
                >
                  <span className={cn("flex items-center gap-2", !selected && "text-muted-foreground")}>
                    {selected?.isCustom && (
                      <Sparkles className="text-brand-bright size-3.5" aria-hidden />
                    )}
                    {selected?.label ?? "Select a role…"}
                  </span>
                  <ChevronsUpDown className="size-4 shrink-0 opacity-50" aria-hidden />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
                <Command>
                  <CommandInput placeholder="Search roles…" />
                  <CommandList>
                    <CommandEmpty>
                      No role matches. Compose one on Roles &amp; Access and it appears here.
                    </CommandEmpty>
                    <CommandGroup>
                      {roleOptions.map((r) => (
                        <CommandItem
                          key={r.value}
                          /* Searched by LABEL, not by the stored value — a
                             custom role's value is an opaque `role_3`, so
                             typing its name would match nothing. */
                          value={r.label}
                          onSelect={() => {
                            setRoleName(r.value);
                            setPickerOpen(false);
                          }}
                          className="items-start gap-2"
                        >
                          <Check
                            className={cn(
                              "mt-0.5 size-3.5 shrink-0",
                              r.value === roleName ? "opacity-100" : "opacity-0",
                            )}
                            aria-hidden
                          />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5 text-[13px]">
                              {r.label}
                              {r.isCustom && (
                                <Sparkles className="text-brand-bright size-3" aria-hidden />
                              )}
                            </span>
                            {r.description && (
                              <span className="text-muted-foreground block text-[11px]">
                                {r.description}
                              </span>
                            )}
                          </span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          </div>

          {conflict && (
            <p className="text-destructive text-[11.5px]">
              They already hold {ROLE_META[conflict as PlatformRole]?.label ?? conflict} in{" "}
              {businessUnitName}. Governance and delivery in one {BUSINESS_UNIT_LABEL.toLowerCase()}{" "}
              would let them approve their own work.
            </p>
          )}
          <p
            className={cn(
              "text-muted-foreground flex items-start gap-1.5 text-[11.5px]",
              blocked && "opacity-60",
            )}
          >
            <ShieldCheck className="mt-0.5 size-3 shrink-0" aria-hidden />
            <span>Every grant is written to the audit trail.</span>
          </p>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={assign.isPending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            disabled={!roleName || blocked || assign.isPending}
            aria-busy={assign.isPending}
            onClick={() => assign.mutate()}
          >
            {assign.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Assign role
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
