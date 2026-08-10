"use client";

import * as React from "react";

import { API_BASE } from "@/lib/api/client";
import {
  CopilotEvent,
  CopilotTicket,
  type ChoiceCard as ChoiceCardT,
  type CopilotOutbound,
  type GateState,
} from "@/lib/copilot/types";
import {
  ArtifactsRead,
  TranscriptRead,
  rendererFor,
  type Artifact,
} from "@/lib/copilot/artifacts";
import { stageLabel } from "@/lib/copilot/stages";

/** A tool the agent invoked during a turn — rendered as an activity chip. */
export interface CopilotToolActivity {
  name: string;
  status: "running" | "done";
}

/** A rendered turn in the unified Copilot thread. */
export interface CopilotMessage {
  id: string;
  /** "user" bubbles are the driver; "agent" bubbles are attributed to a stage. */
  role: "user" | "agent";
  content: string;
  /** Active stage when the bubble was produced — labels agent bubbles. */
  stage?: string;
  streaming?: boolean;
  createdAt: string;
  /** For answered choice cards echoed into the transcript. */
  card?: ChoiceCardT;
  /** The option ids the user picked — kept so the echoed card stays highlighted. */
  answeredIds?: string[];
  /** Tools the agent called this turn (activity trail, in call order). */
  tools?: CopilotToolActivity[];
  /** Accumulated extended-thinking / reasoning for this turn. */
  thinking?: string;
  /**
   * Marks a one-time "stage output ready → open in panel" affordance. Rendered
   * inline in the thread (scrolls into history) instead of a persistent pinned
   * card. Set only for live `artifact.ready` events, never for hydration replays.
   */
  artifactCard?: { titles: string[]; firstId: string | null };
}

export type CopilotConnState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "closed"
  | "error";

/** One entry in the live agent-action feed (the panel's "Activity" tab). */
export type CopilotActivityKind = "tool" | "thinking" | "stage" | "turn";

export interface CopilotActivityItem {
  id: string;
  ts: string;
  kind: CopilotActivityKind;
  label: string;
  status?: "running" | "done";
}

export interface UseCopilotResult {
  messages: CopilotMessage[];
  streaming: boolean;
  choiceCard: ChoiceCardT | null;
  gate: GateState | null;
  activeStage: string;
  error: string | null;
  connState: CopilotConnState;
  /** Alias of `connState` for the Activity tab / reconnect banner. */
  connectionStatus: CopilotConnState;
  send: (text: string) => void;
  /** Stop the in-flight turn (cooperative cancel over REST; the WS ends the stream). */
  cancel: () => void;
  answerChoice: (cardId: string, selectedIds: string[], freeText?: string) => void;
  sendGateDecision: (decision: "approved" | "rejected", stage: string, reason?: string) => void;
  /** Re-point the run's active stage (Copilot left-rail "work with this agent" jump). */
  setStage: (stage: string) => void;
  /** All artifacts produced/persisted for the run (across stages). */
  artifacts: Artifact[];
  /** The artifact id currently selected in the panel (streaming or manual). */
  openArtifactId: string | null;
  setOpenArtifactId: (id: string | null) => void;
  /** The id of the artifact still receiving `artifact.delta` frames, if any. */
  streamingArtifactId: string | null;
  /** True once any artifact exists — the panel should auto-open. */
  panelOpen: boolean;
  /** Live agent-action feed for the Activity tab (newest last). */
  activity: CopilotActivityItem[];
  /** True the instant a turn is dispatched (before any token/tool/thinking arrives). */
  working: boolean;
  /** True when `working` and no new event has landed for ~25s. */
  stuck: boolean;
  /** Seconds since the last event was observed — drives the "stuck" copy. */
  idleSeconds: number;
}

// Stages whose backend turn-loop streams its own "✓ <stage> ready — open the
// Artifacts panel →" chat bubble (they route a document/report into the panel).
// For these the in-thread artifact-card would duplicate that bubble, so it's
// suppressed; every other stage (requirements/development/testing) has no backend
// bubble and relies on the in-thread card as its single "output ready" affordance.
const BACKEND_READY_CARD_STAGES = new Set([
  "design",
  "code_review",
  "security",
  "deployment",
  "documentation",
]);

const STUCK_THRESHOLD_MS = 25_000;
const RECONNECT_MAX_ATTEMPTS = 5;
const RECONNECT_BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 8_000];

/**
 * Owns the Copilot WebSocket lifecycle for a run.
 *
 * Ticket flow mirrors `use-agent-chat` / `/api/chat`: the browser POSTs the BFF
 * (`/api/copilot/ws-ticket`), which mints a single-use ws-ticket the exact same
 * way the SSE bridges do (`mintWsTicket` → FastAPI `/auth/ws-ticket`) and returns
 * `{ ticket, wsUrl }`. The hook then opens `wsUrl?ticket=<t>&run=<runId>`. The BFF
 * JWT never reaches the browser — only the short-lived (20 s), single-use ticket.
 *
 * Event handling:
 *   stream_chunk  → append `content` to the current streaming agent bubble
 *   stream_end    → finalize the streaming bubble (streaming=false)
 *   choice.card   → set `choiceCard` (rendered inline; cleared on answer)
 *   gate.state    → set `gate`
 *   stage.changed → set `activeStage` (labels subsequent agent bubbles)
 *   error         → surface `error`
 *
 * Reconnect: an UNEXPECTED close (backend restart, network blip — never an
 * intentional unmount/close) re-mints the ticket and reopens with capped
 * backoff (1s→2s→4s→8s, 5 tries). A successful reopen re-hydrates the
 * transcript + artifacts (merge, not replace, so nothing already rendered is
 * lost) and resets the attempt counter. The last known `activeStage` is kept
 * as-is across the gap — the WS corrects it on the next `stage.changed` frame,
 * so nothing needs to be re-derived from a REST call that doesn't share the
 * same stage-id vocabulary.
 */
export function useCopilot(opts: {
  runId: string;
  projectId?: string;
  /** Optional greeting to seed the thread before the first agent turn. */
  initialStage?: string;
  enabled?: boolean;
}): UseCopilotResult {
  const { runId, projectId, initialStage = "requirements", enabled = true } = opts;

  const [messages, setMessages] = React.useState<CopilotMessage[]>([]);
  const [streaming, setStreaming] = React.useState(false);
  const [choiceCard, setChoiceCard] = React.useState<ChoiceCardT | null>(null);
  const [gate, setGate] = React.useState<GateState | null>(null);
  const [activeStage, setActiveStage] = React.useState<string>(initialStage);
  const [error, setError] = React.useState<string | null>(null);
  const [connState, setConnState] = React.useState<CopilotConnState>("idle");

  // ── Artifacts panel state ──────────────────────────────────────────────────
  const [artifacts, setArtifacts] = React.useState<Artifact[]>([]);
  const [openArtifactId, setOpenArtifactId] = React.useState<string | null>(null);
  const [streamingArtifactId, setStreamingArtifactId] = React.useState<string | null>(null);

  // ── Activity feed state (Activity tab) ─────────────────────────────────────
  const [activity, setActivity] = React.useState<CopilotActivityItem[]>([]);
  const [working, setWorking] = React.useState(false);
  const [stuck, setStuck] = React.useState(false);
  const [idleSeconds, setIdleSeconds] = React.useState(0);

  const wsRef = React.useRef<WebSocket | null>(null);
  const activeStageRef = React.useRef<string>(initialStage);
  activeStageRef.current = activeStage;
  // The id of the agent bubble currently accumulating stream_chunk deltas.
  const streamingIdRef = React.useRef<string | null>(null);
  // Outbound frames queued before the socket finishes its handshake.
  const outboxRef = React.useRef<CopilotOutbound[]>([]);
  const closedByUnmount = React.useRef(false);
  // Mirror the live choice card so actions can read it without nesting setState.
  const choiceCardRef = React.useRef<ChoiceCardT | null>(null);
  choiceCardRef.current = choiceCard;
  // Mirror artifacts so the ready-handler can inspect them without a dep cycle.
  const artifactsRef = React.useRef<Artifact[]>(artifacts);
  artifactsRef.current = artifacts;

  // ── Reconnect bookkeeping ───────────────────────────────────────────────────
  const reconnectAttemptsRef = React.useRef(0);
  const reconnectTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Activity feed bookkeeping ────────────────────────────────────────────────
  const activitySeq = React.useRef(0);
  const toolActivityIdRef = React.useRef<Map<string, string>>(new Map());
  const thinkingActivityIdRef = React.useRef<string | null>(null);
  const lastEventAtRef = React.useRef<number>(Date.now());

  const noteEvent = React.useCallback(() => {
    lastEventAtRef.current = Date.now();
  }, []);

  const pushActivity = React.useCallback(
    (item: Omit<CopilotActivityItem, "id" | "ts">) => {
      const id = `act${activitySeq.current++}-${runId}`;
      setActivity((prev) => [...prev, { id, ts: new Date().toISOString(), ...item }]);
      return id;
    },
    [runId],
  );

  const patchActivity = React.useCallback(
    (id: string, patch: Partial<Pick<CopilotActivityItem, "label" | "status">>) => {
      setActivity((prev) => prev.map((a) => (a.id === id ? { ...a, ...patch } : a)));
    },
    [],
  );

  /** Resolve any still-"running" activity rows to "done" — turn boundary cleanup. */
  const finalizeActivity = React.useCallback(() => {
    toolActivityIdRef.current.clear();
    thinkingActivityIdRef.current = null;
    setActivity((prev) =>
      prev.map((a) => (a.status === "running" ? { ...a, status: "done" as const } : a)),
    );
  }, []);

  const flushOutbox = React.useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    for (const frame of outboxRef.current) ws.send(JSON.stringify(frame));
    outboxRef.current = [];
  }, []);

  const sendFrame = React.useCallback(
    (frame: CopilotOutbound) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(frame));
      } else {
        outboxRef.current.push(frame);
      }
    },
    [],
  );

  /**
   * Apply `mut` to the active streaming agent bubble, opening one if none is
   * live yet. Every live signal for a turn — text, tool calls, thinking — flows
   * through here so a tool-only or thinking-only turn still surfaces a bubble
   * (this is why "nothing happened" before: tool activity never opened one).
   */
  const bubbleSeq = React.useRef(0);
  const mutateBubble = React.useCallback((mut: (m: CopilotMessage) => CopilotMessage) => {
    setStreaming(true);
    // Decide the target bubble id OUTSIDE the updater. Mutating a ref inside a
    // setState updater is unsafe: React (StrictMode/dev) invokes updaters twice,
    // and a ref mutated in the throwaway pass makes the committed pass believe the
    // bubble already exists — so it never gets created. Keeping the id decision
    // here makes the updater pure and idempotent under double-invocation.
    let id = streamingIdRef.current;
    if (!id) {
      id = `a${bubbleSeq.current++}-${runId}`;
      streamingIdRef.current = id;
    }
    const bubbleId = id;
    setMessages((prev) => {
      if (prev.some((m) => m.id === bubbleId)) {
        return prev.map((m) => (m.id === bubbleId ? mut(m) : m));
      }
      const base: CopilotMessage = {
        id: bubbleId,
        role: "agent",
        content: "",
        stage: activeStageRef.current,
        streaming: true,
        createdAt: new Date().toISOString(),
      };
      return [...prev, mut(base)];
    });
  }, [runId]);

  const appendChunk = React.useCallback(
    (delta: string) => mutateBubble((m) => ({ ...m, content: m.content + delta })),
    [mutateBubble],
  );

  const appendThinking = React.useCallback(
    (delta: string) =>
      mutateBubble((m) => ({ ...m, thinking: (m.thinking ?? "") + delta })),
    [mutateBubble],
  );

  const noteTool = React.useCallback(
    (name: string, status: "running" | "done") =>
      mutateBubble((m) => {
        const tools = [...(m.tools ?? [])];
        const idx = tools.findIndex((t) => t.name === name);
        if (idx >= 0) tools[idx] = { name, status };
        else tools.push({ name, status });
        return { ...m, tools };
      }),
    [mutateBubble],
  );

  const finalizeStream = React.useCallback(() => {
    setStreaming(false);
    setWorking(false);
    finalizeActivity();
    const id = streamingIdRef.current;
    streamingIdRef.current = null;
    if (!id) return;
    // Any tool still "running" at turn-end resolved — mark it done so no chip spins forever.
    setMessages((prev) =>
      prev.map((m) =>
        m.id === id
          ? {
              ...m,
              streaming: false,
              tools: m.tools?.map((t) => ({ ...t, status: "done" as const })),
            }
          : m,
      ),
    );
  }, [finalizeActivity]);

  // ── Artifact stream handlers ───────────────────────────────────────────────
  const openArtifact = React.useCallback(
    (stage: string, artifactId: string, kind: string, title: string) => {
      const next: Artifact = {
        id: artifactId,
        stage,
        kind: rendererFor(kind),
        title,
        content: "",
      };
      setArtifacts((prev) => {
        const idx = prev.findIndex((a) => a.id === artifactId);
        if (idx >= 0) {
          const copy = prev.slice();
          // Reset the placeholder body but keep any earlier content out of the way.
          copy[idx] = { ...copy[idx]!, ...next, content: copy[idx]!.content ?? "" };
          return copy;
        }
        return [...prev, next];
      });
      setStreamingArtifactId(artifactId);
      // Auto-select the streaming artifact so it shows live in the panel.
      setOpenArtifactId(artifactId);
    },
    [],
  );

  const appendArtifact = React.useCallback((artifactId: string, delta: string) => {
    if (!delta) return;
    setArtifacts((prev) =>
      prev.map((a) => (a.id === artifactId ? { ...a, content: (a.content ?? "") + delta } : a)),
    );
  }, []);

  const endArtifact = React.useCallback((artifactId: string) => {
    setStreamingArtifactId((cur) => (cur === artifactId ? null : cur));
  }, []);

  const readyArtifacts = React.useCallback(
    (stage: string, next: Artifact[]) => {
      setStreamingArtifactId((cur) => {
        // If the finished stream belonged to this stage, it's now superseded.
        const belonged = artifactsRef.current.some((a) => a.id === cur && a.stage === stage);
        return belonged ? null : cur;
      });
      setArtifacts((prev) => {
        // Replace this stage's artifacts with the authoritative parsed list;
        // keep every other stage's artifacts untouched.
        const others = prev.filter((a) => a.stage !== stage);
        return [...others, ...next];
      });
      // Select the first authoritative artifact for the stage if nothing is open
      // or the open one just got replaced.
      setOpenArtifactId((cur) => {
        if (next.length === 0) return cur;
        const stillThere = cur && next.some((a) => a.id === cur);
        return stillThere ? cur : next[0]!.id;
      });
      // Drop a one-time "ready" affordance into the thread so it scrolls into
      // history instead of pinning above the composer. Dedupe against an
      // identical trailing card for the same stage (a stage can emit
      // artifact.ready more than once per turn — e.g. doc + generated files).
      // Skip stages whose backend already streams its OWN "✓ … ready — open the
      // Artifacts panel →" chat bubble (design + the report stages), so those
      // don't get a duplicate card; requirements/development/testing have no
      // backend bubble, so the card here is their single affordance.
      if (next.length > 0 && !BACKEND_READY_CARD_STAGES.has(stage)) {
        const cardId = `artcard-${stage}-${next[0]!.id}-${bubbleSeq.current++}-${runId}`;
        const titles = next.map((a) => a.title);
        const firstId = next[0]!.id;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.artifactCard && last.stage === stage) return prev;
          return [
            ...prev,
            {
              id: cardId,
              role: "agent",
              stage,
              content: "",
              createdAt: new Date().toISOString(),
              artifactCard: { titles, firstId },
            },
          ];
        });
      }
    },
    [runId],
  );

  const handleEvent = React.useCallback(
    (raw: unknown) => {
      const parsed = CopilotEvent.safeParse(raw);
      if (!parsed.success) return; // drop unrecognised frames (defense-in-depth)
      const evt = parsed.data;
      noteEvent();
      switch (evt.type) {
        case "artifact.open":
          openArtifact(evt.stage, evt.artifact_id, evt.kind, evt.title);
          break;
        case "artifact.delta":
          appendArtifact(evt.artifact_id, evt.content);
          break;
        case "artifact.end":
          endArtifact(evt.artifact_id);
          break;
        case "artifact.ready":
          readyArtifacts(evt.stage, evt.artifacts);
          break;
        case "stream_chunk":
          if (evt.content) appendChunk(evt.content);
          break;
        case "tool.call": {
          noteTool(evt.name, evt.status);
          const label = evt.name;
          const existingId = toolActivityIdRef.current.get(evt.name);
          if (evt.status === "running") {
            if (existingId) {
              patchActivity(existingId, { status: "running" });
            } else {
              const id = pushActivity({ kind: "tool", label, status: "running" });
              toolActivityIdRef.current.set(evt.name, id);
            }
          } else {
            if (existingId) {
              patchActivity(existingId, { status: "done" });
              toolActivityIdRef.current.delete(evt.name);
            } else {
              pushActivity({ kind: "tool", label, status: "done" });
            }
          }
          break;
        }
        case "agent.thinking":
          if (evt.delta) appendThinking(evt.delta);
          if (!thinkingActivityIdRef.current) {
            thinkingActivityIdRef.current = pushActivity({
              kind: "thinking",
              label: "Thinking…",
              status: "running",
            });
          }
          break;
        case "stream_end":
          finalizeStream();
          break;
        case "choice.card": {
          setChoiceCard(evt.card);
          break;
        }
        case "gate.state": {
          const { type: _t, ...g } = evt;
          void _t;
          setGate(g);
          break;
        }
        case "stage.changed":
          setActiveStage(evt.stage);
          // A new stage begins a fresh agent bubble; clear any stuck streaming indicator.
          streamingIdRef.current = null;
          setStreaming(false);
          // Advancing past a gate clears it (the approved/rejected gate no longer applies).
          setGate(null);
          pushActivity({ kind: "stage", label: `Stage → ${stageLabel(evt.stage)}` });
          break;
        case "error":
          setError(evt.message ?? evt.detail ?? "The Copilot reported an error.");
          finalizeStream();
          break;
      }
    },
    [
      appendChunk,
      appendThinking,
      noteTool,
      finalizeStream,
      openArtifact,
      appendArtifact,
      endArtifact,
      readyArtifacts,
      noteEvent,
      pushActivity,
      patchActivity,
    ],
  );

  // ── Hydrate a saved run: transcript replay + persisted artifacts ───────────
  // Used on mount AND after a successful reconnect. On mount the thread/artifacts
  // are empty, so this seeds them outright. After a reconnect it MERGES instead
  // of replacing: any artifact the backend produced while the socket was down is
  // upserted by id, and any transcript messages beyond what's already rendered
  // are appended — so a missed window during the gap is filled in without
  // clobbering the live turns already on screen.
  const hydrateTranscript = React.useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/transcript`, {
        credentials: "include",
      });
      if (!res.ok) return;
      const { messages: seeded } = TranscriptRead.parse(await res.json());
      if (seeded.length === 0) return;
      setMessages((prev) => {
        if (prev.length === 0) {
          return seeded.map((m, i) => ({
            id: `replay-${i}-${runId}`,
            role: m.role,
            content: m.content,
            stage: m.stage ?? undefined,
            createdAt: new Date().toISOString(),
          }));
        }
        if (seeded.length <= prev.length) return prev;
        const extra = seeded.slice(prev.length).map((m, i) => ({
          id: `replay-r${prev.length + i}-${runId}-${Date.now()}`,
          role: m.role,
          content: m.content,
          stage: m.stage ?? undefined,
          createdAt: new Date().toISOString(),
        }));
        return [...prev, ...extra];
      });
    } catch {
      // fail-soft: a fresh run has no transcript; the WS drives from empty.
    }
  }, [runId]);

  const hydrateArtifacts = React.useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/artifacts`, {
        credentials: "include",
      });
      if (!res.ok) return;
      const { artifacts: seeded } = ArtifactsRead.parse(await res.json());
      if (seeded.length === 0) return;
      setArtifacts((prev) => {
        if (prev.length === 0) return seeded;
        const byId = new Map(prev.map((a) => [a.id, a] as const));
        for (const a of seeded) byId.set(a.id, a);
        return Array.from(byId.values());
      });
    } catch {
      // fail-soft: no persisted artifacts yet — the panel shows its empty state.
    }
  }, [runId]);

  React.useEffect(() => {
    if (!enabled || !runId) return;
    let cancelled = false;
    (async () => {
      if (!cancelled) await hydrateTranscript();
    })();
    (async () => {
      if (!cancelled) await hydrateArtifacts();
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, runId, hydrateTranscript, hydrateArtifacts]);

  // ── Connect on mount / when the run changes — with auto-reconnect ─────────
  React.useEffect(() => {
    if (!enabled || !runId) return;
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
        setError("Lost connection to the Copilot session. Refresh the page to reconnect.");
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
      const isReconnectAttempt = reconnectAttemptsRef.current > 0;
      setConnState(isReconnectAttempt ? "reconnecting" : "connecting");
      setError(null);
      try {
        const res = await fetch(`${API_BASE}/copilot/ws-ticket`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
        });
        if (!res.ok) throw new Error(`ticket mint failed (${res.status})`);
        const { ticket, wsUrl } = CopilotTicket.parse(await res.json());
        if (cancelled) return;

        const url = `${wsUrl}?ticket=${encodeURIComponent(ticket)}&run=${encodeURIComponent(runId)}`;
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          if (cancelled) return;
          const wasReconnect = reconnectAttemptsRef.current > 0;
          reconnectAttemptsRef.current = 0;
          setConnState("connected");
          flushOutbox();
          if (wasReconnect) {
            // Fill any gap opened while the socket was down, and keep the last
            // known active stage — the next stage.changed frame reconciles it.
            void hydrateTranscript();
            void hydrateArtifacts();
          }
        };
        ws.onmessage = (msg: MessageEvent) => {
          try {
            handleEvent(JSON.parse(msg.data as string));
          } catch {
            // malformed JSON — discard
          }
        };
        ws.onerror = () => {
          if (cancelled || closedByUnmount.current) return;
          setConnState("error");
        };
        ws.onclose = () => {
          if (cancelled || closedByUnmount.current) return;
          setStreaming(false);
          setWorking(false);
          // Unexpected close (backend restart / network blip) — never fires for
          // an intentional unmount (closedByUnmount short-circuits above).
          scheduleReconnect();
        };
      } catch (err) {
        if (cancelled) return;
        void err;
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
  }, [enabled, runId, flushOutbox, handleEvent, hydrateTranscript, hydrateArtifacts]);

  // ── Stuck / working timer ───────────────────────────────────────────────────
  // Ticks every second: drives the "Ns since last event" copy and flips `stuck`
  // once `working` has gone quiet for STUCK_THRESHOLD_MS.
  React.useEffect(() => {
    const t = setInterval(() => {
      const idleMs = Date.now() - lastEventAtRef.current;
      setIdleSeconds(Math.max(0, Math.floor(idleMs / 1000)));
      setStuck(working && idleMs > STUCK_THRESHOLD_MS);
    }, 1000);
    return () => clearInterval(t);
  }, [working]);

  // ── Public actions ───────────────────────────────────────────────────────
  const send = React.useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setError(null);
      setWorking(true);
      noteEvent();
      setMessages((prev) => [
        ...prev,
        {
          id: `u${Date.now()}`,
          role: "user",
          content: trimmed,
          createdAt: new Date().toISOString(),
        },
      ]);
      const preview = trimmed.length > 80 ? `${trimmed.slice(0, 77)}…` : trimmed;
      pushActivity({ kind: "turn", label: `You: ${preview}` });
      // A new user turn opens a fresh agent bubble for the reply.
      streamingIdRef.current = null;
      toolActivityIdRef.current.clear();
      thinkingActivityIdRef.current = null;
      sendFrame({ type: "user_message", text: trimmed, run_id: runId, project_id: projectId });
    },
    [runId, projectId, sendFrame, noteEvent, pushActivity],
  );

  const cancel = React.useCallback(() => {
    // Best-effort Stop: signal the backend over REST (the WS turn is busy
    // streaming and can't read a control frame). The turn ends on its own with a
    // stream_end that finalizes the bubble — no optimistic state mutation here so
    // the thread stays consistent with what the server actually emitted.
    (async () => {
      try {
        await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/copilot/cancel-turn`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
        });
      } catch {
        // signal failed — the turn simply runs to completion
      }
    })();
  }, [runId]);

  const answerChoice = React.useCallback(
    (cardId: string, selectedIds: string[], freeText?: string) => {
      setError(null);
      setWorking(true);
      noteEvent();
      // Freeze the answered card into the transcript, then clear the live slot.
      const cur = choiceCardRef.current;
      if (cur && cur.card_id === cardId) {
        const labels = cur.options
          .filter((o) => selectedIds.includes(o.id))
          .map((o) => o.label);
        const summary =
          freeText?.trim() || (labels.length ? labels.join(", ") : "(no selection)");
        setMessages((prev) => [
          ...prev,
          {
            id: `u${Date.now()}`,
            role: "user",
            content: summary,
            createdAt: new Date().toISOString(),
            card: cur,
            answeredIds: selectedIds,
          },
        ]);
        pushActivity({ kind: "turn", label: `You: ${summary}` });
      }
      setChoiceCard(null);
      streamingIdRef.current = null;
      toolActivityIdRef.current.clear();
      thinkingActivityIdRef.current = null;
      sendFrame({
        type: "choice_answer",
        card_id: cardId,
        selected_ids: selectedIds,
        free_text: freeText,
        run_id: runId,
      });
    },
    [runId, sendFrame, noteEvent, pushActivity],
  );

  const sendGateDecision = React.useCallback(
    (decision: "approved" | "rejected", stage: string, reason?: string) => {
      setError(null);
      setWorking(true);
      noteEvent();
      // Optimistically clear the gate; the WS emits stage.changed + a note on success.
      setGate(null);
      streamingIdRef.current = null;
      toolActivityIdRef.current.clear();
      thinkingActivityIdRef.current = null;
      pushActivity({ kind: "turn", label: `You: ${decision} ${stageLabel(stage)}` });
      sendFrame({ type: "gate.decision", decision, stage, reason, run_id: runId });
    },
    [runId, sendFrame, noteEvent, pushActivity],
  );

  const setStage = React.useCallback(
    (stage: string) => {
      setError(null);
      (async () => {
        try {
          const res = await fetch(
            `${API_BASE}/runs/${encodeURIComponent(runId)}/copilot/set-stage`,
            {
              method: "POST",
              credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ stage }),
            },
          );
          if (!res.ok) {
            setError(`Couldn't switch to ${stageLabel(stage)} (${res.status}).`);
            return;
          }
          const data: unknown = await res.json();
          const nextStage =
            data && typeof data === "object" && "current_stage" in data
              ? (data as { current_stage?: string | null }).current_stage
              : null;
          const resolved = nextStage ?? stage;
          // Treat like a stage.changed frame: fresh bubble, clear gate/choice.
          setActiveStage(resolved);
          streamingIdRef.current = null;
          setStreaming(false);
          setGate(null);
          setChoiceCard(null);
          pushActivity({ kind: "stage", label: `Stage → ${stageLabel(resolved)}` });
        } catch {
          setError(`Couldn't switch to ${stageLabel(stage)} — the request failed.`);
        }
      })();
    },
    [runId, pushActivity],
  );

  return {
    messages,
    streaming,
    choiceCard,
    gate,
    activeStage,
    error,
    connState,
    connectionStatus: connState,
    send,
    cancel,
    answerChoice,
    setStage,
    artifacts,
    openArtifactId,
    setOpenArtifactId,
    streamingArtifactId,
    panelOpen: artifacts.length > 0,
    sendGateDecision,
    activity,
    working,
    stuck,
    idleSeconds,
  };
}
