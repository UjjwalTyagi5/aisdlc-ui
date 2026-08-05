"use client";

import * as React from "react";
import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";

import { Card } from "@/components/ui/card";
import { LoadingState } from "@/components/ui/loading-state";
import { ProjectModelSelectionCard } from "@/components/app/project-model-selection-card";
import { useRawSession } from "@/components/auth/session-provider";
import { hasPermission } from "@/lib/auth/permissions";
import { getProjectModelSelection } from "@/lib/api/models";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/**
 * The project's own Models screen.
 *
 * WHY THIS IS A PAGE AND NOT A SETTINGS TAB. It was reachable only at
 * Settings → Model, which is the right place for a Project Admin (it sits with
 * the other things they configure) and the wrong place for everyone else: a
 * contributor asking "which models can I use here, and how do I ask for one
 * more" was being sent to a screen named after administering the project, most
 * of which is disabled for them. The question is not a settings question, so
 * it gets an address of its own.
 *
 * The card is unchanged and shared with the Settings tab — one list, one set
 * of checkboxes, one Request access button. Duplicating it would have been two
 * places for the project's model selection to disagree.
 */
export default function ProjectModelsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const session = useRawSession();
  const canManage = hasPermission(session, "model:manage");

  // Only to decide which sentence goes above the card — the card fetches its
  // own data, and this shares the query key so it costs nothing extra.
  const q = useQuery({
    queryKey: ["model", "project-selection", id],
    queryFn: () => getProjectModelSelection(id),
  });
  const inheritedCount = q.data?.inherited.length ?? 0;
  const selectedCount = q.data?.selected.length ?? 0;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <Boxes className="text-brand-bright size-4" aria-hidden />
          <h1 className="font-display text-[22px] font-bold tracking-[-0.02em]">Models</h1>
        </div>
        <p className="text-muted-foreground text-[13px]">
          {canManage
            ? `Which of this project's inherited models it actually runs on. The list comes down the cascade — your Organization Admin grants models to the ${BUSINESS_UNIT_LABEL.toLowerCase()}, and this narrows it.`
            : `The models this project can run on. You can't change the list — ask for one that isn't here and it goes to your Project Admin, who can turn it on.`}
        </p>
      </header>

      {q.isLoading ? (
        <LoadingState variant="card" />
      ) : (
        <>
          {/* The one number worth stating up front: a project using fewer than
              it inherits has had a deliberate narrowing applied, and that is
              exactly the situation where "why can't I use X" comes up. */}
          {inheritedCount > 0 && selectedCount < inheritedCount && (
            <Card className="border-line-soft bg-surface-1 p-4">
              <p className="text-[13px]">
                Using {selectedCount} of {inheritedCount} models available to this project.
                {!canManage && " Request any of the rest below."}
              </p>
            </Card>
          )}

          <ProjectModelSelectionCard projectId={id} canManage={canManage} />
        </>
      )}
    </div>
  );
}
