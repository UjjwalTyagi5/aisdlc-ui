"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { createRequest } from "@/lib/api/governance-approvals";
import { PHASE_LABEL } from "@/lib/agents";
import { agentOwnerRole } from "@/lib/requests/routing";
import { ROLE_META } from "@/lib/roles";
import { AGENT_OWNERSHIP, type PlatformRole } from "@/lib/roles";
import type { Phase } from "@/lib/schemas/enums";

/**
 * A contributor asking to work with an agent their role does not reach.
 *
 * Small on purpose. The ask is one sentence — which agent, and why — and every
 * heavier request form on the platform is a page for a reason this one is not:
 * this is raised mid-task, from the screen where the person just found the
 * door locked, and a form that asked for a priority and attachments would be
 * asking them to stop what they were doing.
 *
 * The justification is required rather than optional. Both approvers are being
 * asked to widen someone's access, and "why" is the entire content of that
 * decision — an empty one puts the burden of guessing on the approver.
 */
export function RequestAgentAccessDialog({
  projectId,
  projectName,
  workspaceId,
  workspaceName,
  phases,
  requesterRole,
}: {
  projectId: string;
  projectName: string;
  workspaceId: string;
  workspaceName: string;
  /** The phases this project actually runs — asking for an agent the track
   *  does not include would be a request nobody can grant. */
  phases: Phase[];
  requesterRole: PlatformRole | null;
}) {
  const [open, setOpen] = React.useState(false);
  const [phase, setPhase] = React.useState<Phase | "">("");
  const [justification, setJustification] = React.useState("");

  // Only the agents they cannot already reach. Offering one they hold would
  // produce a request whose correct answer is "you already have this".
  const options = React.useMemo(
    () =>
      phases.filter((p) => !requesterRole || AGENT_OWNERSHIP[requesterRole][p] === "none"),
    [phases, requesterRole],
  );

  const raise = useMutation({
    mutationFn: () =>
      createRequest({
        type: "agent_access",
        title: `Access to the ${PHASE_LABEL[phase as Phase]} agent`,
        description: justification.trim(),
        priority: "normal",
        workspaceId,
        projectId,
        // The ask itself — what stage two is routed by.
        phase: phase as Phase,
        // Nothing to attach: the justification IS the request. The heavier
        // forms take evidence; this one is raised mid-task.
        attachments: [],
      }),
    onSuccess: () => {
      toast.success("Request sent to your Project Admin", {
        description: "They review it first, then the agent's owner signs off.",
      });
      setOpen(false);
      setPhase("");
      setJustification("");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  // Nothing to ask for — every agent this project runs is already reachable.
  if (options.length === 0) return null;

  const owner = phase ? agentOwnerRole(phase as Phase) : null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <KeyRound className="size-4" aria-hidden />
          Request agent access
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Request agent access</DialogTitle>
          <DialogDescription>
            For {projectName} in {workspaceName}. Your Project Admin reviews it first; the
            agent&apos;s owner gives the final sign-off.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="agent-access-phase">Agent</Label>
            <Select value={phase} onValueChange={(v) => setPhase(v as Phase)}>
              <SelectTrigger id="agent-access-phase">
                <SelectValue placeholder="Choose an agent" />
              </SelectTrigger>
              <SelectContent>
                {options.map((p) => (
                  <SelectItem key={p} value={p}>
                    {PHASE_LABEL[p]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {owner && (
              // Named up front, so the second approver is not a surprise when
              // the request visibly stops with someone else.
              <p className="text-muted-foreground text-[11.5px]">
                Final sign-off: {ROLE_META[owner].label}.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="agent-access-why">Why you need it</Label>
            <Textarea
              id="agent-access-why"
              rows={4}
              value={justification}
              placeholder="Covering the BA this sprint — I need to update the acceptance criteria before Thursday's gate."
              onChange={(e) => setJustification(e.target.value)}
            />
            <p className="text-muted-foreground text-[11.5px]">
              Both approvers see this, and it is the whole of what they are deciding on.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => raise.mutate()}
            disabled={!phase || justification.trim().length < 10 || raise.isPending}
          >
            {raise.isPending ? "Sending…" : "Send request"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
