"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban, Info, Sparkles, Workflow } from "lucide-react";

import { cn } from "@/lib/utils";
import { LoadingState } from "@/components/ui/loading-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { RequestAccessButton } from "@/components/requests/request-access-button";
import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";
import { ModelPicker, type ProjectModelOption } from "@/components/orchestrator/model-picker";
import { ProjectPicker } from "@/components/orchestrator/project-picker";
import { SessionRail } from "@/components/orchestrator/session-rail";
import { Thread } from "@/components/orchestrator/thread";
import { useAccessScope } from "@/hooks/use-access-scope";
import { roleAgentSplit } from "@/lib/agent-access";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { getProject, listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { TRACK_META } from "@/lib/tracks";
import { freshStages, useOrchestratorStore } from "@/stores/orchestrator-store";
import type { ProjectId } from "@/lib/schemas";

export interface OrchestratorCockpitProps {
  /**
   * Fixes the cockpit to one project.
   *
   * Set on `/projects/[id]/orchestrator`, where the project is already the
   * page's subject: the picker is replaced by a static label and the session
   * rail only lists that project's runs. Omitted on the global
   * `/orchestrator`, where choosing the project is the first thing you do.
   */
  lockedProjectId?: string;
  /**
   * `"page"` owns the viewport (global route). `"embedded"` sits under the
   * project shell's context strip and tabs, so it takes a bounded height and
   * renders as a panel rather than edge-to-edge.
   */
  variant?: "page" | "embedded";
}

/**
 * The Orchestrator cockpit — one component behind two routes.
 *
 * Pick a project (or arrive with one) and a model that project is allowed to
 * run on; the session rail keeps a history of conversations against this
 * Business Unit's projects. There is no fixed agent order here — any agent
 * can pick up work based on what the conversation asks for.
 *
 * NO ENGINE YET — the composer is intentionally disabled. What used to drive
 * a scripted, timed reveal of fake agent turns has been removed outright
 * (see the SDD's mock-engine deletion); until the real engine lands, this
 * component only renders whatever a session already holds.
 */
export function OrchestratorCockpit({
  lockedProjectId,
  variant = "page",
}: OrchestratorCockpitProps) {
  const session = useSession({ required: true });
  const scope = useAccessScope();
  const locked = !!lockedProjectId;

  // Zustand's persisted store rehydrates on the client only; rendering its
  // contents before that would mismatch the server's empty render.
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  // The artifacts panel starts collapsed — there is nothing to show until a
  // run has produced something, and an empty expanded panel is just noise.
  const [artifactsCollapsed, setArtifactsCollapsed] = React.useState(true);

  const allSessions = useOrchestratorStore((s) => s.sessions);
  const activeSessionId = useOrchestratorStore((s) => s.activeSessionId);
  const store = useOrchestratorStore;

  // Locked to a project → the rail is that project's history, not everyone's.
  const sessions = React.useMemo(
    () => (locked ? allSessions.filter((s) => s.projectId === lockedProjectId) : allSessions),
    [allSessions, locked, lockedProjectId],
  );

  // A session belonging to some other project must not drive this surface —
  // the store's active id is shared between the global and per-project routes.
  const active = React.useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  // ── Selection ─────────────────────────────────────────────────────────────
  const [pendingProjectId, setPendingProjectId] = React.useState<string | null>(null);
  const [pendingModelKey, setPendingModelKey] = React.useState<string | null>(null);

  const projectId = lockedProjectId ?? active?.projectId ?? pendingProjectId;
  const modelKey = active?.modelKey ?? pendingModelKey;

  const projectQ = useQuery({
    queryKey: qk.projects.detail((projectId ?? "") as ProjectId),
    queryFn: () => getProject(projectId as ProjectId),
    enabled: !!projectId,
  });
  const project = projectQ.data ?? null;

  // Names for the session rail's sub-label — one list request, already scoped.
  const projectsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 100 }),
    queryFn: () => listProjects({ pageSize: 100 }),
    staleTime: 30_000,
    // The locked cockpit already has its project from the route; the list is
    // only needed to label *other* projects' sessions, which it never shows.
    enabled: !locked,
  });
  const projectName = React.useCallback(
    (id: string) =>
      project && String(project.id) === id
        ? project.name
        : (projectsQ.data?.items.find((p) => String(p.id) === id)?.name ?? "Unknown project"),
    [projectsQ.data, project],
  );

  // ── Session plumbing ──────────────────────────────────────────────────────

  const handleProjectChange = React.useCallback(
    (id: string) => {
      setPendingProjectId(id);
      setPendingModelKey(null);
      if (!active) return;
      const p = projectsQ.data?.items.find((x) => String(x.id) === id);
      if (active.messages.length === 0) {
        // Untouched session — repoint it rather than littering the rail.
        store.getState().retargetSession(active.id, {
          projectId: id,
          projectName: p?.name ?? "Session",
          track: p?.track ?? "greenfield",
          modelKey: null,
        });
      } else if (p) {
        // A run already happened here; switching project starts a fresh one so
        // the transcript keeps describing the project it actually ran against.
        store.getState().createSession({
          projectId: id,
          projectName: p.name,
          track: p.track,
          modelKey: null,
        });
      }
    },
    [active, store, projectsQ.data],
  );

  const handleModelChange = React.useCallback(
    (key: string) => {
      setPendingModelKey(key);
      if (active) store.getState().setModelKey(active.id, key);
    },
    [active, store],
  );

  // Seed the model from the project's own default once the picker resolves it.
  const handleOptionsResolved = React.useCallback(
    (options: ProjectModelOption[], defaultKey: string | null) => {
      const stillValid = modelKey && options.some((o) => o.key === modelKey);
      if (stillValid || !defaultKey) return;
      setPendingModelKey(defaultKey);
      if (active) store.getState().setModelKey(active.id, defaultKey);
    },
    [modelKey, active, store],
  );

  // Above the early return below, because hooks cannot be called conditionally.
  const reachableAgents = React.useMemo(
    () => (scope.role ? roleAgentSplit(scope.role, project?.track).reachable : []),
    [scope.role, project?.track],
  );

  // ── Access ────────────────────────────────────────────────────────────────
  if (!hasPermission(session, "artifact:view")) {
    return (
      <RestrictedAccess description="The Orchestrator requires access to project artifacts." />
    );
  }
  // ── Who may drive ─────────────────────────────────────────────────────────
  //
  // MEMBERSHIP PLUS AGENT REACH, not a Project Admin binding.
  //
  // The old rule was "only the Project Admin drives", which made this screen
  // read-only for the people it is for. A Developer opening the Orchestrator on
  // their own project was told to go to Approvals — but they are not waiting on
  // a gate, they are trying to run the Development agent they own.
  //
  // What actually bounds a person here is the same thing that bounds them
  // everywhere else: the agents their role reaches in this project
  // (`AGENT_OWNERSHIP` via `roleAgentSplit`). A BA drives Requirements and
  // Design; a Developer drives Development; the Project Admin reaches every
  // agent and so drives the whole roster — which is the PRD reading, arrived at
  // by their access rather than by a special case for them.
  //
  // Two conditions, both necessary:
  //   1. You are bound to the project (`projectIds`), or org-wide. Reaching an
  //      agent in the abstract is not standing to run it on someone else's work.
  //   2. Your role reaches at least one agent in this project's track. The
  //      governance tier reaches none by design (PRD §14.8), so they still get
  //      the read-only view — correctly, since they never run agents.
  const scopeReady = !scope.isLoading;
  const reachesAnyAgent = reachableAgents.length > 0;
  const inProject = (id: string | null) =>
    scope.isOrgWide || (id !== null && scope.projectIds.includes(id));
  const canDrive =
    scopeReady && reachesAnyAgent && (projectId ? inProject(projectId) : true);

  if (!mounted) {
    return (
      <div className="p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }

  // With no session yet, show the roster the project *would* run rather than an
  // empty rail: before you start anything is precisely when "which agents, who
  // owns their gates, and what is the project already holding on" is the
  // question — the read-only control view this rail replaced answered it
  // without needing a run either.
  const stages = active?.stages ?? (project ? freshStages(project.track) : []);
  const trackMeta = project ? TRACK_META[project.track] : null;

  const shell =
    variant === "page"
      ? "h-[calc(100vh-var(--app-header-h,3.5rem))]"
      : // Under the project shell's context strip + tabs. Bounded rather than
        // viewport-tall so the page never scrolls two panes at once.
        "h-[calc(100vh-var(--app-header-h,3.5rem)-14rem)] min-h-[520px] border-line-soft mt-4 overflow-hidden rounded-xl border";

  return (
    <div className={cn("flex min-h-0", shell)}>
      <div className="hidden md:block">
        <SessionRail
          sessions={sessions}
          activeId={active?.id ?? null}
          onSelect={(id) => store.getState().selectSession(id)}
          onCreate={() => {
            if (!project) return;
            store.getState().createSession({
              projectId: String(project.id),
              projectName: project.name,
              track: project.track,
              modelKey,
            });
          }}
          onRename={(id, title) => store.getState().renameSession(id, title)}
          onDelete={(id) => store.getState().deleteSession(id)}
          projectName={projectName}
        />
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* ── Top bar: project and model pickers ──────────────────────────── */}
        <header className="border-line-soft bg-panel-elevated/60 flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2.5 backdrop-blur-sm md:px-6">
          {!locked && (
            <span className="mr-1 flex items-center gap-2">
              <span className="bg-primary text-primary-foreground grid size-7 place-items-center rounded-md">
                <Sparkles className="size-4" aria-hidden />
              </span>
              <span className="font-display text-[15px] font-semibold tracking-tight">
                Orchestrator
              </span>
            </span>
          )}

          {locked ? (
            // The project is the page's subject already — restating it as a
            // picker would imply you can switch it here, which you cannot.
            <span className="border-line-soft bg-surface-1 text-muted-foreground inline-flex h-8 max-w-[340px] items-center gap-2 rounded-md border px-3 text-[12.5px]">
              <FolderKanban className="size-3.5 shrink-0" aria-hidden />
              <span className="text-foreground truncate font-medium">
                {project?.name ?? "Loading…"}
              </span>
              {trackMeta && (
                <span className="shrink-0 font-mono text-[11px]">T{trackMeta.number}</span>
              )}
            </span>
          ) : (
            <ProjectPicker value={projectId} onValueChange={handleProjectChange} />
          )}

          <ModelPicker
            projectId={projectId}
            workspaceId={project?.workspaceId ?? null}
            value={modelKey}
            onValueChange={handleModelChange}
            onOptionsResolved={handleOptionsResolved}
          />
        </header>

        {/* Silent while the scope is still resolving — "you cannot drive this"
            is an assertion, and asserting it before the answer has arrived
            reads as an access denial that then disappears. */}
        {!canDrive && scopeReady && (
          <div className="border-line-soft text-muted-foreground flex shrink-0 items-start gap-2 border-b px-4 py-2 text-[12.5px] md:px-6">
            <Info className="mt-px size-4 shrink-0" aria-hidden />
            <p>
              {/* Two genuinely different reasons, and conflating them was the
                  old copy's failure: it told a Developer that driving "is the
                  Project Admin's role" when in fact they drive their own
                  agents, and were only blocked because they are not on this
                  project. */}
              {!reachesAnyAgent ? (
                <>
                  Your role has no agent access, so there is nothing here for you to run —
                  the Orchestrator drives agents, and yours is a governance role. You approve
                  what others run from{" "}
                  <Link href="/approvals" className="text-brand-bright underline underline-offset-2">
                    Requests &amp; Approvals
                  </Link>
                  .
                </>
              ) : (
                <>
                  Read-only — you&apos;re not a member of{" "}
                  <span className="text-foreground font-medium">
                    {project?.name ?? "this project"}
                  </span>
                  . Reaching an agent elsewhere isn&apos;t standing to run it on this team&apos;s
                  work; ask its Project Admin to add you, or{" "}
                  <RequestAccessButton
                    variant="ghost"
                    size="sm"
                    label="request access"
                    prefill={{
                      type: "access_request",
                      title: `Access to ${project?.name ?? "this project"}`,
                      description: "Requesting to join this project as a contributor.",
                      // Not `project?.id`: `project` comes from GET /projects/:id,
                      // which 404s for exactly the person this button is for — not
                      // a member, so the detail fetch is refused before the name
                      // or id in the response body ever reaches them. `projectId`
                      // (the route param, resolved above independently of that
                      // fetch) is what's actually known good here; falling back to
                      // the fetched object would silently raise a project-less
                      // request, which is the one thing this button exists to not do.
                      projectId: projectId ?? undefined,
                      workspaceId: project?.workspaceId ?? undefined,
                    }}
                  />
                  .
                </>
              )}
            </p>
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <Thread
            messages={active?.messages ?? []}
            busy={false}
            disabled
            placeholder="The Orchestrator engine arrives in the next phase."
            onSend={() => {}}
            onStop={() => {}}
            onGateDecision={() => {}}
            emptySlot={
              <EmptyThread
                projectName={project?.name ?? null}
                trackLabel={trackMeta ? `Track ${trackMeta.number} · ${trackMeta.label}` : null}
                agentCount={stages.length}
              />
            }
          />

          <div className={cn("hidden", variant === "page" ? "xl:block" : "lg:block")}>
            <ArtifactsPanel
              runId=""
              activeStage=""
              gate={null}
              artifacts={[]}
              openArtifactId={null}
              onSelectArtifact={() => {}}
              streamingArtifactId={null}
              collapsed={artifactsCollapsed}
              onToggle={() => setArtifactsCollapsed((v) => !v)}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyThread({
  projectName,
  trackLabel,
  agentCount,
}: {
  projectName: string | null;
  trackLabel: string | null;
  agentCount: number;
}) {
  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-3 py-16 text-center">
      <span className="border-line-soft bg-surface-2 grid size-11 place-items-center rounded-xl border">
        <Workflow className="text-brand-bright size-5" aria-hidden />
      </span>
      <h2 className="font-display text-base font-semibold tracking-tight">
        {projectName ? `Ready to orchestrate ${projectName}` : "Pick a project to orchestrate"}
      </h2>
      <p className="text-muted-foreground max-w-md text-[13px] leading-relaxed">
        {projectName && trackLabel ? (
          <>
            <span className="text-foreground">{trackLabel}</span> — {agentCount} agents on the
            roster. The Orchestrator engine arrives in the next phase, so nothing runs yet.
          </>
        ) : (
          "Choose a project and one of the models it is allowed to run on. The Orchestrator engine arrives in the next phase, so nothing runs yet."
        )}
      </p>
    </div>
  );
}
