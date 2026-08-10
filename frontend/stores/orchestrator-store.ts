"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

import { agentsForTrack } from "@/lib/tracks";
import type { DeliveryTrack } from "@/lib/schemas/enums";
import type {
  OrchestratorMessage,
  OrchestratorSession,
  SessionStatus,
  StageRun,
  StageRunStatus,
} from "@/lib/orchestrator/types";

/**
 * Orchestrator sessions — the history rail on the left of `/orchestrator`.
 *
 * Persisted client-side rather than through the dummy-data seam on purpose. A
 * session is a *conversation*, which is per-user and per-device state, and the
 * seam's in-memory fixtures are shared and reset on reload; worse, a Next route
 * write and an MSW-handled read never share memory (see the dual-runtime rule),
 * so a session created here would not be readable there. localStorage sidesteps
 * both and is the honest home for chat history in a frontend-only build.
 */

const uid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

function freshStages(track: DeliveryTrack): StageRun[] {
  return agentsForTrack(track).map((phase) => ({
    phase,
    status: "pending" as StageRunStatus,
    artifacts: [],
    startedAt: null,
    finishedAt: null,
  }));
}

interface OrchestratorState {
  sessions: OrchestratorSession[];
  activeSessionId: string | null;

  createSession: (input: {
    projectId: string;
    projectName: string;
    track: DeliveryTrack;
    modelKey: string | null;
  }) => string;
  selectSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;

  /** Repoint an existing session at a different project — resets the run. */
  retargetSession: (
    id: string,
    input: { projectId: string; projectName: string; track: DeliveryTrack; modelKey: string | null },
  ) => void;
  setModelKey: (id: string, modelKey: string | null) => void;
  setAutoAdvance: (id: string, on: boolean) => void;

  // ── Run mechanics, called by the engine ──────────────────────────────────
  appendMessage: (id: string, msg: OrchestratorMessage) => void;
  patchMessage: (id: string, msgId: string, patch: Partial<OrchestratorMessage>) => void;
  patchStage: (id: string, index: number, patch: Partial<StageRun>) => void;
  setStatus: (id: string, status: SessionStatus) => void;
  setCursor: (id: string, cursor: number) => void;
  /** Clear the transcript and stage states, keeping the session identity. */
  resetRun: (id: string, track: DeliveryTrack) => void;
}

/** Apply `fn` to one session and stamp `updatedAt`. */
const mapSession =
  (id: string, fn: (s: OrchestratorSession) => OrchestratorSession) =>
  (state: { sessions: OrchestratorSession[] }) => ({
    sessions: state.sessions.map((s) =>
      s.id === id ? { ...fn(s), updatedAt: Date.now() } : s,
    ),
  });

export const useOrchestratorStore = create<OrchestratorState>()(
  persist(
    (set) => ({
      sessions: [],
      activeSessionId: null,

      createSession: ({ projectId, projectName, track, modelKey }) => {
        const id = uid();
        const now = Date.now();
        const session: OrchestratorSession = {
          id,
          title: projectName,
          projectId,
          modelKey,
          createdAt: now,
          updatedAt: now,
          messages: [],
          stages: freshStages(track),
          cursor: 0,
          autoAdvance: true,
          status: "idle",
        };
        set((s) => ({ sessions: [session, ...s.sessions], activeSessionId: id }));
        return id;
      },

      selectSession: (id) => set({ activeSessionId: id }),

      deleteSession: (id) =>
        set((s) => {
          const sessions = s.sessions.filter((x) => x.id !== id);
          return {
            sessions,
            activeSessionId:
              s.activeSessionId === id ? (sessions[0]?.id ?? null) : s.activeSessionId,
          };
        }),

      renameSession: (id, title) =>
        set(mapSession(id, (s) => ({ ...s, title: title.trim() || s.title }))),

      retargetSession: (id, { projectId, projectName, track, modelKey }) =>
        set(
          mapSession(id, (s) => ({
            ...s,
            projectId,
            // Only rename while the session is still untouched — renaming a
            // session someone has already run would lose the label they know it by.
            title: s.messages.length === 0 ? projectName : s.title,
            modelKey,
            messages: [],
            stages: freshStages(track),
            cursor: 0,
            status: "idle",
          })),
        ),

      setModelKey: (id, modelKey) => set(mapSession(id, (s) => ({ ...s, modelKey }))),

      setAutoAdvance: (id, on) => set(mapSession(id, (s) => ({ ...s, autoAdvance: on }))),

      appendMessage: (id, msg) =>
        set(mapSession(id, (s) => ({ ...s, messages: [...s.messages, msg] }))),

      patchMessage: (id, msgId, patch) =>
        set(
          mapSession(id, (s) => ({
            ...s,
            messages: s.messages.map((m) => (m.id === msgId ? { ...m, ...patch } : m)),
          })),
        ),

      patchStage: (id, index, patch) =>
        set(
          mapSession(id, (s) => ({
            ...s,
            stages: s.stages.map((st, i) => (i === index ? { ...st, ...patch } : st)),
          })),
        ),

      setStatus: (id, status) => set(mapSession(id, (s) => ({ ...s, status }))),

      setCursor: (id, cursor) => set(mapSession(id, (s) => ({ ...s, cursor }))),

      resetRun: (id, track) =>
        set(
          mapSession(id, (s) => ({
            ...s,
            messages: [],
            stages: freshStages(track),
            cursor: 0,
            status: "idle",
          })),
        ),
    }),
    {
      name: "orchestrator-sessions",
      version: 1,
      /**
       * A run that was mid-flight when the tab closed has no driver any more,
       * so it must not rehydrate as "running" — that would render a spinner
       * nothing will ever resolve. Park it as paused, which the UI offers a
       * Resume for.
       */
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        state.sessions = state.sessions.map((s) =>
          s.status === "running" ? { ...s, status: "paused" as SessionStatus } : s,
        );
      },
    },
  ),
);

/** Read one session without subscribing to the whole list. */
export const useSession_ = (id: string | null) =>
  useOrchestratorStore((s) => s.sessions.find((x) => x.id === id) ?? null);

export const orchestratorUid = uid;
export { freshStages };

/** Non-reactive read, for the engine's async driver (avoids stale closures). */
export const readSession = (id: string): OrchestratorSession | null =>
  useOrchestratorStore.getState().sessions.find((s) => s.id === id) ?? null;
