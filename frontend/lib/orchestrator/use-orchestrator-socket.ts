"use client";

import * as React from "react";
import { z } from "zod";

import { API_BASE } from "@/lib/api/client";
import {
  OrchestratorEvent,
  type OrchestratorAgentId,
  type OrchestratorUserMessage,
} from "@/lib/orchestrator/protocol";
import type { CopilotActivityItem } from "@/lib/copilot/use-copilot";
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
 *  · It never names an agent for you. `agent` is an OPTIONAL override; omitted,
 *    the ENGINE routes and says which agent it chose and why. What this hook must
 *    not do is guess one locally to fill the gap — the choice and its reason have
 *    to come from the server, or the badge on the bubble would be the UI's opinion
 *    rather than the agent that actually ran.
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
  /**
   * Which agent runs this turn — an override. Omit it (or pass null) to let the
   * Orchestrator choose, which is the default the cockpit sends.
   */
  agent?: OrchestratorAgentId | null;
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
  /**
   * The live agent-action feed for the Activity tab: which agent was chosen, and
   * every tool it runs.
   *
   * `agent.thinking` has a branch too, but NOTHING IN `orchestrator2` EMITS ONE —
   * the socket forwards `agent.selected`, `stream_chunk`, `tool.call`, `error` and
   * `stream_end`, and that is the whole list. The branch is kept for the same reason
   * `choice.card`'s is: the protocol union accepts the event, so the moment a graph
   * produces one it would otherwise be validated, accepted and silently discarded.
   * It is named here as unreachable rather than advertised as something the feed
   * carries, because claiming it does is how an empty tab looks like a working one.
   *
   * `tool.call` was declared in the protocol and consumed by the panel from the
   * start, but nothing emitted it and nothing recorded it — the tab was wired to an
   * empty array. A declared interface with no data behind it looks exactly like an
   * agent that never uses tools.
   */
  activity: CopilotActivityItem[];
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
  const [activity, setActivity] = React.useState<CopilotActivityItem[]>([]);
  // Tool-call id → activity row, so the `done` event patches the row its `running`
  // opened instead of appending a second one. Keyed on the tool NAME, which is what
  // both events carry; a tool used twice in one turn therefore reuses its row, which
  // is the same trade `lib/copilot/use-copilot.ts` makes.
  const toolActivityIdRef = React.useRef<Map<string, string>>(new Map());
  // One "Thinking…" row per turn, closed when the turn ends.
  const thinkingActivityIdRef = React.useRef<string | null>(null);

  const wsRef = React.useRef<WebSocket | null>(null);
  const outboxRef = React.useRef<OrchestratorUserMessage[]>([]);
  // `busy` as the socket's callbacks can read it: they fire from timers and
  // event handlers that closed over an older render.
  const busyRef = React.useRef(false);
  /**
   * WHICH CONNECTION A TURN BELONGS TO.
   *
   * `send` resolves the run over the network before it can build a frame, so its
   * continuation resumes at a moment the socket it was dispatched for may no
   * longer exist. The counter is captured before that await and re-checked after:
   * if it has moved, the frame belongs to a connection that is gone and must not
   * be enqueued for the next one. Every site that discards the outbox bumps it.
   */
  const connectionGenRef = React.useRef(0);
  /**
   * Did THIS turn's frame actually reach the wire?
   *
   * The close handler used to infer this from "is the outbox non-empty", which is
   * false during the run-creation round-trip — the frame exists nowhere yet — so a
   * turn that had never been sent was reported as one that had. One turn is in
   * flight at a time (the composer is disabled for the duration), so one flag says it.
   */
  const turnSentRef = React.useRef(false);
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

  const pushActivity = React.useCallback(
    (item: Omit<CopilotActivityItem, "id" | "ts">): string => {
      const id = nextId("act");
      setActivity((prev) => [
        ...prev,
        { ...item, id, ts: new Date().toISOString() },
      ]);
      return id;
    },
    [nextId],
  );

  const patchActivity = React.useCallback(
    (id: string, patch: Partial<CopilotActivityItem>) => {
      setActivity((prev) =>
        prev.map((item) => (item.id === id ? { ...item, ...patch } : item)),
      );
    },
    [],
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
    // Close this turn's open activity rows. A "Thinking…" or a tool left spinning
    // after the turn ended reads as an agent still working, which is the "stuck"
    // state the Activity tab's status line is there to report honestly.
    if (thinkingActivityIdRef.current) {
      patchActivity(thinkingActivityIdRef.current, { status: "done" });
      thinkingActivityIdRef.current = null;
    }
    for (const id of toolActivityIdRef.current.values()) {
      patchActivity(id, { status: "done" });
    }
    toolActivityIdRef.current.clear();
    busyRef.current = false;
    setBusy(false);
    const id = streamingIdRef.current;
    streamingIdRef.current = null;
    if (!id) return;
    setMessages((prev) => prev.filter((m) => !(m.id === id && m.content.length === 0)));
  }, [patchActivity]);

  /** Surface a failure in the thread AND as a banner, then end the turn. */
  const failTurn = React.useCallback(
    (message: string) => {
      setError(message);
      appendSystem(message);
      finalizeTurn();
    },
    [appendSystem, finalizeTurn],
  );

  /**
   * Retire the current connection: nothing queued for it may reach another one.
   *
   * Two things, and they have to happen together. Emptying the outbox handles the
   * frame that is already queued; bumping the generation handles the frame that is
   * not queued YET — the one sitting in a `send` continuation waiting on run
   * creation, which would otherwise push onto the outbox after this ran and ride
   * the next socket. Clearing the array alone closed the first door and left the
   * second one open.
   */
  const retireConnection = React.useCallback(() => {
    outboxRef.current = [];
    connectionGenRef.current += 1;
  }, []);

  /**
   * The connection is not coming back — say so, and END ANY TURN IT WAS CARRYING.
   *
   * Without the second half, a socket that never opened left `busy` set forever:
   * the composer stayed disabled reading "the X agent is working…" while nothing
   * was running and nothing ever would. That is a dead connection wearing the
   * costume of work in progress — the precise failure shape this engine was
   * rebuilt to eliminate, and an error banner beside a composer still claiming
   * the agent is working does not undo it.
   */
  const abandonConnection = React.useCallback(() => {
    const hadTurnInFlight = busyRef.current || outboxRef.current.length > 0;
    const neverSent = !turnSentRef.current;
    retireConnection();
    if (hadTurnInFlight) {
      failTurn(
        neverSent
          ? "The connection to the Orchestrator dropped, so this turn never ran. " +
              "Reload the page to reconnect."
          : "The connection to the Orchestrator dropped before this turn finished. " +
              "Reload the page to reconnect.",
      );
      return;
    }
    setError("Lost the connection to the Orchestrator. Reload the page to reconnect.");
  }, [failTurn, retireConnection]);

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
          pushActivity({
            kind: "stage",
            label: evt.reason
              ? `${agentLabel(evt.agent)} — ${evt.reason}`
              : `${agentLabel(evt.agent)} is answering`,
          });
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
          // UNREACHABLE TODAY — nothing in `orchestrator2` emits this; see the
          // `activity` docstring above. Kept, and kept working, so that a graph that
          // starts emitting one is rendered rather than dropped.
          //
          // No token yet, but the agent is working — open the bubble so the thread
          // shows a working indicator instead of nothing.
          mutateBubble((m) => m);
          if (!thinkingActivityIdRef.current) {
            thinkingActivityIdRef.current = pushActivity({
              kind: "thinking",
              label: "Thinking…",
              status: "running",
            });
          }
          break;
        case "tool.call": {
          // Opens the bubble (the agent is working) AND records the action, which is
          // what the Activity tab renders.
          mutateBubble((m) => m);
          const known = toolActivityIdRef.current.get(evt.name);
          if (evt.status === "running") {
            if (known) {
              patchActivity(known, { status: "running" });
            } else {
              toolActivityIdRef.current.set(
                evt.name,
                pushActivity({ kind: "tool", label: evt.name, status: "running" }),
              );
            }
          } else if (known) {
            patchActivity(known, { status: "done" });
            toolActivityIdRef.current.delete(evt.name);
          } else {
            // A `done` with no `running` before it — a provider that never announced
            // the start. Recorded rather than dropped: the tool did run.
            pushActivity({ kind: "tool", label: evt.name, status: "done" });
          }
          break;
        }
        case "choice.card":
          // UNREACHABLE TODAY — `orchestrator2/ws.py` forwards five event types
          // and this is not one of them. Kept, and kept to one line, because the
          // protocol union accepts it: the moment an agent's graph emits one it
          // would otherwise be validated, accepted, and then silently discarded
          // by a missing branch, which is the failure mode this hook exists to
          // prevent. There is nothing to answer a card WITH (the socket accepts
          // `user_message` only), so the prompt is shown and answered in prose.
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
    [
      appendChunk,
      appendSystem,
      failTurn,
      finalizeTurn,
      mutateBubble,
      patchActivity,
      pushActivity,
    ],
  );

  // The connect effect must not re-run just because a handler's identity changed —
  // reopening the socket mid-turn would drop the stream. It reads the latest
  // handler through a ref instead.
  const handleEventRef = React.useRef(handleEvent);
  handleEventRef.current = handleEvent;

  /**
   * Send whatever was dispatched before the handshake finished.
   *
   * The outbox covers ONE window: a turn dispatched while its socket is still
   * CONNECTING. A frame can only flush onto the connection generation it was
   * enqueued for, and that is enforced in two halves because a frame can be
   * invisible at the moment a connection dies:
   *
   *   · already queued  → `retireConnection` empties the array.
   *   · not queued yet, still waiting on run creation → `send`'s continuation
   *     re-checks the generation before it pushes, and refuses if it moved.
   *
   * The first half alone is what the previous version claimed was sufficient. It
   * was not: run creation is a network round-trip, so a socket that dies during
   * it left an empty outbox to clear and a frame that arrived afterwards.
   */
  const flushOutbox = React.useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (outboxRef.current.length === 0) return;
    for (const frame of outboxRef.current) ws.send(JSON.stringify(frame));
    outboxRef.current = [];
    turnSentRef.current = true;
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
        abandonConnection();
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
          // A closed socket must stop being "the" socket, so a frame can never be
          // handed to a dead generation's `wsRef`. Guarded on identity: a late
          // close from an older socket must not null the one that replaced it.
          if (wsRef.current === ws) wsRef.current = null;
          if (cancelled || closedByUnmount.current) return;

          // RETIRE THIS CONNECTION, before the message below tells the user to
          // send the turn again.
          //
          // A frame that has not left the browser — queued, or still waiting on
          // run creation — would otherwise ride the reconnect via `flushOutbox`.
          // Combined with "send it again" that runs the agent TWICE, and these
          // are the real delivery agents: a duplicate push to Azure DevOps, a
          // duplicate release artifact, a duplicate work item, caused by doing
          // exactly what the UI asked. Retiring the generation is what makes
          // "send it again" mean one run.
          //
          // The alternative — replay it and say nothing — was rejected because
          // the turn would then arrive after the thread has already said it did
          // not run, attached to a composer the user has moved on from. Silence
          // about work that is still coming is the shape of failure this engine
          // exists to remove.
          //
          // `turnSentRef` decides the wording, NOT the outbox's length. During
          // the run-creation round-trip the frame is in neither place, so length
          // reported "sent but unfinished" about a turn that had never been sent.
          const neverSent = !turnSentRef.current;
          retireConnection();

          // A turn cannot survive the socket that was carrying it. Release the
          // composer, and say which of the two things happened — finalizing
          // quietly would leave the user's own message on screen with no reply
          // and no reason.
          if (busyRef.current) {
            failTurn(
              neverSent
                ? "The connection dropped before this turn was sent, so it never ran. " +
                    "Send it again once the connection is back."
                : "The connection dropped before this turn finished. Send it again once " +
                    "the connection is back.",
            );
          }
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
      // Same retirement as a close: `enabled` can go false and true again (the
      // project is cleared and re-picked), and a turn still resolving its run
      // across that gap must not enqueue onto the socket that comes back.
      retireConnection();
      streamingIdRef.current = null;
    };
    // Every callback here is a stable `useCallback`, so listing them cannot
    // churn the socket; `enabled` is the only thing that reopens it.
  }, [enabled, flushOutbox, failTurn, abandonConnection, retireConnection]);

  // ── Actions ───────────────────────────────────────────────────────────────
  const send = React.useCallback(
    ({ text, agent = null, resolveRunId, modelKey = null }: OrchestratorTurnInput) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setError(null);
      busyRef.current = true;
      setBusy(true);

      // Echo the turn immediately, then open the reply bubble attributed to the
      // agent the USER picked. `agent.selected` confirms it a moment later and
      // re-attributes if the server picked differently.
      //
      // With no override there is nothing to attribute it to YET, and `null` is the
      // honest value: the bubble stays unattributed until `agent.selected` arrives,
      // rather than showing a guess that a moment later turns into a different
      // agent. A wrong badge that corrects itself reads as a bug in the answer.
      turnAgentRef.current = agent;
      turnModelKeyRef.current = modelKey;
      turnSentRef.current = false;
      streamingIdRef.current = null;
      // The connection this turn belongs to, read BEFORE the await below.
      const generation = connectionGenRef.current;
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
        // THE CONNECTION MAY HAVE DIED WHILE THE RUN WAS BEING CREATED.
        //
        // Resolving the run is a network round-trip, so this continuation resumes
        // at a moment that can be on the far side of a close. Enqueueing here
        // regardless is how the duplicate survived a first fix: the close handler
        // found an empty outbox, told the user the turn had not run and to send it
        // again, and this line then pushed the frame for `flushOutbox` to replay
        // onto the reconnected socket. The agent ran twice, because the user did
        // what the UI asked.
        if (connectionGenRef.current !== generation) {
          // Whoever retired the connection has usually already reported it (and
          // cleared `busy`). Only speak if nothing did — otherwise this would be a
          // second failure line for one failure, and a turn abandoned by a path
          // that says nothing at all would leave the composer disabled forever.
          if (busyRef.current) {
            failTurn(
              "The connection dropped before this turn was sent, so it never ran. " +
                "Send it again once the connection is back.",
            );
          }
          return;
        }
        // `agent` is OMITTED, not sent as null, when there is no override — see
        // the field's own comment in protocol.ts.
        const frame: OrchestratorUserMessage = {
          type: "user_message",
          text: trimmed,
          run_id: runId,
          ...(agent ? { agent } : {}),
        };
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify(frame));
          turnSentRef.current = true;
        } else {
          outboxRef.current.push(frame);
        }
      })();
    },
    [appendMessage, failTurn, mutateBubble, nextId],
  );

  const reset = React.useCallback(() => {
    setActivity([]);
    toolActivityIdRef.current.clear();
    thinkingActivityIdRef.current = null;
    setMessages([]);
    setActiveAgent(null);
    setError(null);
    busyRef.current = false;
    setBusy(false);
    streamingIdRef.current = null;
    turnAgentRef.current = null;
    turnModelKeyRef.current = null;
    turnSentRef.current = false;
    // A turn from the conversation being discarded must not surface in the new
    // one: its continuation may still be resolving a run that belongs to a
    // project the user has already navigated away from.
    retireConnection();
  }, [retireConnection]);

  return { messages, send, connState, activeAgent, error, busy, activity, reset };
}
