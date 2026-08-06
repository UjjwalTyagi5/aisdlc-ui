"use client";

import * as React from "react";
import { toast } from "sonner";
import { ArrowLeft, ArrowRight, Building2, Check, Loader2, ShieldCheck, UserPlus, Users } from "lucide-react";

import { cn } from "@/lib/utils";
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
import { onboardPerson } from "@/lib/api/onboarding";
import { ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/** The two org-level appointments, with the sentence that decides between
 *  them. Written as a choice about the PERSON, not about the platform's role
 *  taxonomy — "do they run a unit, or work in one" is the only thing the
 *  Organization Admin actually knows about a new joiner. */
const CHOICES = [
  {
    role: "bu_admin" as const,
    icon: ShieldCheck,
    title: ROLE_META.bu_admin.label,
    blurb: `Runs a ${BUSINESS_UNIT_LABEL.toLowerCase()}: its budget, its connections, its people, and who does what inside it.`,
    unit: "optional" as const,
    unitHint: `Leave this blank to appoint them now and decide their ${BUSINESS_UNIT_LABEL.toLowerCase()} later.`,
  },
  {
    role: "contributor" as const,
    icon: Users,
    title: ROLE_META.contributor.label,
    blurb: `Works inside a ${BUSINESS_UNIT_LABEL.toLowerCase()}. Its admin decides what they do there — you are not asked to guess.`,
    unit: "required" as const,
    unitHint: `Its admin is notified that someone is waiting on them. Until they assign a role, this person can sign in and do nothing.`,
  },
];

/** Radix reserves "" as a Select's placeholder value, so "no unit yet" needs a
 *  sentinel of its own rather than the empty string it maps to. */
const NO_UNIT = "__none__";

type OrgRoleChoice = (typeof CHOICES)[number]["role"];

export interface OnboardUserDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  businessUnits: readonly { id: string; displayName: string }[];
  onOnboarded: () => void;
}

/**
 * Onboarding, in two steps: who they are, then where they sit.
 *
 * WHAT THIS REPLACED. One form with four fields, whose role dropdown listed all
 * eleven working roles — Developer, BA, Architect, QA, DevOps, Security, Data
 * Engineer, Scrum Master, Project Admin. That asked the Organization Admin a
 * question they cannot answer: whether a new joiner is a QA or a Developer is a
 * fact about a team they do not run, for every hire in the organisation. So it
 * was answered by guessing, and the guess became the person's permissions.
 *
 * Two steps, and the second one has two answers. Everything else is delegated
 * to the person who knows — see `AssignBusinessUnitRoleDialog`.
 */
export function OnboardUserDialog({
  open,
  onOpenChange,
  businessUnits,
  onOnboarded,
}: OnboardUserDialogProps) {
  const [step, setStep] = React.useState<1 | 2>(1);
  const [email, setEmail] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [role, setRole] = React.useState<OrgRoleChoice>("contributor");
  const [unitId, setUnitId] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setStep(1);
    setEmail("");
    setDisplayName("");
    setRole("contributor");
    setUnitId("");
    setError(null);
  }, [open]);

  const choice = CHOICES.find((c) => c.role === role)!;
  const emailValid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());
  const unitSatisfied = choice.unit === "optional" || unitId !== "";

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const result = await onboardPerson({
        email: email.trim(),
        displayName: displayName.trim() || undefined,
        // A Business Unit Admin appointed without one sends no unit at all,
        // rather than an empty string the server would have to interpret.
        workspaceId: unitId || undefined,
        role,
      });

      // Say what happened NEXT, not just that it worked. For a Contributor the
      // onboarding is half the transaction — the half that matters to them is
      // the unit admin acting on it, and an admin who thinks they finished
      // granting access has been misled by a bare "invited".
      if (role === "contributor") {
        toast.success(`${result.displayName} added to ${unitName(unitId)}`, {
          description: result.notifiedBusinessUnitAdmin
            ? `Its admin has been asked to give them a role. They hold nothing until then.`
            : `That ${BUSINESS_UNIT_LABEL.toLowerCase()} has no admin yet, so nobody has been notified — appoint one, or assign the role yourself.`,
        });
      } else {
        toast.success(`${result.displayName} appointed ${ROLE_META.bu_admin.label}`, {
          description: unitId
            ? `They run ${unitName(unitId)}.`
            : `No ${BUSINESS_UNIT_LABEL.toLowerCase()} yet — assign one when you know which.`,
        });
      }
      onOpenChange(false);
      onOnboarded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't onboard this person");
    } finally {
      setSubmitting(false);
    }
  }

  function unitName(id: string) {
    return businessUnits.find((u) => u.id === id)?.displayName ?? id;
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display text-lg font-bold tracking-tight">
            Onboard someone
          </DialogTitle>
          <DialogDescription className="text-[13px]">
            {step === 1
              ? "Who they are. A new email is created automatically — there is no separate invite step."
              : `What they are here to do. Everything more specific is the ${BUSINESS_UNIT_LABEL.toLowerCase()}'s to decide.`}
          </DialogDescription>
        </DialogHeader>

        {/* Two dots, not a progress bar. The whole point of the redesign is
            that this is short; a bar implies there is more coming. */}
        <div className="flex items-center gap-2" aria-hidden>
          {[1, 2].map((n) => (
            <span
              key={n}
              className={cn(
                "h-1 flex-1 rounded-full transition-colors",
                n <= step ? "bg-brand-bright" : "bg-surface-2",
              )}
            />
          ))}
          <span className="text-muted-foreground ml-1 font-mono text-[10.5px]">{step} of 2</span>
        </div>

        {step === 1 ? (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="onboard-email">Email</Label>
              <Input
                id="onboard-email"
                autoFocus
                type="email"
                autoComplete="off"
                placeholder="name@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && emailValid) setStep(2);
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="onboard-name">
                Name{" "}
                <span className="text-muted-foreground/60 font-normal normal-case">(optional)</span>
              </Label>
              <Input
                id="onboard-name"
                autoComplete="off"
                placeholder="Jane Doe"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
              <p className="text-muted-foreground text-[11.5px]">
                Taken from the email if you leave it blank, and replaced by their directory name
                when they first sign in.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              {CHOICES.map((c) => {
                const active = c.role === role;
                const Icon = c.icon;
                return (
                  <button
                    key={c.role}
                    type="button"
                    onClick={() => {
                      setRole(c.role);
                      setError(null);
                    }}
                    aria-pressed={active}
                    className={cn(
                      "flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors",
                      "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                      active
                        ? "border-brand-bright/45 bg-brand-bright/8"
                        : "border-line-soft bg-surface-1 hover:border-line",
                    )}
                  >
                    <Icon
                      className={cn(
                        "mt-0.5 size-4 shrink-0",
                        active ? "text-brand-bright" : "text-muted-foreground",
                      )}
                      aria-hidden
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2 text-[13px] font-medium">
                        {c.title}
                        {active && <Check className="text-brand-bright size-3.5" aria-hidden />}
                      </span>
                      <span className="text-muted-foreground mt-0.5 block text-[11.5px]">
                        {c.blurb}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="onboard-unit">
                {BUSINESS_UNIT_LABEL}{" "}
                <span className="text-muted-foreground/60 font-normal normal-case">
                  ({choice.unit})
                </span>
              </Label>
              <Select
                value={unitId}
                onValueChange={(v) => {
                  // The "no unit yet" option round-trips through an empty
                  // string; Radix reserves "" as the placeholder value, so the
                  // item carries a sentinel instead.
                  setUnitId(v === NO_UNIT ? "" : v);
                  setError(null);
                }}
              >
                <SelectTrigger id="onboard-unit">
                  <SelectValue placeholder={`Select a ${BUSINESS_UNIT_LABEL.toLowerCase()}…`} />
                </SelectTrigger>
                <SelectContent>
                  {choice.unit === "optional" && <SelectItem value={NO_UNIT}>Not yet</SelectItem>}
                  {businessUnits.map((u) => (
                    <SelectItem key={u.id} value={u.id}>
                      {u.displayName}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground flex items-start gap-1.5 text-[11.5px]">
                <Building2 className="mt-0.5 size-3 shrink-0" aria-hidden />
                <span>{choice.unitHint}</span>
              </p>
            </div>

            {error && <p className="text-destructive text-[12px]">{error}</p>}
          </div>
        )}

        <DialogFooter className="gap-2 sm:justify-between">
          {step === 1 ? (
            <>
              <Button
                variant="outline"
                onClick={() => onOpenChange(false)}
                className="border-line-soft"
              >
                Cancel
              </Button>
              <Button disabled={!emailValid} onClick={() => setStep(2)}>
                Continue
                <ArrowRight className="size-4" aria-hidden />
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                onClick={() => setStep(1)}
                disabled={submitting}
                className="border-line-soft"
              >
                <ArrowLeft className="size-4" aria-hidden />
                Back
              </Button>
              <Button
                disabled={!unitSatisfied || submitting}
                aria-busy={submitting}
                onClick={submit}
              >
                {submitting ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <UserPlus className="size-4" aria-hidden />
                )}
                Onboard
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
