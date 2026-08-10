"use client";

import * as React from "react";

import { GATE_POLICY, PHASE_LABEL } from "@/lib/agents";
import {
  autoApprovalNote,
  conversationalReply,
  gatePrompt,
  openingTurn,
  stageArtifacts,
  stageTurn,
  type StageScriptContext,
} from "@/lib/orchestrator/script";
import type { OrchestratorMessage } from "@/lib/orchestrator/types";
import { orchestratorUid, readSession, useOrchestratorStore } from "@/stores/orchestrator-store";
import type { DeliveryTrack, Phase } from "@/lib/schemas/enums";

/** Reveal cadence — fast enough to feel live, slow enough to read. */
const CHUNK_MS = 45;
const WORDS_PER_CHUNK = 4;
/** Beat between one stage finishing and the next starting. */
const HANDOFF_MS = 550;

interface Token {
  cancelled: boolean;
}

const sleep = (ms: number, token: Token) =>
  new Promise<void>((resolve) => {
    const id = setTimeout(resolve, ms);
    // A cancelled run should not keep the tab awake waiting out its own beat.
    const poll = setInterval(() => {
      if (token.cancelled) {
        clearTimeout(id);
        clearInterval(poll);
        resolve();
      }
    }, 60);
    setTimeout(() => clearInterval(poll), ms + 10);
  });

export interface UseOrchestratorArgs {
  sessionId: string | null;
  projectName: string;
  track: DeliveryTrack;
  /** Human label of the selected model, e.g. "claude-opus-5". */
  modelLabel: string;
  modelKey: string | null;
}

export interface OrchestratorControls {
  /** Kick off (or restart) the full roster from stage 0. */
  start: (objective?: string) => void;
  /** Continue from wherever the run paused. */
  resume: () => void;
  /** Halt the run where it stands; stage state is kept. */
  stop: () => void;
  /** Decide the gate the run is parked on. */
  decideGate: (decision: "approved" | "rejected") => void;
  /** Talk to the Orchestrator without disturbing the sequence. */
  send: (text: string) => void;
  /** True while a turn is actively streaming. */
  busy: boolean;
}

/**
 * The auto-sequencer.
 *
 * Implemented as one cancellable async driver rather than a chain of effects:
 * the sequence is inherently linear ("run stage, close gate, hand off"), and
 * expressing it as a loop keeps that shape readable and makes cancellation a
 * single flag instead of a teardown per stage.
 *
 * State lives in the store, not in this hook, so a session survives navigation
 * and the thread can be rendered by any component that reads the store. The
 * driver therefore reads through `readSession` (non-reactive) — closing over
 * a rendered snapshot would make every await resume against stale state.
 */
export function useOrchestrator({
  sessionId,
  projectName,
  track,
  modelLabel,
  modelKey,
}: UseOrchestratorArgs): OrchestratorControls {
  const store = useOrchestratorStore;
  const tokenRef = React.useRef<Token | null>(null);
  const [busy, setBusy] = React.useState(false);

  /**
   * The session to act on, resolved when the action fires rather than when the
   * hook rendered.
   *
   * The `sessionId` prop is one render behind whenever a control creates the
   * session it then drives — "Run the pipeline" on an empty page does exactly
   * that — and a `start()` closed over the pre-creation `null` silently does
   * nothing. Reading the store at call time is the only version of this that
   * cannot go stale.
   */
  const currentId = React.useCallback(
    () => store.getState().activeSessionId,
    [store],
  );

  // The objective the run was started with, replayed into every stage's script.
  const objectiveRef = React.useRef("");

  const ctx = React.useCallback(
    (): StageScriptContext => ({
      projectName,
      objective: objectiveRef.current,
      modelLabel,
    }),
    [projectName, modelLabel],
  );

  const cancel = React.useCallback(() => {
    if (tokenRef.current) tokenRef.current.cancelled = true;
    tokenRef.current = null;
  }, []);

  // Abandon the run when the user switches to a *different* session, or the
  // page unmounts — a driver still writing into a session nobody is watching
  // would append turns to a transcript that is no longer on screen.
  //
  // Deliberately not `useEffect(() => cancel, [sessionId])`: starting a run
  // from the empty state creates the session, which moves `sessionId` from
  // null to an id and would fire that cleanup — killing the run one chunk
  // after it began. Only a change *between two real sessions* is a switch.
  const drivenRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (drivenRef.current && drivenRef.current !== sessionId) cancel();
    drivenRef.current = sessionId;
  }, [sessionId, cancel]);
  React.useEffect(() => cancel, [cancel]);

  /** Append a message, then reveal `content` into it word-group by word-group. */
  const streamMessage = React.useCallback(
    async (
      id: string,
      token: Token,
      base: Omit<OrchestratorMessage, "id" | "createdAt" | "content">,
      content: string,
    ) => {
      const msgId = orchestratorUid();
      store.getState().appendMessage(id, {
        ...base,
        id: msgId,
        createdAt: Date.now(),
        content: "",
      });

      const words = content.split(/(\s+)/);
      let acc = "";
      for (let i = 0; i < words.length; i += WORDS_PER_CHUNK * 2) {
        if (token.cancelled) return msgId;
        acc += words.slice(i, i + WORDS_PER_CHUNK * 2).join("");
        store.getState().patchMessage(id, msgId, { content: acc });
        await sleep(CHUNK_MS, token);
      }
      if (!token.cancelled) store.getState().patchMessage(id, msgId, { content });
      return msgId;
    },
    [store],
  );

  /**
   * Run stages from `from` until the roster ends or a gate stops us.
   *
   * Returns nothing — everything it decides is written to the store, because
   * the UI reads the run from there and a return value would be a second,
   * divergent source of truth.
   */
  const drive = React.useCallback(
    async (id: string, from: number, token: Token) => {
      setBusy(true);
      try {
        const stageCount = readSession(id)?.stages.length ?? 0;

        for (let i = from; i < stageCount; i++) {
          if (token.cancelled) return;

          const session = readSession(id);
          if (!session) return;
          const stage = session.stages[i];
          if (!stage) return;

          store.getState().setCursor(id, i);
          store.getState().setStatus(id, "running");
          store.getState().patchStage(id, i, {
            status: "running",
            startedAt: Date.now(),
            finishedAt: null,
            artifacts: [],
          });

          await streamMessage(
            id,
            token,
            { role: "agent", phase: stage.phase, modelKey },
            stageTurn(stage.phase, ctx()),
          );
          if (token.cancelled) return;

          store.getState().patchStage(id, i, {
            artifacts: stageArtifacts(stage.phase),
            finishedAt: Date.now(),
          });

          // ── The gate ────────────────────────────────────────────────────
          const gate = GATE_POLICY[stage.phase];
          // A mandatory checkpoint cannot be waived (PRD §13), so the
          // sequencer never closes one — not even with auto-advance on.
          const mustAsk = gate.mandatory || !readSession(id)?.autoAdvance;

          if (mustAsk) {
            store.getState().patchStage(id, i, { status: "awaiting_gate" });
            await streamMessage(
              id,
              token,
              {
                role: "agent",
                phase: stage.phase,
                modelKey,
                gate: { phase: stage.phase, mandatory: gate.mandatory },
              },
              gatePrompt(stage.phase),
            );
            if (token.cancelled) return;
            store.getState().setStatus(id, "paused");
            return;
          }

          store.getState().patchStage(id, i, { status: "approved" });
          store.getState().appendMessage(id, {
            id: orchestratorUid(),
            role: "system",
            phase: stage.phase,
            content: autoApprovalNote(stage.phase),
            createdAt: Date.now(),
          });

          await sleep(HANDOFF_MS, token);
        }

        if (token.cancelled) return;
        store.getState().setCursor(id, stageCount);
        store.getState().setStatus(id, "complete");
        store.getState().appendMessage(id, {
          id: orchestratorUid(),
          role: "agent",
          phase: null,
          modelKey,
          content: `Run complete — every agent in **${projectName}**'s roster has handed off and all gates are closed. Artifacts from each stage are listed in the rail.`,
          createdAt: Date.now(),
        });
      } finally {
        setBusy(false);
      }
    },
    [store, streamMessage, ctx, modelKey, projectName],
  );

  const launch = React.useCallback(
    (id: string, from: number) => {
      cancel();
      const token: Token = { cancelled: false };
      tokenRef.current = token;
      void drive(id, from, token);
    },
    [cancel, drive],
  );

  const start = React.useCallback(
    (objective = "") => {
      const sid = currentId();
      if (!sid) return;
      objectiveRef.current = objective;
      cancel();
      store.getState().resetRun(sid, track);

      const token: Token = { cancelled: false };
      tokenRef.current = token;

      void (async () => {
        setBusy(true);
        try {
          if (objective.trim()) {
            store.getState().appendMessage(sid, {
              id: orchestratorUid(),
              role: "user",
              phase: null,
              content: objective.trim(),
              createdAt: Date.now(),
            });
          }
          const roster = readSession(sid)?.stages.map((s) => s.phase) ?? [];
          await streamMessage(
            sid,
            token,
            { role: "agent", phase: null, modelKey },
            openingTurn(ctx(), roster as Phase[]),
          );
        } finally {
          setBusy(false);
        }
        if (!token.cancelled) await drive(sid, 0, token);
      })();
    },
    [currentId, cancel, store, track, streamMessage, ctx, modelKey, drive],
  );

  const resume = React.useCallback(() => {
    const sid = currentId();
    if (!sid) return;
    const session = readSession(sid);
    if (!session) return;
    // Resuming onto an open gate would silently skip it; the gate control is
    // the only way past.
    const parked = session.stages[session.cursor];
    if (parked?.status === "awaiting_gate") return;
    launch(sid, session.cursor);
  }, [currentId, launch]);

  const stop = React.useCallback(() => {
    const sid = currentId();
    if (!sid) return;
    cancel();
    setBusy(false);
    store.getState().setStatus(sid, "paused");
  }, [currentId, cancel, store]);

  const decideGate = React.useCallback(
    (decision: "approved" | "rejected") => {
      const sessionId = currentId();
      if (!sessionId) return;
      const session = readSession(sessionId);
      if (!session) return;
      const i = session.cursor;
      const stage = session.stages[i];
      if (!stage || stage.status !== "awaiting_gate") return;

      // Close the open gate message so it stops rendering as a live decision.
      const open = [...session.messages].reverse().find((m) => m.gate && !m.gate.decided);
      if (open?.gate) {
        store.getState().patchMessage(sessionId, open.id, {
          gate: { ...open.gate, decided: decision },
        });
      }

      if (decision === "rejected") {
        store.getState().patchStage(sessionId, i, { status: "rejected" });
        store.getState().setStatus(sessionId, "paused");
        store.getState().appendMessage(sessionId, {
          id: orchestratorUid(),
          role: "system",
          phase: stage.phase,
          content: `${PHASE_LABEL[stage.phase]} gate rejected. The run is stopped here — nothing downstream ran. Restart the run once the stage is reworked.`,
          createdAt: Date.now(),
        });
        return;
      }

      store.getState().patchStage(sessionId, i, { status: "approved" });
      store.getState().appendMessage(sessionId, {
        id: orchestratorUid(),
        role: "system",
        phase: stage.phase,
        content: `${PHASE_LABEL[stage.phase]} gate approved. Handing off.`,
        createdAt: Date.now(),
      });
      launch(sessionId, i + 1);
    },
    [currentId, store, launch],
  );

  const send = React.useCallback(
    (text: string) => {
      const trimmed = text.trim();
      const sessionId = currentId();
      if (!sessionId || !trimmed) return;

      const session = readSession(sessionId);
      // Nothing has run yet → the first thing you type IS the objective.
      if (session && session.messages.length === 0) {
        start(trimmed);
        return;
      }

      store.getState().appendMessage(sessionId, {
        id: orchestratorUid(),
        role: "user",
        phase: null,
        content: trimmed,
        createdAt: Date.now(),
      });

      // A side conversation must not cancel the sequence, so it gets its own
      // token and writes alongside whatever the driver is doing.
      const token: Token = { cancelled: false };
      void streamMessage(
        sessionId,
        token,
        { role: "agent", phase: null, modelKey },
        conversationalReply(trimmed, ctx()),
      );
    },
    [currentId, store, streamMessage, ctx, modelKey, start],
  );

  return { start, resume, stop, decideGate, send, busy };
}
