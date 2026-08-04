"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban, Info, Play, RotateCcw, Sparkles, SquarePlay, Workflow } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { LoadingState } from "@/components/ui/loading-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ModelPicker, type ProjectModelOption } from "@/components/orchestrator/model-picker";
import { ProjectPicker } from "@/components/orchestrator/project-picker";
import { SessionRail } from "@/components/orchestrator/session-rail";
import { StageRail } from "@/components/orchestrator/stage-rail";
import { Thread } from "@/components/orchestrator/thread";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { getProject, listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { PHASE_LABEL } from "@/lib/agents";
import { TRACK_META } from "@/lib/tracks";
import { splitModelKey } from "@/lib/orchestrator/types";
import { useOrchestrator } from "@/lib/orchestrator/use-orchestrator";
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
 * Pick a project (or arrive with one), pick a model that project is allowed to
 * run on, and it executes that project's agent roster in hand-off order,
 * streaming each agent's turn and closing the gates it is permitted to close.
 *
 * SCOPE OF "AUTOMATIC" — auto-advance closes `safe` and `consequential` gates
 * on the run's behalf. It never closes a **mandatory** one: PRD §13 makes those
 * unwaivable by the owner *or* the fallback, so a sequencer that waived them
 * would not be automating the process, it would be routing around it. Those
 * stop the run and wait, which is what the inline gate control in the thread is
 * for.
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

  const modelLabel = modelKey ? splitModelKey(modelKey).model_id : "no model";

  const controls = useOrchestrator({
    sessionId: active?.id ?? null,
    projectName: project?.name ?? "this project",
    track: project?.track ?? "greenfield",
    modelLabel,
    modelKey,
  });

  // ── Session plumbing ──────────────────────────────────────────────────────

  /** Ensure a session exists for the current project, and return its id. */
  const ensureSession = React.useCallback((): string | null => {
    if (!project) return null;
    if (active && active.projectId === String(project.id)) return active.id;
    return store.getState().createSession({
      projectId: String(project.id),
      projectName: project.name,
      track: project.track,
      modelKey,
    });
  }, [project, active, store, modelKey]);

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

  // `ensureSession` leaves the new session active in the store, and the engine
  // resolves the session at call time, so these need no deferral.
  const handleRun = React.useCallback(
    (objective?: string) => {
      if (!ensureSession()) return;
      controls.start(objective);
    },
    [ensureSession, controls],
  );

  const handleSend = React.useCallback(
    (text: string) => {
      if (!ensureSession()) return;
      // A brand-new session has no transcript, so the first thing typed is the
      // objective — `send` routes that into `start` itself.
      controls.send(text);
    },
    [ensureSession, controls],
  );

  // ── Access ────────────────────────────────────────────────────────────────
  if (!hasPermission(session, "artifact:view")) {
    return (
      <RestrictedAccess description="The Orchestrator requires access to project artifacts." />
    );
  }
  // ── Who may drive ─────────────────────────────────────────────────────────
  //
  // Driving is a `project_admin` binding on *the project being driven* — which
  // is exactly what `managedProjectIds` holds (`lib/mock/access-scope.ts`), so
  // the access scope answers this on its own and no permission check is
  // layered on top.
  //
  // Deliberately NOT `hasPermission(session, "run:create")`: that verb means
  // "start a run", and Developer, BA, Architect and Data Engineer all hold it
  // (`lib/auth/role-permissions.ts`) because they invoke their own agent. PRD
  // §15.5–§15.11 is narrower — every role but the Project Admin "cannot drive
  // the Orchestrator" — so gating on `run:create` would hand the whole pipeline
  // to four roles the PRD excludes. The converse is free: every identity with a
  // `project_admin` binding holds `run:create` anyway, so the scope check is
  // strictly the right one rather than merely the stricter one.
  //
  // The replaced check was `role === "project_admin" || role === "org_admin"`,
  // a *global* role test. On the cross-project `/orchestrator` that let the
  // admin of one project select another and auto-close its gates; the
  // per-project form closes that. Org-scoped roles are `isOrgWide` and still
  // pass everywhere.
  //
  // Before a project is chosen there is nothing to scope against, so the
  // question becomes "do you administer anything at all" — otherwise the
  // read-only banner would greet a Project Admin on an empty page, and a
  // Developer would not see it until after picking a project.
  //
  // `canManageProject` fails closed while the scope request is in flight, so
  // `scopeReady` holds the controls disabled until it lands.
  const scopeReady = !scope.isLoading;
  const managesAnyProject = scope.isOrgWide || scope.managedProjectIds.length > 0;
  const canDrive =
    scopeReady && (projectId ? scope.canManageProject(projectId) : managesAnyProject);

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
  const status = active?.status ?? "idle";
  const paused = status === "paused";
  const complete = status === "complete";
  const trackMeta = project ? TRACK_META[project.track] : null;
  const ready = !!project && !!modelKey;
  const parkedOnGate = stages[active?.cursor ?? 0]?.status === "awaiting_gate";

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
        {/* ── Top bar: the pickers, and the run controls ──────────────────── */}
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

          <div className="ml-auto flex items-center gap-3">
            <span className="hidden items-center gap-2 lg:flex">
              <Switch
                id="auto-advance"
                checked={active?.autoAdvance ?? true}
                onCheckedChange={(v) => active && store.getState().setAutoAdvance(active.id, v)}
                disabled={!active || !canDrive}
              />
              <Label htmlFor="auto-advance" className="text-[12px] font-normal">
                Auto-advance
              </Label>
            </span>

            {canDrive &&
              (controls.busy ? (
                <Button size="sm" variant="outline" className="h-8 gap-1.5" onClick={controls.stop}>
                  Stop
                </Button>
              ) : complete || (active && active.messages.length > 0 && !paused) ? (
                <Button size="sm" className="h-8 gap-1.5" disabled={!ready} onClick={() => handleRun()}>
                  <RotateCcw className="size-3.5" aria-hidden />
                  Restart run
                </Button>
              ) : paused ? (
                <Button
                  size="sm"
                  className="h-8 gap-1.5"
                  onClick={controls.resume}
                  disabled={parkedOnGate}
                  title={parkedOnGate ? "Decide the open gate in the thread to continue" : undefined}
                >
                  <SquarePlay className="size-3.5" aria-hidden />
                  Resume
                </Button>
              ) : (
                <Button size="sm" className="h-8 gap-1.5" disabled={!ready} onClick={() => handleRun()}>
                  <Play className="size-3.5" aria-hidden />
                  Run pipeline
                </Button>
              ))}
          </div>
        </header>

        {/* Silent while the scope is still resolving — "you cannot drive this"
            is an assertion, and asserting it before the answer has arrived
            reads as an access denial that then disappears. */}
        {!canDrive && scopeReady && (
          <div className="border-line-soft text-muted-foreground flex shrink-0 items-start gap-2 border-b px-4 py-2 text-[12.5px] md:px-6">
            <Info className="mt-px size-4 shrink-0" aria-hidden />
            <p>
              {!managesAnyProject ? (
                <>
                  You&apos;re viewing the Orchestrator read-only — driving it across agents is the
                  Project Admin&apos;s role. You act on your own agent&apos;s gates from{" "}
                  <Link href="/approvals" className="text-brand-bright underline underline-offset-2">
                    Approvals
                  </Link>
                  .
                </>
              ) : (
                <>
                  Read-only here — you don&apos;t administer{" "}
                  <span className="text-foreground font-medium">
                    {project?.name ?? "this project"}
                  </span>
                  , so you can&apos;t drive its pipeline. Its Project Admin can, and you act on your
                  own agent&apos;s gates from{" "}
                  <Link href="/approvals" className="text-brand-bright underline underline-offset-2">
                    Approvals
                  </Link>
                  .
                </>
              )}
            </p>
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <Thread
            messages={active?.messages ?? []}
            busy={controls.busy}
            disabled={!ready || !canDrive}
            placeholder={
              !project
                ? "Pick a project to begin…"
                : !modelKey
                  ? "Pick a model to begin…"
                  : !canDrive
                    ? "Read-only — you cannot drive this Orchestrator"
                    : (active?.messages.length ?? 0) === 0
                      ? `What should the ${project.name} pipeline achieve? Enter starts the run.`
                      : "Message the Orchestrator…"
            }
            onSend={handleSend}
            onStop={controls.stop}
            onGateDecision={controls.decideGate}
            emptySlot={
              <EmptyThread
                projectName={project?.name ?? null}
                trackLabel={trackMeta ? `Track ${trackMeta.number} · ${trackMeta.label}` : null}
                stageCount={stages.length}
                firstStage={stages[0] ? PHASE_LABEL[stages[0].phase] : null}
                ready={ready && canDrive}
                onRun={() => handleRun()}
              />
            }
          />

          <div className={cn("hidden", variant === "page" ? "xl:block" : "lg:block")}>
            <StageRail stages={stages} cursor={active?.cursor ?? 0} projectId={projectId} />
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyThread({
  projectName,
  trackLabel,
  stageCount,
  firstStage,
  ready,
  onRun,
}: {
  projectName: string | null;
  trackLabel: string | null;
  stageCount: number;
  firstStage: string | null;
  ready: boolean;
  onRun: () => void;
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
            <span className="text-foreground">{trackLabel}</span> — {stageCount} agents, starting at{" "}
            <span className="text-foreground">{firstStage}</span>. Each agent hands its artifacts to
            the next automatically. Mandatory gates stop the run and wait for you.
          </>
        ) : (
          "Choose a project and one of the models it is allowed to run on. The Orchestrator then executes that project's agent roster in hand-off order."
        )}
      </p>
      {ready && (
        <Button size="sm" className="mt-1 gap-1.5" onClick={onRun}>
          <Play className="size-3.5" aria-hidden />
          Run the pipeline
        </Button>
      )}
    </div>
  );
}
