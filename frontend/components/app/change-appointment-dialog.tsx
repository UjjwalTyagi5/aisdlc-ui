"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import { changeOrgAppointment } from "@/lib/api/users";
import { qk } from "@/lib/api/query-keys";
import { ORG_ASSIGNABLE_ROLES, ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/** Radix reserves "" for the placeholder, so "no unit" needs a sentinel. */
const NO_UNIT = "__none__";

export interface ChangeAppointmentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId: string;
  displayName: string;
  currentRole: string;
  currentBusinessUnitId: string | null;
  businessUnits: readonly { id: string; displayName: string }[];
}

/**
 * The Organization Admin's only lever over an existing person: which of the two
 * appointments they hold, and which Business Unit they sit in.
 *
 * It offers the same two roles as onboarding and no others — the restriction
 * would be theatre if it applied to the first decision and not to every one
 * after it. What someone DOES inside a unit stays with that unit's admin, which
 * is why this dialog cannot set it and does not clear it: promoting an
 * unassigned person to Contributor in a unit where they already have a job
 * leaves that job alone.
 */
export function ChangeAppointmentDialog({
  open,
  onOpenChange,
  userId,
  displayName,
  currentRole,
  currentBusinessUnitId,
  businessUnits,
}: ChangeAppointmentDialogProps) {
  const queryClient = useQueryClient();
  const [role, setRole] = React.useState(currentRole);
  const [unitId, setUnitId] = React.useState(currentBusinessUnitId ?? "");

  React.useEffect(() => {
    if (!open) return;
    setRole(ORG_ASSIGNABLE_ROLES.includes(currentRole as never) ? currentRole : "contributor");
    setUnitId(currentBusinessUnitId ?? "");
  }, [open, currentRole, currentBusinessUnitId]);

  const unitRequired = role === "contributor";
  const save = useMutation({
    mutationFn: () => changeOrgAppointment(userId, { role, workspaceId: unitId || null }),
    onSuccess: () => {
      toast.success(`${displayName} is now ${ROLE_META[role as "bu_admin"].label}`);
      queryClient.invalidateQueries({ queryKey: qk.users.directory() });
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      onOpenChange(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated sm:max-w-md">
        {/* Deliberately the same shape as the Business Unit Admin's dialog —
            "Role for X", one picker, one note about who decides the rest. The
            two answer different halves of the same question, and making them
            look like different features made the halves hard to see. */}
        <DialogHeader>
          <DialogTitle>Role for {displayName}</DialogTitle>
          <DialogDescription className="text-[13px]">
            Whether they run a {BUSINESS_UNIT_LABEL.toLowerCase()} or work in one. What they do
            inside it is that unit&apos;s admin to decide.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="appointment-role">Role</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger id="appointment-role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ORG_ASSIGNABLE_ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {ROLE_META[r].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="appointment-unit">
              {BUSINESS_UNIT_LABEL}{" "}
              <span className="text-muted-foreground/60 font-normal normal-case">
                ({unitRequired ? "required" : "optional"})
              </span>
            </Label>
            <Select
              value={unitId}
              onValueChange={(v) => setUnitId(v === NO_UNIT ? "" : v)}
            >
              <SelectTrigger id="appointment-unit">
                <SelectValue placeholder={`Select a ${BUSINESS_UNIT_LABEL.toLowerCase()}…`} />
              </SelectTrigger>
              <SelectContent>
                {!unitRequired && <SelectItem value={NO_UNIT}>Not yet</SelectItem>}
                {businessUnits.map((u) => (
                  <SelectItem key={u.id} value={u.id}>
                    {u.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {role === "bu_admin" && unitId && unitId !== currentBusinessUnitId && (
              <p className="text-muted-foreground text-[11.5px]">
                Whoever runs that {BUSINESS_UNIT_LABEL.toLowerCase()} today is replaced — a unit
                has one admin.
              </p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={save.isPending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            disabled={(unitRequired && !unitId) || save.isPending}
            aria-busy={save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
