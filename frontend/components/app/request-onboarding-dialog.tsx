"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Send } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import { createRequest } from "@/lib/api/governance-approvals";
import { qk } from "@/lib/api/query-keys";
import { initialApproverRole } from "@/lib/requests/routing";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/**
 * "Onboard someone", for everyone who is not the Organization Admin.
 *
 * `POST /onboarding` — the real account-creating action — is Org-Admin-only
 * (shared/routers/onboarding.py's own docstring: "the Organization Admin's
 * half of the handover"), and until this dialog existed the only way anyone
 * else could ask for it was the generic "Raise a request" form, which has no
 * email field at all — `user_onboarding`'s payload carries one
 * (`onboardEmail`), but nothing in that form ever collected it. A Business
 * Unit Admin or Project Admin who needed a new person on the platform had no
 * way to name who.
 *
 * Small on purpose, same discipline as `RequestAgentAccessDialog`: one real
 * field — the email — plus the business unit they'd land in and why. Every
 * other choice `POST /onboarding` itself makes (Business Unit Admin vs.
 * Contributor) stays the Organization Admin's alone; this always asks for a
 * Contributor, because appointing a co-admin for someone else is not this
 * requester's call to make on their behalf.
 *
 * RENDERS NOTHING for a role that may not raise `user_onboarding` — the
 * Organization Admin foremost, who onboards directly from the button next to
 * this one, but also anyone below the tiers `raisableTypesFor` names it for.
 */
export function RequestOnboardingDialog({
  open,
  onOpenChange,
  requesterRole,
  onRaised,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  requesterRole: PlatformRole | null;
  onRaised?: () => void;
}) {
  const queryClient = useQueryClient();
  const { units } = useScopedBusinessUnits();

  const [email, setEmail] = React.useState("");
  const [workspaceId, setWorkspaceId] = React.useState("");
  const [justification, setJustification] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  // Reset on open — mirrors RaiseRequestDialog's own "seed on open, not on
  // mount" rule, and preselects the one unit a single-unit admin has.
  React.useEffect(() => {
    if (!open) return;
    setEmail("");
    setJustification("");
    setError(null);
    setWorkspaceId(units.length === 1 ? (units[0]?.id ?? "") : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const emailValid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());
  const canSubmit = emailValid && workspaceId !== "" && justification.trim().length >= 10;
  const approver = initialApproverRole("user_onboarding", requesterRole);

  const raise = useMutation({
    mutationFn: () =>
      createRequest({
        type: "user_onboarding",
        title: `Onboard ${email.trim()}`,
        description: justification.trim(),
        priority: "normal",
        workspaceId,
        attachments: [],
        onboardEmail: email.trim().toLowerCase(),
      }),
    onSuccess: () => {
      toast.success("Onboarding request sent", {
        description: approver
          ? `Waiting on the ${ROLE_META[approver].label} — only they can create the account.`
          : "Waiting on approval.",
      });
      queryClient.invalidateQueries({ queryKey: qk.governanceApprovals.list() });
      onOpenChange(false);
      onRaised?.();
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Request onboarding</DialogTitle>
          <DialogDescription>
            Ask for someone new to be admitted to the organisation. Only an Organization
            Admin can create the account — this asks them to.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="onboard-req-email">Email</Label>
            <Input
              id="onboard-req-email"
              autoFocus
              type="email"
              autoComplete="off"
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="onboard-req-unit">{BUSINESS_UNIT_LABEL}</Label>
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger id="onboard-req-unit">
                <SelectValue placeholder={`Choose a ${BUSINESS_UNIT_LABEL.toLowerCase()}`} />
              </SelectTrigger>
              <SelectContent>
                {units.map((u) => (
                  <SelectItem key={u.id} value={u.id}>
                    {u.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-[11.5px]">
              They join as a Contributor here. Its admin gives them a role once they land.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="onboard-req-why">Why</Label>
            <Textarea
              id="onboard-req-why"
              rows={3}
              value={justification}
              placeholder="New QA joining this sprint — needs an account to get started."
              onChange={(e) => setJustification(e.target.value)}
            />
          </div>

          {approver && (
            <p className="text-muted-foreground text-[11.5px]">
              Goes to the {ROLE_META[approver].label}.
            </p>
          )}

          {error && (
            <p role="alert" className="text-destructive text-[12.5px]">
              {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => raise.mutate()} disabled={!canSubmit || raise.isPending}>
            {raise.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Send className="size-3.5" aria-hidden />
            )}
            Send request
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
