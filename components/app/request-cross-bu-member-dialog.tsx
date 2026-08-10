"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Info, Loader2, Send } from "lucide-react";

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
import { requestCrossBuMember } from "@/lib/api/project-members";
import { CROSS_BU_ASSIGNABLE_ROLES, ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { ProjectId } from "@/lib/schemas";

export interface RequestCrossBuMemberDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fixed target — used when opened from a project, which already is one. */
  projectId?: ProjectId;
  projectName?: string;
  /**
   * Offer a project picker instead. Used by a Business Unit Admin, who opens
   * this from Users & Roles and is standing beside their whole unit rather than
   * inside one project — so which project is a question, not a given.
   */
  projects?: readonly { id: string; name: string }[];
  onRaised: () => void;
}

/**
 * Ask another Business Unit for one of its people.
 *
 * BY EMAIL, AND THAT IS THE DESIGN. There is no person picker because there is
 * no list to pick from: the people directory is scoped to your own unit, and
 * widening it so this dialog could offer a dropdown would hand every Project
 * Admin the whole organisation's roster — the exact disclosure the approval
 * step exists to gate. You already know who you want; typing their address
 * asserts nothing you did not already believe.
 *
 * What happens next is stated up front, because the gap between asking and
 * getting is the part people get wrong: nothing changes until the contributor's
 * OWN admin approves, and even then they reach this project and nothing else in
 * the unit.
 */
export function RequestCrossBuMemberDialog({
  open,
  onOpenChange,
  projectId,
  projectName,
  projects,
  onRaised,
}: RequestCrossBuMemberDialogProps) {
  const [email, setEmail] = React.useState("");
  const [roleName, setRoleName] = React.useState<string>("developer");
  const [reason, setReason] = React.useState("");
  const [pickedProject, setPickedProject] = React.useState<string>("");

  React.useEffect(() => {
    if (!open) return;
    setEmail("");
    setRoleName("developer");
    setReason("");
    setPickedProject(projectId ?? projects?.[0]?.id ?? "");
  }, [open, projectId, projects]);

  const targetProject = projectId ?? pickedProject;
  const targetName =
    projectName ?? projects?.find((p) => p.id === pickedProject)?.name ?? "this project";

  const raise = useMutation({
    mutationFn: () =>
      requestCrossBuMember(targetProject as ProjectId, {
        email: email.trim(),
        roleName,
        reason: reason.trim(),
      }),
    onSuccess: () => {
      toast.success("Request sent", {
        description: `Their ${BUSINESS_UNIT_LABEL.toLowerCase()}'s admin decides. You'll see it in Requests & Approvals.`,
      });
      onOpenChange(false);
      onRaised();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const emailValid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Ask another {BUSINESS_UNIT_LABEL.toLowerCase()} for someone</DialogTitle>
          <DialogDescription className="text-[13px]">
            {projectId ? `For ${targetName}. ` : ""}Their {BUSINESS_UNIT_LABEL.toLowerCase()} keeps
            them — this asks its admin to lend them to the project.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {projects && !projectId && (
            <div className="space-y-1.5">
              <Label htmlFor="xbu-project">Project</Label>
              <Select value={pickedProject} onValueChange={setPickedProject}>
                <SelectTrigger id="xbu-project">
                  <SelectValue placeholder="Choose a project…" />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="xbu-email">Their email</Label>
            <Input
              id="xbu-email"
              autoFocus
              type="email"
              autoComplete="off"
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <p className="text-muted-foreground text-[11.5px]">
              There is no list to pick from — people in other{" "}
              {BUSINESS_UNIT_LABEL.toLowerCase()}s are not yours to browse.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="xbu-role">Role on this project</Label>
            <Select value={roleName} onValueChange={setRoleName}>
              <SelectTrigger id="xbu-role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CROSS_BU_ASSIGNABLE_ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {ROLE_META[r].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="xbu-reason">
              Why{" "}
              <span className="text-muted-foreground/60 font-normal normal-case">(optional)</span>
            </Label>
            <Textarea
              id="xbu-reason"
              rows={3}
              placeholder="What this project needs them for."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>

          <p className="text-muted-foreground flex items-start gap-1.5 text-[11.5px]">
            <Info className="mt-0.5 size-3 shrink-0" aria-hidden />
            <span>
              If approved, they reach this project and nothing else here — not the other projects
              in this {BUSINESS_UNIT_LABEL.toLowerCase()}, its budget or its connections.
            </span>
          </p>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={raise.isPending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            disabled={!emailValid || !targetProject || raise.isPending}
            aria-busy={raise.isPending}
            onClick={() => raise.mutate()}
          >
            {raise.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Send className="size-4" aria-hidden />
            )}
            Send request
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
