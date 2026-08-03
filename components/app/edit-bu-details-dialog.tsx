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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { updateWorkspace } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { Workspace } from "@/lib/schemas/workspace";

/**
 * Edit a Business Unit's record after it exists.
 *
 * A unit gets renamed, re-labelled, or moves cost centre. Until this dialog
 * those three fields were write-once: the creation form set them and nothing
 * could touch them afterwards.
 *
 * Budget, active/inactive and who administers the unit are deliberately NOT
 * here — each has its own rule (a cascade, an Org-Admin-only flag, an
 * appointment) and its own surface. This dialog is only the unit's description
 * of itself.
 *
 * Gated on `workspace:manage`, matching what the API already allows
 * (`canManageBusinessUnit` in app/api/workspaces/[id]/route.ts): the Org Admin,
 * and the unit's own Admin maintaining their own record.
 */
export function EditBuDetailsDialog({
  workspace: ws,
  open,
  onOpenChange,
}: {
  workspace: Workspace;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = React.useState(ws.displayName);
  const [businessUnit, setBusinessUnit] = React.useState(ws.businessUnit ?? "");
  const [costCenter, setCostCenter] = React.useState(ws.costCenter ?? "");

  // Re-seed whenever it opens, so a cancelled edit doesn't persist as the
  // starting point of the next one.
  React.useEffect(() => {
    if (open) {
      setDisplayName(ws.displayName);
      setBusinessUnit(ws.businessUnit ?? "");
      setCostCenter(ws.costCenter ?? "");
    }
  }, [open, ws.displayName, ws.businessUnit, ws.costCenter]);

  const mutation = useMutation({
    mutationFn: () =>
      updateWorkspace(String(ws.id), {
        displayName: displayName.trim(),
        // Empty string clears the field rather than storing "" — these are
        // nullable on the contract and "unset" is a real state for both.
        businessUnit: businessUnit.trim() || null,
        costCenter: costCenter.trim() || null,
      }),
    onSuccess: (updated) => {
      toast.success(`${updated.displayName} updated`);
      queryClient.invalidateQueries({ queryKey: qk.workspaces.all() });
      queryClient.invalidateQueries({ queryKey: qk.workspaces.detail(String(ws.id)) });
      onOpenChange(false);
    },
    onError: (e) =>
      toast.error("Couldn't save changes", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  const nameValid = displayName.trim().length >= 2;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated max-w-lg">
        <DialogHeader>
          <div className="text-brand-bright mb-1 font-mono text-[11px] tracking-widest uppercase">
            {BUSINESS_UNIT_LABEL}
          </div>
          <DialogTitle className="font-display text-xl font-bold tracking-tight">
            Edit details
          </DialogTitle>
          <DialogDescription className="text-[13px]">
            Budget, active status and who administers this{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} are changed from their own controls.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (nameValid) mutation.mutate();
          }}
        >
          <div className="space-y-1.5">
            <Label
              htmlFor="bu-name"
              className="text-muted-foreground font-mono text-xs tracking-wider uppercase"
            >
              {BUSINESS_UNIT_LABEL} name
            </Label>
            <Input
              id="bu-name"
              autoFocus
              autoComplete="off"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="border-line-soft bg-surface-1 font-display"
            />
            {!nameValid && (
              <p className="text-destructive font-mono text-[10.5px]">
                Name must be at least 2 characters
              </p>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label
                htmlFor="bu-unit"
                className="text-muted-foreground font-mono text-xs tracking-wider uppercase"
              >
                Business unit <span className="normal-case opacity-60">(optional)</span>
              </Label>
              <Input
                id="bu-unit"
                autoComplete="off"
                value={businessUnit}
                onChange={(e) => setBusinessUnit(e.target.value)}
                className="border-line-soft bg-surface-1"
              />
            </div>
            <div className="space-y-1.5">
              <Label
                htmlFor="bu-cc"
                className="text-muted-foreground font-mono text-xs tracking-wider uppercase"
              >
                Cost center <span className="normal-case opacity-60">(optional)</span>
              </Label>
              <Input
                id="bu-cc"
                autoComplete="off"
                value={costCenter}
                onChange={(e) => setCostCenter(e.target.value)}
                className="border-line-soft bg-surface-1 font-mono"
              />
            </div>
          </div>


          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="border-line-soft"
              disabled={mutation.isPending}
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!nameValid || mutation.isPending}
              aria-busy={mutation.isPending}
              className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white"
            >
              {mutation.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
