"use client";

import * as React from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";

import { Copilot } from "@/components/copilot/copilot";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";

/**
 * `/projects/[id]/copilot?run=<runId>` — the Orchestrator Copilot page.
 * "Run Agent" navigates here after creating a run.
 */
export default function CopilotPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = params.id;
  const runId = searchParams.get("run") ?? "";

  if (!runId) {
    return (
      <div className="mx-auto w-full max-w-lg p-6 md:p-10">
        <EmptyState
          title="No run selected"
          description="The Copilot needs a run to drive. Start a run from the project to open the pipeline cockpit."
          variant="plain"
          action={
            <Button asChild size="sm">
              <Link href={`/projects/${projectId}`}>Back to project</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return <Copilot projectId={projectId} runId={runId} />;
}
