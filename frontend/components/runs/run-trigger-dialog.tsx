"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Loader2, Play } from "lucide-react";

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
import { ModelSelector } from "@/components/app/model-selector";
import { createRun } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";

export function RunTriggerDialog({
  projectId,
  open,
  onOpenChange,
  /** Pre-selects the offering when the dialog opens (e.g. the project header's choice). */
  initialOffering,
}: {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialOffering?: string;
}) {
  const queryClient = useQueryClient();
  const router = useRouter();

  // Selected offering (provider connection + model). Undefined → org default,
  // resolved server-side at dispatch.
  const [offeringId, setOfferingId] = React.useState<string | undefined>(initialOffering);

  React.useEffect(() => {
    if (open) setOfferingId(initialOffering);
  }, [open, initialOffering]);

  const runMutation = useMutation({
    mutationFn: () =>
      createRun({
        project_id: projectId,
        offering_id: offeringId ?? null,
        // Run Agent opens the Copilot, which drives the pipeline chat-first — create
        // a conversational run so the Copilot can DRIVE it.
        conversational: true,
      }),
    onSuccess: ({ runId }) => {
      toast.success("Run started");
      queryClient.invalidateQueries({ queryKey: qk.runs.all() });
      onOpenChange(false);
      // Run Agent opens the Orchestrator Copilot (the conversational cockpit) —
      // it drives the pipeline via the Copilot WS bound to this run.
      router.push(`/projects/${projectId}/copilot?run=${runId}`);
    },
    onError: (err) =>
      toast.error("Couldn't start run", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const pending = runMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={(v) => !pending && onOpenChange(v)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Run SDLC pipeline</DialogTitle>
          <DialogDescription>
            Runs Requirements &rarr; Design &rarr; Development &rarr; Testing for this project using
            the selected provider connection and model.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <Label htmlFor="run-model-select">Provider &amp; model</Label>
          <ModelSelector
            id="run-model-select"
            variant="field"
            aria-label="Provider and model"
            projectId={projectId}
            value={offeringId}
            onValueChange={setOfferingId}
          />
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            onClick={() => runMutation.mutate()}
            disabled={pending}
            aria-busy={pending}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]"
          >
            {pending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Play className="size-4" aria-hidden />
            )}
            {pending ? "Starting…" : "Run pipeline"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
