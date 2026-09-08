"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";
import { useOrchestratorStore } from "@/stores/orchestrator-store";
import { useSession } from "@/hooks/use-session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { canUseOrchestrator } from "@/lib/orchestrator/access";
import { OutOfScope } from "@/components/auth/scope-empty-state";

/**
 * `/orchestrator` — the **global** Orchestrator.
 *
 * Every project you can open, in one cockpit: choose the project, choose a
 * model it is allowed to run on, and drive its agent roster. The per-project
 * twin lives at `/projects/[id]/orchestrator` and is the same component with
 * the project fixed (`components/orchestrator/cockpit.tsx`).
 *
 * Project-Admin-only (`canUseOrchestrator`) — see `lib/orchestrator/access.ts`
 * for why. The nav entry already hides the tab from everyone else
 * (`lib/nav.ts`); this is the belt-and-braces check for anyone who lands here
 * by URL instead.
 */
export default function OrchestratorPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlProject = searchParams.get("project");
  const store = useOrchestratorStore;
  const session = useSession({ required: true });
  const role = effectivePlatformRole(session);
  const allowed = canUseOrchestrator(role);

  /**
   * `?project=<id>` is a one-shot seed from the per-project page's "Open
   * globally" link, not a binding — it selects that project's most recent
   * session (if any) and then clears itself, so a refresh doesn't yank the
   * user back to a project they have since navigated away from.
   */
  React.useEffect(() => {
    if (!allowed || !urlProject) return;
    const existing = store.getState().sessions.find((s) => s.projectId === urlProject);
    if (existing) store.getState().selectSession(existing.id);
    router.replace("/orchestrator");
  }, [allowed, urlProject, router, store]);

  if (!allowed) {
    return <OutOfScope kind="resource" backHref="/dashboard" backLabel="Your dashboard" />;
  }

  return <OrchestratorCockpit />;
}
