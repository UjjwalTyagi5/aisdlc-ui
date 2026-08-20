"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { AccessLevelPicker } from "@/components/app/access-level-picker";
import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import {
  clearProjectIntegrationAccess,
  listProjectIntegrationAccess,
  setProjectIntegrationAccess,
} from "@/lib/api/integration-access";
import { ACCESS_LEVEL_LABEL } from "@/lib/schemas/integration-access";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/**
 * What THIS project may do with each integration its unit was granted.
 *
 * THE PROJECT-WIDE DEFAULT. The organisation grants a {BUSINESS_UNIT_LABEL} REACH to
 * an integration — whether it may use the thing at all — and this sets what the
 * project's stages do with it by default. A stage that picked its own mode in
 * Settings -> Tools per stage overrides this; that is where the real per-agent
 * decision is made, and this screen is the shortcut for "read-only everywhere here".
 *
 * THERE IS NO CEILING ANY MORE. The unit's grant used to carry a level that capped
 * this one, and the picker disabled whatever lay outside it. Backend migration 0024
 * removed that: a grant is reach only, so nothing above the project bounds the
 * read/write choice. Nothing here is disabled for being "too wide" — the only
 * remaining refusal is a connector that cannot honour a level at all (Slack cannot
 * read), which the server raises and this surfaces verbatim.
 *
 * "no default" MEANS the stages fall through to read and write, which is the stage
 * picker's own documented default. It is labelled rather than left blank because a
 * blank reads as "nothing", and it means the opposite.
 */
export function ProjectAccessList({
  projectId,
  canManage,
}: {
  projectId: string;
  /**
   * A Business Unit Admin over this project's unit, or its Project Admin. Both, as
   * `assert_can_administer_project` defines the pair server-side — a unit admin
   * because deciding what each of their projects may do is what running a unit
   * means, a project admin because tightening your own project needs no permission
   * from above. Presentation only; the server checks the same thing.
   */
  canManage: boolean;
}) {
  const queryClient = useQueryClient();

  const q = useQuery({
    queryKey: ["project-integration-access", projectId],
    queryFn: () => listProjectIntegrationAccess(projectId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["project-integration-access", projectId] });
    // The effective level changes what the project's agents may do.
    queryClient.invalidateQueries({ queryKey: ["projects"] });
  };

  const narrow = useMutation({
    mutationFn: setProjectIntegrationAccess,
    onSuccess: (r, vars) => {
      toast.success(
        `${label(vars.targetId)} is now ${ACCESS_LEVEL_LABEL[r.effectiveAccess].toLowerCase()} for this project`,
      );
      for (const w of r.warnings ?? []) toast.warning(w, { duration: 9000 });
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const reset = useMutation({
    mutationFn: clearProjectIntegrationAccess,
    onSuccess: (_r, vars) => {
      toast.success(
        `${label(vars.targetId)} has no project default — each stage decides`,
      );
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const label = (targetId: string) =>
    CONNECTOR_KIND_LABEL[targetId as keyof typeof CONNECTOR_KIND_LABEL] ?? targetId;

  if (q.isLoading) return <LoadingState variant="list" rows={3} />;
  if (q.isError) {
    return (
      <ApiErrorState
        title="Couldn't load this project's access"
        description={q.error instanceof Error ? q.error.message : undefined}
        onRetry={() => q.refetch()}
      />
    );
  }

  const rows = q.data ?? [];
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-[12.5px]">
        This project&apos;s {BUSINESS_UNIT_LABEL.toLowerCase()} has not been granted any
        integrations yet. An Organization Admin grants them on the Integrations page.
      </p>
    );
  }

  const busy = narrow.isPending || reset.isPending;

  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <li
          key={`${row.kind}:${row.targetId}`}
          className="border-line-soft flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border px-3 py-2.5"
        >
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
            {label(row.targetId)}
          </span>

          {/* No unit badge any more. The unit's grant carries no level to show as a
              ceiling — it only decides whether this integration appears here at all.
              What is set below is this project's DEFAULT; a stage that picked its own
              mode in Settings -> Tools per stage overrides it. */}
          {row.inherited && (
            <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
              no default
            </Badge>
          )}

          {canManage ? (
            <div className="flex shrink-0 items-center gap-2">
              <AccessLevelPicker
                size="sm"
                value={row.effectiveAccess}
                disabled={busy}
                onChange={(next) =>
                  narrow.mutate({
                    projectId,
                    kind: row.kind,
                    targetId: row.targetId,
                    access: next,
                  })
                }
              />
              {/* Only when there is a default to clear. Offered on a row that has
                  none, it would be a no-op presented as an action. */}
              {!row.inherited && (
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={busy}
                  aria-label={`Clear the default for ${label(row.targetId)}`}
                  title="Clear this default — stages decide for themselves"
                  onClick={() =>
                    reset.mutate({ projectId, kind: row.kind, targetId: row.targetId })
                  }
                >
                  <RotateCcw className="size-3.5" aria-hidden />
                </Button>
              )}
            </div>
          ) : (
            <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
              {row.effectiveLabel}
            </Badge>
          )}
        </li>
      ))}
    </ul>
  );
}
