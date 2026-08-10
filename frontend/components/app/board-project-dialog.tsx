"use client";

import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { LoadingState } from "@/components/ui/loading-state";
import { getBoardProjects, ingestBoard } from "@/lib/api/projects";
import type { ProjectId } from "@/lib/schemas";

/** Friendly board-provider labels for the dialog title (keyed by backend kind). */
const PROVIDER_LABELS: Record<string, string> = {
  azure_devops: "Azure DevOps",
  jira: "Jira",
  github_issues: "GitHub Issues",
  linear: "Linear",
};

export interface BoardProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: ProjectId;
  /** Called after a successful ingest so the caller can refetch stories. */
  onIngested: () => void;
}

/**
 * "Run Requirements agent" picker. Discovers the connected provider's board
 * projects, lets the user choose one (pre-selecting the remembered choice), then
 * pulls that project's work items into stories. Always opened explicitly so the
 * user sees and confirms which board they're importing from.
 */
export function BoardProjectDialog({
  open,
  onOpenChange,
  projectId,
  onIngested,
}: BoardProjectDialogProps) {
  // Chosen board provider (when the stage has more than one, e.g. ADO + Jira).
  const [provider, setProvider] = React.useState<string | undefined>(undefined);
  const q = useQuery({
    queryKey: ["board-projects", projectId, provider ?? "default"],
    queryFn: () => getBoardProjects(projectId, provider),
    enabled: open,
    staleTime: 30_000,
  });

  const [choice, setChoice] = React.useState<string | null>(null);
  const available = q.data?.available_providers ?? [];

  // Adopt the resolved provider as the chooser's value on first load.
  React.useEffect(() => {
    if (q.data?.provider && provider === undefined) setProvider(q.data.provider);
  }, [q.data?.provider, provider]);

  // Pre-select the remembered board (or the first) once discovery resolves.
  React.useEffect(() => {
    if (q.data) setChoice(q.data.selected ?? q.data.projects[0]?.name ?? null);
  }, [q.data]);
  React.useEffect(() => {
    if (!open) {
      setChoice(null);
      setProvider(undefined);
    }
  }, [open]);

  const ingest = useMutation({
    mutationFn: () => ingestBoard(projectId, choice ?? undefined, provider),
    onSuccess: (res) => {
      if (res.ingested > 0) {
        toast.success(
          `Pulled ${res.ingested} ${res.ingested === 1 ? "story" : "stories"} from ${res.board_project ?? "the board"}`,
        );
      } else {
        toast.message(`No work items found in ${res.board_project ?? "the selected project"}`);
      }
      onIngested();
      onOpenChange(false);
    },
    onError: (err) =>
      toast.error("Couldn't pull from the board", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const projects = q.data?.projects ?? [];
  const providerLabel = PROVIDER_LABELS[q.data?.provider ?? ""] ?? "board";

  return (
    <Dialog open={open} onOpenChange={(v) => !ingest.isPending && onOpenChange(v)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Pull from {providerLabel} board</DialogTitle>
          <DialogDescription>
            Choose which board project to import work items from. Stories load only from the
            selected project.
          </DialogDescription>
        </DialogHeader>

        {available.length > 1 && (
          <div className="space-y-1.5">
            <p className="text-muted-foreground text-xs font-medium uppercase tracking-wider">
              Board source
            </p>
            <RadioGroup
              value={provider ?? ""}
              onValueChange={(v) => {
                setProvider(v);
                setChoice(null);
              }}
              className="flex flex-wrap gap-2"
              aria-label="Board provider"
            >
              {available.map((pk) => {
                const rid = `prov-${pk}`;
                return (
                  <Label
                    key={pk}
                    htmlFor={rid}
                    className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 font-normal transition-colors"
                  >
                    <RadioGroupItem value={pk} id={rid} />
                    <span className="text-sm">{PROVIDER_LABELS[pk] ?? pk}</span>
                  </Label>
                );
              })}
            </RadioGroup>
          </div>
        )}

        {available.length > 1 && (
          <p className="text-muted-foreground text-xs font-medium uppercase tracking-wider">
            Project
          </p>
        )}
        <div className="min-h-[8rem]">
          {q.isLoading ? (
            <LoadingState variant="list" rows={3} />
          ) : q.isError ? (
            <p className="text-destructive text-sm">
              {q.error instanceof Error
                ? q.error.message
                : "Couldn't reach the board. Connect a provider on the Integrations page."}
            </p>
          ) : projects.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No projects found on the connected board.
            </p>
          ) : (
            <RadioGroup
              value={choice ?? ""}
              onValueChange={setChoice}
              className="max-h-72 space-y-1.5 overflow-auto"
              aria-label="Board project"
            >
              {projects.map((p) => {
                const rid = `bp-${p.name}`;
                return (
                  <Label
                    key={p.name}
                    htmlFor={rid}
                    className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-3 rounded-lg border p-3 font-normal transition-colors"
                  >
                    <RadioGroupItem value={p.name} id={rid} />
                    <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{p.name}</span>
                      {p.key && p.key !== p.name && (
                        <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                          {p.key}
                        </span>
                      )}
                    </span>
                  </Label>
                );
              })}
            </RadioGroup>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={ingest.isPending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            onClick={() => ingest.mutate()}
            disabled={!choice || ingest.isPending}
            aria-busy={ingest.isPending}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]"
          >
            {ingest.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            {ingest.isPending ? "Pulling…" : "Pull stories"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
