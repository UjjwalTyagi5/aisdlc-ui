"use client";

import { create } from "zustand";

import { agentsForTrack } from "@/lib/tracks";
import type { DeliveryTrack } from "@/lib/schemas/enums";
import type {
  OrchestratorSession,
  SessionStatus,
  StageRun,
  StageRunStatus,
} from "@/lib/orchestrator/types";

/**
 * Orchestrator UI state — which chat is selected, and the unsaved draft.
 *
 * NOT the history. Until Phase 5B this store WAS the rail: sessions and their whole
 * transcripts lived in localStorage, and the rail said so on screen. That could not
 * satisfy "every run belongs to the project" (spec §5.3) — a chat survived neither a
 * cleared browser nor a change of device, and none of it was auditable.
 *
 * Chats are now server-side, listed through `lib/api/conversations` and keyed by run
 * id. What is left here is genuinely local: the draft chat that has not been sent yet
 * (a run, and therefore a session, is minted by the first turn — D23) and which row
 * the rail has selected.
 *
 * TRANSCRIPTS ARE NOT KEPT HERE. A local copy would be a second version of the same
 * conversation with nothing keeping the two in step.
 *
 * EXISTING localStorage SESSIONS ARE NOT MIGRATED. They are per-browser, hold no run
 * id for turns that were never sent, and cannot be attributed to a user server-side.
 * Migrating a shape that can no longer be produced is the mistake ruling R13 already
 * recorded.
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

  // ── Run mechanics, called by the engine ──────────────────────────────────
  setStatus: (id: string, status: SessionStatus) => void;
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
          // A draft has no run yet, so repointing it is free; a chat that HAS run is
          // saved server-side and is never retargeted (the cockpit starts a new one),
          // so this only ever sees an untouched draft.
          title: projectName,
          modelKey,
          messages: [],
          stages: freshStages(track),
          status: "idle",
        })),
      ),

    setModelKey: (id, modelKey) => set(mapSession(id, (s) => ({ ...s, modelKey }))),

    setStatus: (id, status) => set(mapSession(id, (s) => ({ ...s, status }))),
}),
);

/** Read one session without subscribing to the whole list. */
export const useSession_ = (id: string | null) =>
  useOrchestratorStore((s) => s.sessions.find((x) => x.id === id) ?? null);

export { freshStages };
