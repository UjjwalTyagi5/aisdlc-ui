"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, ShieldCheck } from "lucide-react";

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
import { changeBusinessUnitAdmin } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/**
 * Re-appoint who runs a Business Unit (PRD §15.2).
 *
 * One dialog shared by the Business Units list (the card's ⋯ menu) and the
 * unit's own detail page, because both are the same act with the same rules —
 * Org Admin only, the outgoing holder is demoted rather than removed, and an
 * unrecognized email is onboarded. Two copies of that would drift.
 *
 * Gating is the caller's job: this renders whatever it's given, and the API
 * rejects a non-Org-Admin regardless (app/api/workspaces/[id]/admin/route.ts).
 */
export function ChangeBuAdminDialog({
  workspaceId,
  workspaceName,
  currentAdminName,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  workspaceName: string;
  currentAdminName: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setEmail("");
      setName("");
    }
  }, [open]);

  const mutation = useMutation({
    mutationFn: () =>
      changeBusinessUnitAdmin(workspaceId, {
        email: email.trim(),
        displayName: name.trim() || undefined,
      }),
    onSuccess: (res) => {
      toast.success(`${res.admin.displayName} now runs ${workspaceName}`, {
        description: res.replacedDisplayName
          ? `${res.replacedDisplayName} was moved off the admin role but kept in the unit.`
          : undefined,
      });
      queryClient.invalidateQueries({ queryKey: qk.workspaces.all() });
      queryClient.invalidateQueries({ queryKey: qk.workspaces.detail(workspaceId) });
      queryClient.invalidateQueries({ queryKey: qk.workspaces.members(workspaceId) });
      onOpenChange(false);
    },
    onError: (e) =>
      toast.error("Couldn't change the admin", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!emailValid) return;
    mutation.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated max-w-md">
        <DialogHeader>
          <div className="text-brand-bright mb-1 flex items-center gap-1.5 font-mono text-[11px] tracking-widest uppercase">
            <ShieldCheck className="size-3.5" aria-hidden />
            {workspaceName}
          </div>
          <DialogTitle className="font-display text-xl font-bold tracking-tight">
            {currentAdminName ? "Change" : "Appoint"} {ROLE_META.bu_admin.label.toLowerCase()}
          </DialogTitle>
          <DialogDescription className="text-[13px]">
            {currentAdminName ? (
              <>
                <span className="text-foreground font-medium">{currentAdminName}</span> runs this{" "}
                {BUSINESS_UNIT_LABEL.toLowerCase()} today. Whoever you name below takes it over —{" "}
                {currentAdminName.split(" ")[0]} stays in the unit as a contributor.
              </>
            ) : (
              <>
                No one runs this {BUSINESS_UNIT_LABEL.toLowerCase()} yet. Its budget, connections,
                members and project creation all route to this role.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="bu-admin-email"
              className="text-muted-foreground font-mono text-xs tracking-wider uppercase"
            >
              Email
            </Label>
            <Input
              id="bu-admin-email"
              type="email"
              autoFocus
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@company.com"
              className="border-line-soft bg-surface-1"
            />
          </div>

          <div className="space-y-1.5">
            <Label
              htmlFor="bu-admin-name"
              className="text-muted-foreground font-mono text-xs tracking-wider uppercase"
            >
              Name <span className="normal-case opacity-60">(optional)</span>
            </Label>
            <Input
              id="bu-admin-name"
              autoComplete="off"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Jane Doe"
              className="border-line-soft bg-surface-1"
            />
          </div>

          <p className="text-muted-foreground text-[11px]">
            A new email is onboarded automatically — no separate invite step.
          </p>

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
              disabled={!emailValid || mutation.isPending}
              aria-busy={mutation.isPending}
              className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white"
            >
              {mutation.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Appoint
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
