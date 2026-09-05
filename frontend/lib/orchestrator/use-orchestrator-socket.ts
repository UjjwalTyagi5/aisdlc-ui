"use client";

import * as React from "react";
import { z } from "zod";

import { API_BASE } from "@/lib/api/client";
import {
  OrchestratorEvent,
  type OrchestratorAgentId,
  type OrchestratorUserMessage,
} from "@/lib/orchestrator/protocol";
import { PHASE_FOR_AGENT, agentLabel } from "@/lib/orchestrator/types";
import type { OrchestratorMessage } from "@/lib/orchestrator/types";

/**
 * The Orchestrator's socket — one Project Admin, one named agent per turn.
 *
 * Ticket flow mirrors `lib/copilot/use-copilot.ts`: the browser POSTs the BFF
 * (`/api/orchestrator/ws-ticket`), which mints a single-use ws-ticket server-side
 * and returns `{ ticket, wsUrl }`; the hook then opens `wsUrl?ticket=<t>`. The BFF
 * JWT never reaches the browser — only the short-lived (20 s), single-use ticket.
 * The socket itself resolves the caller's role from the redeemed ticket and refuses
 * anyone who is not a Project Admin before the handshake is accepted, so this hook
 * is not the access decision and must not pretend to be one.
 *
 * WHAT THIS HOOK REFUSES TO DO
 * ----------------------------
 *  · It never invents a run. `run_id` becomes the graph's `thread_id` on a
 *    persistent checkpointer, so the backend resolves it against the caller's
 *    tenant and refuses anything it cannot verify. The caller therefore hands in a
 *    `resolveRunId` that produces a REAL `runs` row (see the cockpit), and the
 *    first turn is what pays for creating one.
 *  · It never names an agent for you. `agent` is required on every turn; Phase 2
 *    has no router, so the pick is the user's and stays visible.
 *  · It never lets an unrecognised frame reach component state. EVERY inbound
 *    frame goes through `OrchestratorEvent.safeParse` and is dropped on failure —
 *    that validation is the whole reason the protocol was pinned. A frame the UI
 *    cannot type is a backend bug, and rendering it anyway is how a gate control
 *    the user cannot action, or an agent that does not exist, ends up on screen.
 *
 * There is no gate, no sign-off, no ordering and no auto-advance here, because
 * there is none in the engine either.
 */

const OrchestratorTicket = z.object({ ticket: z.string(), wsUrl: z.string() });

export type OrchestratorConnState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "closed"
  | "error";

export interface OrchestratorTurnInput {
  text: string;
  /** Which agent runs this turn. Required — the UI makes the user choose. */
  agent: OrchestratorAgentId;
  /**
   * Resolves the REAL run this turn belongs to, awaited before the frame goes out.
   *
   * A callback rather than a value so the run can be created lazily, on the first
   * turn, instead of on page load — merely opening the Orchestrator must not
   * litter a project with empty runs. It may reject; a rejection ends the turn
   * with a visible failure rather than a frame the backend would refuse anyway.
   */
  resolveRunId: () => Promise<string>;
  /** `provider::model_id::credentialId` — attribution on the agent's bubble only. */
  modelKey?: string | null;
}

export interface UseOrchestratorSocketResult {
  messages: OrchestratorMessage[];
  send: (turn: OrchestratorTurnInput) => void;
  connState: OrchestratorConnState;
  /** The agent the LAST `agent.selected` named — the server's word, not the UI's. */
  activeAgent: OrchestratorAgentId | null;
  /** The most recent failure, for a banner. Also appended to the thread. */
  error: string | null;
  /** A turn is in flight: dispatched and not yet ended. */
  busy: boolean;
  /** Drop the transcript — the conversation changed underneath us. */
  reset: () => void;
}

const RECONNECT_MAX_ATTEMPTS = 5;
const RECONNECT_BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 8_000];

export function useOrchestratorSocket(
  opts: { enabled?: boolean } = {},
): UseOrchestratorSocketResult {
  const { enabled = true } = opts;

  const [messages, setMessages] = React.useState<OrchestratorMessage[]>([]);
  const [connState, setConnState] = React.useState<OrchestratorConnState>("idle");
  const [activeAgent, setActiveAgent] = React.useState<OrchestratorAgentId | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const wsRef = React.useRef<WebSocket | null>(null);
  const outboxRef = React.useRef<OrchestratorUserMessage[]>([]);
  const closedByUnmount = React.useRef(false);
  const reconnectAttemptsRef = React.useRef(0);
  const reconnectTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  /** The bubble currently accumulating this turn's tokens. */
  const streamingIdRef = React.useRef<string | null>(null);
  /** Who this turn is attributed to: the user's pick until `agent.selected` speaks. */
  const turnAgentRef = React.useRef<OrchestratorAgentId | null>(null);
  const turnModelKeyRef = React.useRef<string | null>(null);
  const seqRef = React.useRef(0);

  const nextId = React.useCallback((prefix: string) => `${prefix}${seqRef.current++}`, []);

  const appendMessage = React.useCallback((msg: OrchestratorMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  /** A dashed, unattributed line in the thread — announcements and failures. */
  const appendSystem = React.useCallback(
    (content: string) => {
      appendMessage({
        id: nextId("s"),
        role: "system",
        phase: null,
        content,
        createdAt: Date.now(),
      });
    },
    [appendMessage, nextId],
  );

  /**
   * Apply `mut` to this turn's agent bubble, opening one if none is live.
   *
   * The id is decided OUTSIDE the updater on purpose: React invokes updaters twice
   * under StrictMode, and a ref mutated in the throwaway pass makes the committed
   * pass believe the bubble already exists, so it never gets created.
   */
  const mutateBubble = React.useCallback(
    (mut: (m: OrchestratorMessage) => OrchestratorMessage) => {
      let id = streamingIdRef.current;
      if (!id) {
        id = nextId("a");
        streamingIdRef.current = id;
      }
      const bubbleId = id;
      const agent = turnAgentRef.current;
      setMessages((prev) => {
        if (prev.some((m) => m.id === bubbleId)) {
          return prev.map((m) => (m.id === bubbleId ? mut(m) : m));
        }
        const base: OrchestratorMessage = {
          id: bubbleId,
          role: "agent",
          phase: agent ? PHASE_FOR_AGENT[agent] : null,
          content: "",
          createdAt: Date.now(),
          modelKey: turnModelKeyRef.current,
        };
        return [...prev, mut(base)];
      });
    },
    [nextId],
  );

  const appendChunk = React.useCallback(
    (delta: string) => mutateBubble((m) => ({ ...m, content: m.content + delta })),
    [mutateBubble],
  );

  /**
   * End the turn.
   *
   * An agent bubble that never received a token is REMOVED rather than left on
   * screen: with no content the thread renders it as a working indicator, and an
   * indicator for a turn that has already ended is a spinner nothing will resolve.
   * Whatever ended the turn (a `stream_end`, an `error`) has already said so.
   */
  const finalizeTurn = React.useCallback(() => {
    setBusy(false);
    const id = streamingIdRef.current;
    streamingIdRef.current = null;
    if (!id) return;
    setMessages((prev) => prev.filter((m) => !(m.id === id && m.content.length === 0)));
  }, []);

  /** Surface a failure in the thread AND as a banner, then end the turn. */
  const failTurn = React.useCallback(
    (message: string) => {
      setError(message);
      appendSystem(message);
      finalizeTurn();
    },
    [appendSystem, finalizeTurn],
  );

  const handleEvent = React.useCallback(
    (raw: unknown) => {
      // WHERE UNRECOGNISED FRAMES DIE. Anything the pinned union rejects —
      // an agent id that does not exist, a gate event, a chunk whose content is
      // not a string, a type invented since — is dropped here and never becomes
      // component state.
      const parsed = OrchestratorEvent.safeParse(raw);
      if (!parsed.success) return;
      const evt = parsed.data;

      switch (evt.type) {
        case "agent.selected": {
          // Announced in the thread, by name, every time. A wrong pick has to be
          // obvious on the turn it happens — the previous engine chose silently by
          // list index, and a wrong choice was indistinguishable from a bad answer.
          setActiveAgent(evt.agent);
          turnAgentRef.current = evt.agent;
          // Re-attribute the bubble already opened for this turn: the server's
          // choice wins over the one the composer assumed.
          const live = streamingIdRef.current;
          if (live) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === live ? { ...m, phase: PHASE_FOR_AGENT[evt.agent] } : m,
              ),
            );
          }
          const reason = evt.reason.trim();
          appendSystem(
            `${agentLabel(evt.agent)} agent is answering.${reason ? ` ${reason}` : ""}`,
          );
          break;
        }
        case "stream_chunk":
          if (evt.content) appendChunk(evt.content);
          break;
        case "agent.thinking":
          // No token yet, but the agent is working — open the bubble so the thread
          // shows a working indicator instead of nothing.
          mutateBubble((m) => m);
          break;
        case "tool.call":
          mutateBubble((m) => m);
          break;
        case "choice.card":
          // Phase 2's socket accepts `user_message` only, so there is nothing to
          // answer a card WITH. Rendering an inert card would be worse than
          // rendering the question: show the prompt and let the user reply in prose.
          appendSystem(evt.card.prompt);
          break;
        case "stream_end":
          finalizeTurn();
          break;
        case "error":
          failTurn(evt.message ?? evt.detail ?? "The Orchestrator reported an error.");
          break;
        // Artifact events are typed by the protocol but nothing writes artifacts in
        // Phase 2, so there is no panel state to feed. Ignored rather than rendered
        // as an empty panel that would imply an output exists.
        case "artifact.open":
        case "artifact.delta":
        case "artifact.end":
        case "artifact.ready":
          break;
      }
    },
    [appendChunk, appendSystem, failTurn, finalizeTurn, mutateBubble],
  );

  // The connect effect must not re-run just because a handler's identity changed —
  // reopening the socket mid-turn would drop the stream. It reads the latest
  // handler through a ref instead.
  const handleEventRef = React.useRef(handleEvent);
  handleEventRef.current = handleEvent;

  const flushOutbox = React.useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    for (const frame of outboxRef.current) ws.send(JSON.stringify(frame));
    outboxRef.current = [];
  }, []);

  // ── Connect, with capped reconnect ────────────────────────────────────────
  React.useEffect(() => {
    if (!enabled) return;
    closedByUnmount.current = false;
    reconnectAttemptsRef.current = 0;
    let cancelled = false;

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const scheduleReconnect = () => {
      if (cancelled || closedByUnmount.current) return;
      const attempt = reconnectAttemptsRef.current;
      if (attempt >= RECONNECT_MAX_ATTEMPTS) {
        setConnState("closed");
        setError(
          "Lost the connection to the Orchestrator. Reload the page to reconnect.",
        );
        return;
      }
      setConnState("reconnecting");
      const delay = RECONNECT_BACKOFF_MS[Math.min(attempt, RECONNECT_BACKOFF_MS.length - 1)]!;
      reconnectAttemptsRef.current = attempt + 1;
      clearReconnectTimer();
      reconnectTimerRef.current = setTimeout(() => {
        if (!cancelled && !closedByUnmount.current) void connect();
      }, delay);
    };

    const connect = async () => {
      if (cancelled) return;
      setConnState(reconnectAttemptsRef.current > 0 ? "reconnecting" : "connecting");
      try {
        const res = await fetch(`${API_BASE}/orchestrator/ws-ticket`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
        });
        if (!res.ok) throw new Error(`ticket mint failed (${res.status})`);
        const { ticket, wsUrl } = OrchestratorTicket.parse(await res.json());
        if (cancelled) return;

        // No `?run=` — a run is per TURN here, not per socket, and the message
        // carries the one the backend verifies. Pinning a run on the URL would
        // hand the socket a fallback the user never chose.
        const ws = new WebSocket(`${wsUrl}?ticket=${encodeURIComponent(ticket)}`);
        wsRef.current = ws;

        ws.onopen = () => {
          if (cancelled) return;
          reconnectAttemptsRef.current = 0;
          setConnState("connected");
          flushOutbox();
        };
        ws.onmessage = (msg: MessageEvent) => {
          try {
            handleEventRef.current(JSON.parse(msg.data as string));
          } catch {
            // Not JSON at all — discard, exactly as an unparseable frame is.
          }
        };
        ws.onerror = () => {
          if (cancelled || closedByUnmount.current) return;
          setConnState("error");
        };
        ws.onclose = () => {
          if (cancelled || closedByUnmount.current) return;
          // A turn cannot survive the socket that was carrying it — release the
          // composer rather than leaving it disabled behind a dead connection.
          finalizeTurn();
          scheduleReconnect();
        };
      } catch {
        if (cancelled) return;
        scheduleReconnect();
      }
    };

    void connect();

    return () => {
      cancelled = true;
      closedByUnmount.current = true;
      clearReconnectTimer();
      try {
        wsRef.current?.close();
      } catch {
        // already closing
      }
      wsRef.current = null;
      outboxRef.current = [];
      streamingIdRef.current = null;
    };
  }, [enabled, flushOutbox, finalizeTurn]);

  // ── Actions ───────────────────────────────────────────────────────────────
  const send = React.useCallback(
    ({ text, agent, resolveRunId, modelKey = null }: OrchestratorTurnInput) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setError(null);
      setBusy(true);

      // Echo the turn immediately, then open the reply bubble attributed to the
      // agent the USER picked. `agent.selected` confirms it a moment later and
      // re-attributes if the server picked differently.
      turnAgentRef.current = agent;
      turnModelKeyRef.current = modelKey;
      streamingIdRef.current = null;
      appendMessage({
        id: nextId("u"),
        role: "user",
        phase: null,
        content: trimmed,
        createdAt: Date.now(),
      });
      mutateBubble((m) => m);

      void (async () => {
        let runId: string;
        try {
          runId = await resolveRunId();
        } catch (err) {
          failTurn(
            err instanceof Error && err.message
              ? `Couldn't start this turn: ${err.message}`
              : "Couldn't start this turn — no run was available for it.",
          );
          return;
        }
        const frame: OrchestratorUserMessage = {
          type: "user_message",
          text: trimmed,
          agent,
          run_id: runId,
        };
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
        else outboxRef.current.push(frame);
      })();
    },
    [appendMessage, failTurn, mutateBubble, nextId],
  );

  const reset = React.useCallback(() => {
    setMessages([]);
    setActiveAgent(null);
    setError(null);
    setBusy(false);
    streamingIdRef.current = null;
    turnAgentRef.current = null;
    turnModelKeyRef.current = null;
    outboxRef.current = [];
  }, []);

  return { messages, send, connState, activeAgent, error, busy, reset };
}
