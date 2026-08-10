"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";
import { useOrchestratorStore } from "@/stores/orchestrator-store";

/**
 * `/orchestrator` — the **global** Orchestrator.
 *
 * Every project you can open, in one cockpit: choose the project, choose a
 * model it is allowed to run on, and drive its agent roster. The per-project
 * twin lives at `/projects/[id]/orchestrator` and is the same component with
 * the project fixed (`components/orchestrator/cockpit.tsx`).
 */
export default function OrchestratorPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlProject = searchParams.get("project");
  const store = useOrchestratorStore;

  /**
   * `?project=<id>` is a one-shot seed from the per-project page's "Open
   * globally" link, not a binding — it selects that project's most recent
   * session (if any) and then clears itself, so a refresh doesn't yank the
   * user back to a project they have since navigated away from.
   */
  React.useEffect(() => {
    if (!urlProject) return;
    const existing = store.getState().sessions.find((s) => s.projectId === urlProject);
    if (existing) store.getState().selectSession(existing.id);
    router.replace("/orchestrator");
  }, [urlProject, router, store]);

  return <OrchestratorCockpit />;
}
