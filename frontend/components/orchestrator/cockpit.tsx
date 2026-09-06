"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Bot, FolderKanban, Info, Sparkles, Workflow } from "lucide-react";

import { cn } from "@/lib/utils";
import { LoadingState } from "@/components/ui/loading-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ArtifactsPanel } from "@/components/orchestrator/artifacts-panel";
import { ModelPicker, type ProjectModelOption } from "@/components/orchestrator/model-picker";
import { ProjectPicker } from "@/components/orchestrator/project-picker";
import { SessionRail } from "@/components/orchestrator/session-rail";
import { Thread } from "@/components/orchestrator/thread";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { canUseOrchestrator } from "@/lib/orchestrator/access";
import { DeliverablesRead } from "@/lib/orchestrator/deliverables";
import {
  deleteConversation,
  getConversationMessages,
  listConversations,
  renameConversation,
} from "@/lib/api/conversations";
import { ORCHESTRATOR_AGENT_IDS, type OrchestratorAgentId } from "@/lib/orchestrator/protocol";
import { agentLabel, splitModelKey } from "@/lib/orchestrator/types";
import {
  useOrchestratorSocket,
  type OrchestratorConnState,
} from "@/lib/orchestrator/use-orchestrator-socket";
import { getModelOptions } from "@/lib/api/models";
import { getProject, listProjects } from "@/lib/api/projects";
import { createRun } from "@/lib/api/runs";
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
 * WHICH AGENT RUNS IS THE ORCHESTRATOR'S CHOICE BY DEFAULT. The picker starts on
 * "Let the Orchestrator choose", and a message sent that way is ROUTED: the engine
 * reads it, and the connection's earlier turns, and picks one of the nine or answers
 * directly. Picking an agent overrides the router for that turn.
 *
 * The invariant the old required-picker stood for is unchanged, and now lives in the
 * engine: nothing is chosen silently. Every routed turn announces its agent AND the
 * reason in `agent.selected` before any of the agent's text, so a wrong choice is
 * visible and correctable in one turn — which is exactly what the previous engine,
 * advancing by list index, never told anyone.
 *
 * A RUN IS CREATED LAZILY, ON THE FIRST TURN. The socket resolves `run_id`
 * against the caller's tenant and refuses anything it cannot verify, so the
 * conversation needs a real `runs` row — but creating one on page load would
 * litter every project with empty runs nobody started. The first message pays
 * for it; the rest of the conversation reuses it.
 */
/**
 * The value the agent picker uses for "let the Orchestrator choose".
 *
 * Radix forbids an empty string as a `SelectItem` value and `null` cannot cross
 * that API, so the no-override option needs a name of its own. Deliberately not
 * one of the nine ids, and never sent on the wire: the hook omits the `agent`
 * field entirely when there is no override.
 */
const AUTO = "__auto__";

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

  // ── History ───────────────────────────────────────────────────────────────
  //
  // The rail used to BE the store: zustand + localStorage, and it said so on screen.
  // A chat survived neither a cleared browser nor a change of device, and none of it
  // was auditable. It is now a view of the server, and the store keeps only the
  // unsaved draft plus which row is selected.
  //
  // A saved chat's id IS its run id (backend `sessions.ensure_session`), which is what
  // makes opening one also reopen its Deliverables, its LangGraph thread and its
  // project scope — no mapping, no second identifier.
  const [openedRunId, setOpenedRunId] = React.useState<string | null>(null);

  // ── Selection ─────────────────────────────────────────────────────────────
  const [pendingProjectId, setPendingProjectId] = React.useState<string | null>(null);
  const [pendingModelKey, setPendingModelKey] = React.useState<string | null>(null);

  const projectId = lockedProjectId ?? active?.projectId ?? pendingProjectId;

  const historyQ = useQuery({
    queryKey: ["orchestrator", "sessions", projectId],
    queryFn: () => listConversations(projectId as ProjectId, "orchestrator"),
    enabled: !!projectId,
  });

  /**
   * What the rail shows: every saved chat on this project, plus the unsaved draft.
   *
   * The draft is local on purpose (spec D23). A run — and therefore a session — is
   * minted by the first turn, so clicking "New" repeatedly cannot litter the database
   * with conversations nobody used.
   */
  const railSessions = React.useMemo(() => {
    const saved = (historyQ.data ?? []).map((s) => ({
      id: s.id,
      title: s.title || "Untitled chat",
      projectId: String(projectId ?? ""),
      status: "idle" as const,
    }));
    const drafts = sessions
      .filter((s) => !saved.some((v) => v.id === s.id))
      .map((s) => ({
        id: s.id, title: s.title, projectId: s.projectId, status: s.status,
      }));
    return [...drafts, ...saved];
  }, [historyQ.data, sessions, projectId]);
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

  // ── Who may drive ─────────────────────────────────────────────────────────
  //
  // PROJECT ADMIN ONLY (`canUseOrchestrator`). The Orchestrator reaches all
  // nine agents at once, so anyone who could drive it would effectively hold
  // every agent's access — the exact `use`-tier leak the one-agent-one-role
  // model removed (see lib/orchestrator/access.ts). Everyone else gets the
  // read-only view below and drives their own owned agent from its own page.
  //
  // This is the UI's half of the answer only. The socket resolves the caller's
  // role server-side and refuses before the handshake, because gating the UI is
  // not access control — last time, typing the URL was enough.
  const scopeReady = !scope.isLoading;
  const canDrive = scopeReady && canUseOrchestrator(scope.role);

  // ── The engine ────────────────────────────────────────────────────────────

  // `null` is "let the Orchestrator choose", which is the DEFAULT and a real
  // selection rather than an unanswered question. Picking an agent overrides the
  // router for that turn. Phase 2 had no router, so the picker started empty and
  // nothing could be sent until it was filled; that is no longer the shape of the
  // decision, and leaving it would make the common case the one that needs work.
  const [agent, setAgent] = React.useState<OrchestratorAgentId | null>(null);


  const socket = useOrchestratorSocket({ enabled: canDrive && !!projectId });
  const { send: sendTurn, reset: resetSocket } = socket;

  // The chosen model as the RUN's own field. `modelKey` identifies a provider
  // connection + model; `offering_id` is the same thing in the runs API's
  // vocabulary, so it is resolved here rather than sending a bare model id and
  // letting the backend pick whichever connection serves it first.
  const modelOptionsQ = useQuery({
    queryKey: qk.model.options(projectId),
    queryFn: () => getModelOptions(projectId!),
    enabled: !!projectId,
    staleTime: 30_000,
  });
  const offeringId = React.useMemo(() => {
    if (!modelKey) return null;
    const { provider, model_id, credentialId } = splitModelKey(modelKey);
    const options = modelOptionsQ.data?.options ?? [];
    const sameModel = options.filter(
      (o) => o.provider === provider && o.model_id === model_id,
    );
    const exact = credentialId
      ? sameModel.find((o) => o.provider_id === credentialId)
      : undefined;
    return (exact ?? sameModel[0])?.offering_id ?? null;
  }, [modelKey, modelOptionsQ.data]);

  // One run per conversation, created on the first turn and reused after.
  const runIdRef = React.useRef<string | null>(null);
  const creatingRunRef = React.useRef<Promise<string> | null>(null);
  // The same value as `runIdRef`, held in state as well. The ref is what the send
  // path reads synchronously; this is what the Deliverables panel renders against,
  // and a ref alone would never re-render it — the run would exist and the panel
  // would go on showing an empty tab.
  const [runId, setRunId] = React.useState<string | null>(null);

  const ensureRun = React.useCallback(async (): Promise<string> => {
    if (runIdRef.current) return runIdRef.current;
    // A chat opened from history already HAS a run — its id is the session id — so
    // continuing it must rejoin that run rather than mint a new one. Without this the
    // rail would look right while every reopened conversation silently started over,
    // against a different LangGraph thread and different Deliverables.
    if (openedRunId) {
      runIdRef.current = openedRunId;
      setRunId(openedRunId);
      return openedRunId;
    }
    // Two quick turns must not mint two runs — the second awaits the first.
    if (creatingRunRef.current) return creatingRunRef.current;
    if (!projectId) throw new Error("no project is selected");
    const pending = createRun({
      project_id: projectId,
      offering_id: offeringId,
      // Only when the offering could not be resolved (the options list has not
      // loaded, or the picked model is not among them): the backend resolves a
      // bare model id itself. Null for both means the organization default.
      model_id: offeringId ? null : (modelKey ? splitModelKey(modelKey).model_id : null),
    }).then(({ runId: created }) => {
      runIdRef.current = created;
      setRunId(created);
      return created;
    });
    creatingRunRef.current = pending;
    try {
      return await pending;
    } finally {
      creatingRunRef.current = null;
    }
  }, [projectId, offeringId, modelKey, openedRunId]);

  // A different conversation is a different run and a different transcript.
  //
  // THE PROJECT IS PART OF THE KEY, not a fallback for when there is no session.
  // It was `active?.id ?? projectId`, so with a session open the key was the session
  // id alone — and `handleProjectChange` repoints the active session in place via
  // `retargetSession`, which deliberately keeps that id. Switching project therefore
  // changed the header, the model picker and the artifacts panel while this key stood
  // still, so the reset below never ran: the next turn went out with the PREVIOUS
  // project's `run_id`.
  //
  // That is not a privilege leak — the caller administers both projects and
  // `_project_admin_tier_for_run` is satisfied — but `ws._resolve_run` reads
  // `project_id` from the RUN row and never from the frame, so the turn enforced the
  // old project's offering grant, spent the old project's budget with its BYOK key,
  // and joined the old run's LangGraph thread, under a UI naming the new project.
  const conversationKey = `${active?.id ?? ""}:${projectId ?? ""}`;
  const lastConversationKey = React.useRef(conversationKey);
  React.useEffect(() => {
    if (lastConversationKey.current === conversationKey) return;
    lastConversationKey.current = conversationKey;
    runIdRef.current = null;
    setRunId(null);
    creatingRunRef.current = null;
    setAgent(null);
    setOpenDeliverableId(null);
    resetSocket();
  }, [conversationKey, resetSocket]);

  /** Open a saved chat: adopt its run and replay its transcript. */
  const openSession = React.useCallback(
    async (id: string) => {
      const saved = (historyQ.data ?? []).some((s) => s.id === id);
      if (!saved) {
        // A local draft: the existing selection path, which has no run yet.
        store.getState().selectSession(id);
        setOpenedRunId(null);
        return;
      }
      // Hold the project before switching. A saved chat is not in the local store, so
      // selecting it leaves `active` null — and `projectId` reads through `active`,
      // so without this the project silently becomes null the moment a chat is
      // opened, disabling the composer on the conversation the user just asked for.
      if (projectId) setPendingProjectId(projectId);
      store.getState().selectSession(id);
      setOpenedRunId(id);
      runIdRef.current = id;
      setRunId(id);
      setOpenDeliverableId(null);
      const messages = await getConversationMessages(id);
      socket.hydrate(
        messages.map((m) => ({
          id: m.id,
          role: m.role === "user" ? ("user" as const) : ("agent" as const),
          phase: null,
          content: m.content,
          createdAt: m.created_at ? Date.parse(m.created_at) : Date.now(),
        })),
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [historyQ.data, store, socket.hydrate, projectId],
  );

  // ── Deliverables ───────────────────────────────────────────────────────────
  //
  // What the agents produced on this run: everything the socket has seen this
  // session, plus whatever the run already held before it, replayed over REST so
  // reopening a conversation shows its documents without waiting for another turn.
  const [openDeliverableId, setOpenDeliverableId] = React.useState<string | null>(null);

  const deliverablesQ = useQuery({
    queryKey: ["orchestrator", "deliverables", runId],
    queryFn: async () => {
      const res = await fetch(
        `/api/runs/${encodeURIComponent(runId as string)}/deliverables`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`deliverables read failed: ${res.status}`);
      return DeliverablesRead.parse(await res.json());
    },
    enabled: !!runId,
  });

  // The panel speaks `stage`; the wire speaks `agent`. ONE mapping, here at the
  // boundary — the panel is shared with the still-live Copilot and must not be taught
  // a second vocabulary.
  //
  // Socket rows come FIRST so a document produced this turn wins over the REST
  // snapshot that predates it, and the de-dupe keeps the newer of the two.
  const deliverables = React.useMemo(() => {
    const seen = new Set<string>();
    return [...socket.deliverables, ...(deliverablesQ.data?.deliverables ?? [])]
      .filter((d) => {
        if (seen.has(d.id)) return false;
        seen.add(d.id);
        return true;
      })
      .map((d) => ({
        id: d.id,
        stage: d.agent,
        kind: d.kind,
        title: d.title,
        content: d.content ?? "",
        url: d.url ?? undefined,
        language: d.language ?? undefined,
        source: d.source ?? undefined,
        created_at: d.created_at ?? undefined,
      }));
  }, [socket.deliverables, deliverablesQ.data]);

  // Re-read once a turn finishes, so a deliverable that was persisted but whose
  // frame did not arrive (a reconnect mid-turn) still appears.
  const busy = socket.busy;
  React.useEffect(() => {
    if (!busy && runId) void deliverablesQ.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, runId]);

  const handleSend = React.useCallback(
    (text: string) => {
      if (!projectId) return;
      // `agent` may be null — that is the routed path, and the hook omits the
      // field entirely rather than sending a null the protocol does not declare.
      sendTurn({ text, agent, resolveRunId: ensureRun, modelKey });
    },
    [agent, projectId, sendTurn, ensureRun, modelKey],
  );

  // ── Access ────────────────────────────────────────────────────────────────
  if (!hasPermission(session, "artifact:view")) {
    return (
      <RestrictedAccess description="The Orchestrator requires access to project artifacts." />
    );
  }

  if (!mounted) {
    return (
      <div className="p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }

  // With no session yet, show the roster the project *would* run rather than an
  // empty rail: before you start anything is precisely when "which agents does
  // this project have, and what is it already holding on" is the question —
  // the read-only control view this rail replaced answered it without needing
  // a run either.
  const stages = active?.stages ?? (project ? freshStages(project.track) : []);
  const trackMeta = project ? TRACK_META[project.track] : null;

  // The composer says WHY it is closed rather than sitting greyed out with no
  // explanation — "nothing happens when I type" was the old cockpit's whole
  // failure mode.
  const composerDisabled = !canDrive || !projectId || socket.busy;
  const composerPlaceholder = !canDrive
    ? "Read-only — only this project's Project Admin can drive the Orchestrator."
    : !projectId
      ? "Pick a project to start."
      : socket.busy
        ? agent
          ? `The ${agentLabel(agent)} agent is working…`
          : "Working…"
        : agent
          ? `Message the ${agentLabel(agent)} agent`
          : "Describe the work — the Orchestrator picks the agent.";

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
          // Saved chats from the server, plus the unsaved draft. The rail stopped
          // being the store and became a view of it.
          sessions={railSessions}
          activeId={active?.id ?? openedRunId}
          onSelect={(id) => void openSession(id)}
          onCreate={() => {
            if (!project) return;
            store.getState().createSession({
              projectId: String(project.id),
              projectName: project.name,
              track: project.track,
              modelKey,
            });
          }}
          onRename={(id, title) => {
            store.getState().renameSession(id, title);
            // A saved chat's title lives on the server; a draft's does not exist there
            // yet, so the call is best-effort and the rail refetches either way.
            void renameConversation(id, title).catch(() => {}).finally(() => {
              void historyQ.refetch();
            });
          }}
          onDelete={(id) => {
            store.getState().deleteSession(id);
            if (openedRunId === id) {
              setOpenedRunId(null);
              runIdRef.current = null;
              setRunId(null);
              resetSocket();
            }
            void deleteConversation(id).catch(() => {}).finally(() => {
              void historyQ.refetch();
            });
          }}
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

          {/* WHICH AGENT — an OVERRIDE, defaulting to letting the Orchestrator
              choose. "Let the Orchestrator choose" is a real, visible, selected
              option rather than an empty control the reader has to interpret: the
              common case must not look like an unanswered question. Picking one of
              the nine forces it for that turn and skips routing entirely. Ordered
              as the protocol lists them; nothing about that order implies a
              sequence. */}
          <Select
            value={agent ?? AUTO}
            onValueChange={(v) => setAgent(v === AUTO ? null : (v as OrchestratorAgentId))}
            disabled={!canDrive || !projectId}
          >
            <SelectTrigger
              aria-label="Agent"
              className="border-line-soft bg-surface-1 h-8 w-auto min-w-[190px] max-w-[260px] gap-2 px-3 text-[12.5px] font-normal"
            >
              <span className="flex min-w-0 items-center gap-2">
                <Bot className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                <SelectValue placeholder="Let the Orchestrator choose" />
              </span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={AUTO} className="text-[12.5px]">
                Let the Orchestrator choose
              </SelectItem>
              {ORCHESTRATOR_AGENT_IDS.map((id) => (
                <SelectItem key={id} value={id} className="text-[12.5px]">
                  {agentLabel(id)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </header>

        {/* Silent while the scope is still resolving — "you cannot drive this"
            is an assertion, and asserting it before the answer has arrived
            reads as an access denial that then disappears. */}
        {!canDrive && scopeReady && (
          <div className="border-line-soft text-muted-foreground flex shrink-0 items-start gap-2 border-b px-4 py-2 text-[12.5px] md:px-6">
            <Info className="mt-px size-4 shrink-0" aria-hidden />
            <p>
              Read-only — the Orchestrator reaches every agent on this project at once, so
              only its Project Admin may drive it.{" "}
              {projectId ? (
                <Link
                  href={`/projects/${projectId}`}
                  className="text-brand-bright underline underline-offset-2"
                >
                  Run the agent you own from the project
                </Link>
              ) : (
                "Run the agent you own from the project"
              )}{" "}
              instead.
            </p>
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <Thread
            messages={socket.messages}
            // No Stop affordance: this engine has no cancel, and a button that
            // does nothing is worse than no button. The composer simply waits.
            busy={false}
            disabled={composerDisabled}
            placeholder={composerPlaceholder}
            onSend={handleSend}
            onStop={() => {}}
            footerSlot={
              <ThreadFooter
                error={socket.error}
                connState={socket.connState}
                canDrive={canDrive}
              />
            }
            emptySlot={
              <EmptyThread
                projectName={project?.name ?? null}
                trackLabel={trackMeta ? `Track ${trackMeta.number} · ${trackMeta.label}` : null}
                agentCount={stages.length}
                agentChosen={!!agent}
              />
            }
          />

          <div className={cn("hidden", variant === "page" ? "xl:block" : "lg:block")}>
            <ArtifactsPanel
              // The REAL run, not "". The panel resolves the Development code tree
              // and every per-agent file tree against this id, so an empty string
              // rendered a permanently empty tree — indistinguishable from a repo the
              // agent had failed to pull.
              runId={runId ?? ""}
              // Drives which group is expanded AND the Development code tree, which
              // the panel synthesises only while Development is the active agent.
              // This was "" too, so that tree never appeared at all.
              activeStage={socket.activeAgent ?? ""}
              // What its agents produced. This was a literal [] — a declared
              // interface with no data behind it, which on screen is exactly what an
              // agent that produced nothing looks like.
              tabLabel="Deliverables"
              artifacts={deliverables}
              openArtifactId={openDeliverableId}
              onSelectArtifact={setOpenDeliverableId}
              streamingArtifactId={null}
              // The live feed: which agent was chosen and why, what it is thinking,
              // and every tool it runs. `tool.call` has been declared in the protocol
              // and rendered by this panel from the start, but nothing emitted it and
              // nothing recorded it, so the tab was wired to a literal `[]` — a
              // declared interface with no data behind it, which on screen is
              // indistinguishable from an agent that never uses tools.
              activity={socket.activity}
              working={socket.busy}
              connectionStatus={socket.connState}
              collapsed={artifactsCollapsed}
              onToggle={() => setArtifactsCollapsed((v) => !v)}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The strip above the composer.
 *
 * An `error` event is rendered HERE as well as in the thread, because a failure
 * that only reaches the console is a failure the user experiences as silence —
 * the exact behaviour this engine was rebuilt to remove.
 */
function ThreadFooter({
  error,
  connState,
  canDrive,
}: {
  error: string | null;
  connState: OrchestratorConnState;
  canDrive: boolean;
}) {
  if (error) {
    return (
      <div className="border-destructive/40 bg-destructive/[0.06] text-destructive mx-4 mb-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-[12.5px] md:mx-6">
        <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
        <span>{error}</span>
      </div>
    );
  }
  if (!canDrive) return null;
  if (connState === "connecting" || connState === "reconnecting") {
    return (
      <p className="text-muted-foreground mx-4 mb-2 text-[11.5px] md:mx-6">
        {connState === "connecting" ? "Connecting…" : "Reconnecting…"}
      </p>
    );
  }
  return null;
}

function EmptyThread({
  projectName,
  trackLabel,
  agentCount,
  agentChosen,
}: {
  projectName: string | null;
  trackLabel: string | null;
  agentCount: number;
  agentChosen: boolean;
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
            roster.{" "}
            {agentChosen
              ? "Describe the work and the agent you picked will answer here."
              : "Describe the work — the Orchestrator picks the agent, and says which and why. Pick one yourself above to override it."}
          </>
        ) : (
          "Choose a project and one of the models it is allowed to run on, then describe the work."
        )}
      </p>
    </div>
  );
}
