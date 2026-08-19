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
 * THE THIRD RUNG. The organisation permits a kind, a {BUSINESS_UNIT_LABEL} is granted
 * it at a level, and this narrows that level for one project. It is the only decision
 * on this screen that is not somebody else's — which is why the rest of the page is
 * read-only and this is not.
 *
 * BOTH LEVELS ARE SHOWN, ALWAYS. "Read only" on its own does not tell you whether
 * that is a choice somebody made here or the most the organisation allows: the first
 * you can undo on this screen, the second needs an Organization Admin. Naming the
 * unit's level next to the project's is what separates them, and it is why the row
 * says "inherited" rather than leaving the reader to infer it from a blank.
 *
 * THE CEILING IS THE UNIT'S GRANT. The picker disables what lies outside it rather
 * than hiding it, and the server refuses independently — so this control makes the
 * boundary visible without being the thing that enforces it. A 403 `exceeds_grant`
 * from the server is surfaced verbatim rather than pre-empted, because the server is
 * the authority on what is allowed and a second copy of that rule here would give it
 * somewhere to drift.
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
        `${label(vars.targetId)} follows the ${BUSINESS_UNIT_LABEL.toLowerCase()} again`,
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

          {/* What the unit holds — the ceiling, and the thing that explains why an
              option below is unavailable. */}
          <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
            {BUSINESS_UNIT_LABEL}: {ACCESS_LEVEL_LABEL[row.unitAccess]}
          </Badge>

          {row.inherited && (
            <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
              inherited
            </Badge>
          )}

          {canManage ? (
            <div className="flex shrink-0 items-center gap-2">
              <AccessLevelPicker
                size="sm"
                value={row.effectiveAccess}
                ceiling={row.unitAccess}
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
              {/* Only when there is a narrowing to undo. Resetting an inherited row
                  would be a no-op offered as an action, which reads as broken. */}
              {!row.inherited && (
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={busy}
                  aria-label={`Follow the ${BUSINESS_UNIT_LABEL.toLowerCase()} for ${label(row.targetId)}`}
                  title={`Follow the ${BUSINESS_UNIT_LABEL.toLowerCase()}'s level again`}
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
