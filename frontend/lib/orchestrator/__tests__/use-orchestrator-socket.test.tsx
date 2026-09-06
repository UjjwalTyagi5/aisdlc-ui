// @vitest-environment jsdom
import * as React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOrchestratorSocket } from "@/lib/orchestrator/use-orchestrator-socket";

/**
 * The frame-validation contract.
 *
 * `OrchestratorEvent` was pinned in Phase 1 for exactly one reason: an inbound
 * frame the UI cannot type must never reach component state. A gate event the
 * Orchestrator has no way to action, an agent id that does not exist, a chunk
 * whose content is not text — each of those is a backend bug, and rendering it
 * anyway turns the bug into something the user has to interpret. So every frame
 * goes through `safeParse` and a failure is DROPPED, silently and completely.
 *
 * These tests drive the real hook over a stub socket, because the drop has to
 * hold where it actually matters — between `onmessage` and `setMessages` — not
 * in a helper that a future refactor could route around.
 */

interface StubSocket {
  url: string;
  readyState: number;
  sent: string[];
  onopen: (() => void) | null;
  onmessage: ((e: { data: string }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  send: (raw: string) => void;
  close: () => void;
}

let sockets: FakeWebSocket[] = [];

class FakeWebSocket implements StubSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    sockets.push(this);
  }

  send(raw: string) {
    this.sent.push(raw);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }

  /** Complete the handshake, as the server accepting the connection would. */
  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  /** Deliver one raw frame, exactly as the browser would. */
  deliver(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

const socket = () => sockets[0]!;

beforeEach(() => {
  sockets = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({ ticket: "t-1", wsUrl: "ws://localhost:8004/sdlc/agent/orchestrator2/ws" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Mount the hook and wait until its socket has finished the handshake. */
async function mount() {
  const rendered = renderHook(() => useOrchestratorSocket({ enabled: true }));
  await waitFor(() => expect(sockets).toHaveLength(1));
  await act(async () => {
    socket().open();
  });
  return rendered;
}

const deliver = async (frame: unknown) => {
  await act(async () => {
    socket().deliver(frame);
  });
};

describe("useOrchestratorSocket — inbound frame validation", () => {
  it("mints a ticket and opens the socket with it, never a JWT", async () => {
    const { result } = await mount();
    expect(socket().url).toBe(
      "ws://localhost:8004/sdlc/agent/orchestrator2/ws?ticket=t-1",
    );
    expect(result.current.connState).toBe("connected");
  });

  /**
   * LOAD-BEARING CASES. Every frame here reaches a switch branch that WOULD put
   * something on screen if `safeParse` were removed — a bubble, a system line, an
   * error banner. Delete the guard and these fail, which is the only way a
   * validation test earns its place.
   */
  it.each([
    // An agent that does not exist. Without the guard this announces
    // "undefined agent is answering" — a name for an agent that is not there.
    ["agent.selected naming an unknown agent", { type: "agent.selected", agent: "marketing" }],
    ["agent.selected naming no agent at all", { type: "agent.selected", reason: "because" }],
    // Right type, wrong payload: both of these become bubble text unguarded.
    ["a chunk whose content is an object", { type: "stream_chunk", content: { nope: 1 } }],
    ["a chunk whose content is a number", { type: "stream_chunk", content: 42 }],
    // Opens an agent bubble unguarded, so a nameless tool spins the thread.
    ["a tool call with no name", { type: "tool.call", status: "running" }],
    ["a tool call with an invented status", { type: "tool.call", name: "grep", status: "exploded" }],
  ])("drops %s", async (_label, frame) => {
    const { result } = await mount();
    await deliver(frame);
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.activeAgent).toBeNull();
    expect(result.current.error).toBeNull();
  });

  /**
   * These reach no branch, so they would also "pass" with the guard deleted. They
   * are kept as a statement of intent rather than as proof: `gate.state` in
   * particular must stay droppable, because the Orchestrator has no gates and the
   * day one arrives is the day someone is tempted to render it.
   */
  it.each([
    ["a gate event", { type: "gate.state", stage: "design", owner_role: "architect", can_approve: true }],
    ["a stage.changed event", { type: "stage.changed", stage: "design" }],
    ["a bare string", "hello"],
    ["null", null],
  ])("also drops %s, which the UI has no branch for", async (_label, frame) => {
    const { result } = await mount();
    await deliver(frame);
    expect(result.current.messages).toHaveLength(0);
  });

  it("does not let a malformed stream_end close a turn that is still running", async () => {
    const { result } = await mount();
    await act(async () => {
      result.current.send({
        text: "go",
        agent: "testing",
        resolveRunId: async () => "run-1",
      });
    });
    expect(result.current.busy).toBe(true);

    // `session_id` must be a string. Unguarded this reaches `finalizeTurn` and
    // hands the composer back while the agent is still answering.
    await deliver({ type: "stream_end", session_id: 42 });
    expect(result.current.busy).toBe(true);

    await deliver({ type: "stream_end" });
    expect(result.current.busy).toBe(false);
  });

  it("keeps serving valid frames after dropping a malformed one", async () => {
    const { result } = await mount();

    await deliver({ type: "agent.selected", agent: "not_an_agent" });
    expect(result.current.messages).toHaveLength(0);

    await deliver({ type: "agent.selected", agent: "plan", reason: "You asked for a schedule." });
    await deliver({ type: "stream_chunk", content: "Here is the plan." });
    await deliver({ type: "stream_end" });

    // The announcement names the agent — and names it the way a user knows it.
    expect(result.current.activeAgent).toBe("plan");
    const announcement = result.current.messages.find((m) => m.role === "system");
    expect(announcement?.content).toContain("Project Manager");
    expect(announcement?.content).not.toContain("Plan agent");

    const agentTurn = result.current.messages.find((m) => m.role === "agent");
    expect(agentTurn?.content).toBe("Here is the plan.");
  });

  it("does not let a malformed error frame invent an error banner", async () => {
    const { result } = await mount();
    await deliver({ type: "error", message: 42 });
    expect(result.current.error).toBeNull();
    expect(result.current.messages).toHaveLength(0);
  });
});

describe("useOrchestratorSocket — a routed turn's bubble attribution", () => {
  /**
   * THE CENTRAL NEW PATH OF THE BRANCH, and it had no test at all.
   *
   * Found by the whole-branch review: every existing test that delivers
   * `agent.selected` either never called `send`, or called it with an explicit
   * `agent` — so `turnAgentRef` was already correct before the frame landed and the
   * re-attribution was a no-op. Two mutations therefore survived the whole suite:
   * deleting the re-attribution outright, and having the UI GUESS an agent when the
   * user named none.
   */
  const runId = "3f6b0b7e-1a8a-4b3d-8a2c-0f1e2d3c4b5a";

  it("leaves the bubble unattributed until the server names the agent", async () => {
    // With no override there is nothing to attribute the reply to yet. A guess here
    // would show one agent's badge and then swap it for another mid-answer, which
    // reads as a bug in the answer rather than as routing working.
    const { result } = await mount();
    await act(async () => {
      result.current.send({ text: "I need a PRD", resolveRunId: async () => runId });
    });
    await deliver({ type: "stream_chunk", content: "Working on it" });

    const bubble = result.current.messages.find((m) => m.role === "agent");
    expect(bubble).toBeDefined();
    expect(bubble!.phase).toBeNull();
  });

  it("re-attributes the live bubble when agent.selected arrives", async () => {
    const { result } = await mount();
    await act(async () => {
      result.current.send({ text: "I need a PRD", resolveRunId: async () => runId });
    });
    await deliver({ type: "stream_chunk", content: "Drafting" });
    expect(result.current.messages.find((m) => m.role === "agent")!.phase).toBeNull();

    await deliver({
      type: "agent.selected",
      agent: "requirements",
      reason: "You asked for a PRD.",
    });

    await waitFor(() =>
      expect(result.current.messages.find((m) => m.role === "agent")!.phase).toBe(
        "requirements",
      ),
    );
  });
});

describe("useOrchestratorSocket — the activity feed", () => {
  it("records a tool call and closes it when the result arrives", async () => {
    // `tool.call` was declared in the protocol and rendered by the Activity tab from
    // the start, while nothing emitted it and this hook accepted it and kept nothing.
    // The tab was wired to an empty array, which on screen is indistinguishable from
    // an agent that never uses tools.
    const { result } = await mount();

    await deliver({ type: "tool.call", name: "read_repo", status: "running" });
    await waitFor(() => expect(result.current.activity).toHaveLength(1));
    expect(result.current.activity[0]).toMatchObject({
      kind: "tool",
      label: "read_repo",
      status: "running",
    });

    await deliver({ type: "tool.call", name: "read_repo", status: "done" });
    await waitFor(() => expect(result.current.activity[0]!.status).toBe("done"));
    expect(result.current.activity).toHaveLength(1);
  });

  it("announces which agent was chosen, and why", async () => {
    const { result } = await mount();
    await deliver({
      type: "agent.selected",
      agent: "requirements",
      reason: "You asked for a PRD.",
    });
    await waitFor(() => expect(result.current.activity).toHaveLength(1));
    expect(result.current.activity[0]!.kind).toBe("stage");
    expect(result.current.activity[0]!.label).toContain("You asked for a PRD.");
  });

  it("leaves nothing spinning after the turn ends", async () => {
    // A tool left "running" past `stream_end` reads as an agent still working, which
    // is exactly the state the Activity tab's status line exists to report honestly.
    const { result } = await mount();
    await deliver({ type: "tool.call", name: "run_tests", status: "running" });
    await deliver({ type: "agent.thinking", delta: "" });
    await deliver({ type: "stream_end" });

    await waitFor(() =>
      expect(
        result.current.activity.filter((a) => a.status === "running"),
      ).toHaveLength(0),
    );
  });
});

describe("useOrchestratorSocket — turns", () => {
  it("names the agent on the wire and sends the resolved run id", async () => {
    const { result } = await mount();

    await act(async () => {
      result.current.send({
        text: "  draft the requirements  ",
        agent: "requirements",
        resolveRunId: async () => "3f6b0b7e-1a8a-4b3d-8a2c-0f1e2d3c4b5a",
      });
    });

    await waitFor(() => expect(socket().sent).toHaveLength(1));
    expect(JSON.parse(socket().sent[0]!)).toEqual({
      type: "user_message",
      text: "draft the requirements",
      agent: "requirements",
      run_id: "3f6b0b7e-1a8a-4b3d-8a2c-0f1e2d3c4b5a",
    });
  });

  it("OMITS the agent key entirely when there is no override", async () => {
    // Not `agent: null`, and not `agent: ""`. The backend reads
    // `(msg.get("agent") or "").strip()`, so any falsy value would route correctly —
    // but `protocol.ts` declares `agent?: OrchestratorAgentId`, and sending a field
    // shaped differently from the declared contract is how frames start being
    // dropped. This union has already silently lost one event type that way.
    const { result } = await mount();

    await act(async () => {
      result.current.send({
        text: "I need a PRD",
        resolveRunId: async () => "3f6b0b7e-1a8a-4b3d-8a2c-0f1e2d3c4b5a",
      });
    });

    await waitFor(() => expect(socket().sent).toHaveLength(1));
    const frame = JSON.parse(socket().sent[0]!);
    expect(frame).toEqual({
      type: "user_message",
      text: "I need a PRD",
      run_id: "3f6b0b7e-1a8a-4b3d-8a2c-0f1e2d3c4b5a",
    });
    expect("agent" in frame).toBe(false);
  });

  it("surfaces a server error instead of swallowing it", async () => {
    const { result } = await mount();

    await act(async () => {
      result.current.send({
        text: "go",
        agent: "security",
        resolveRunId: async () => "run-1",
      });
    });
    await deliver({ type: "error", message: "That run is not available." });

    expect(result.current.error).toBe("That run is not available.");
    expect(
      result.current.messages.some(
        (m) => m.role === "system" && m.content === "That run is not available.",
      ),
    ).toBe(true);
    // The turn ended: no spinner is left behind, and the composer is free again.
    expect(result.current.busy).toBe(false);
    expect(result.current.messages.some((m) => m.role === "agent")).toBe(false);
  });

  it("says the turn did not finish when the socket drops mid-turn", async () => {
    const { result } = await mount();

    await act(async () => {
      result.current.send({ text: "go", agent: "design", resolveRunId: async () => "run-1" });
    });
    await waitFor(() => expect(socket().sent).toHaveLength(1));

    await act(async () => {
      socket().onclose?.();
    });

    // The frame is already gone down a dead socket and no reconnect will replay
    // it, so the composer comes back AND the thread says why.
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toMatch(/dropped before this turn finished/i);
    expect(result.current.messages.some((m) => m.role === "agent")).toBe(false);
  });

  /**
   * A turn queued before the handshake finished must not run twice.
   *
   * `send` queues into the outbox when the socket is not OPEN yet, and
   * `flushOutbox` replays the queue on the next open. `onclose` used to leave
   * that queue alone while telling the user "send it again once the connection is
   * back" — so doing exactly what the UI asked ran the agent twice. These are the
   * real delivery agents: two pushes to Azure DevOps, two release artifacts, two
   * work items, from one message.
   */
  it("does not replay a queued turn after telling the user to send it again", async () => {
    const { result } = renderHook(() => useOrchestratorSocket({ enabled: true }));
    await waitFor(() => expect(sockets).toHaveLength(1));
    const first = socket();
    // Deliberately NOT opened: this is the window the outbox exists for.
    expect(first.readyState).toBe(FakeWebSocket.CONNECTING);

    await act(async () => {
      result.current.send({
        text: "ship it",
        agent: "development",
        resolveRunId: async () => "run-1",
      });
    });
    await waitFor(() => expect(result.current.busy).toBe(true));
    expect(first.sent).toHaveLength(0); // queued, not sent

    // The connection drops before the handshake ever completes.
    await act(async () => {
      first.onclose?.();
    });
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toMatch(/never ran/i);
    expect(result.current.error).toMatch(/send it again/i);

    // The reconnect succeeds. Nothing may ride it.
    await waitFor(() => expect(sockets).toHaveLength(2), { timeout: 4_000 });
    await act(async () => {
      sockets[1]!.open();
    });
    expect(sockets[1]!.sent).toHaveLength(0);
    expect(first.sent).toHaveLength(0);

    // And doing what the message said produces exactly one run.
    await act(async () => {
      result.current.send({
        text: "ship it",
        agent: "development",
        resolveRunId: async () => "run-1",
      });
    });
    await waitFor(() => expect(sockets[1]!.sent).toHaveLength(1));
    expect(JSON.parse(sockets[1]!.sent[0]!)).toMatchObject({
      type: "user_message",
      text: "ship it",
      agent: "development",
      run_id: "run-1",
    });
  }, 10_000);

  /**
   * The narrower window the first duplicate fix missed.
   *
   * `send` cannot build a frame until `resolveRunId` comes back, and that is a
   * run-creation POST. If the socket dies DURING it, the close handler sees an
   * empty outbox — the frame exists nowhere yet — so clearing the outbox achieves
   * nothing, and the continuation then pushes onto it afterwards for the
   * reconnect to replay. Same duplicate run, one await later.
   *
   * A test that drops the socket before or after the await passes against that
   * bug. This one has to hold the await open.
   */
  it("does not enqueue a turn whose connection died while its run was being created", async () => {
    const { result } = renderHook(() => useOrchestratorSocket({ enabled: true }));
    await waitFor(() => expect(sockets).toHaveLength(1));
    const first = socket();
    await act(async () => {
      first.open();
    });

    // A run-creation round-trip held open on purpose.
    let releaseRun: (id: string) => void = () => {};
    const runId = new Promise<string>((resolve) => {
      releaseRun = resolve;
    });

    await act(async () => {
      result.current.send({
        text: "ship it",
        agent: "development",
        resolveRunId: () => runId,
      });
    });
    await waitFor(() => expect(result.current.busy).toBe(true));
    expect(first.sent).toHaveLength(0); // still resolving; nothing exists to send

    // The connection dies mid-resolve.
    await act(async () => {
      first.onclose?.();
    });
    expect(result.current.busy).toBe(false);
    // And it is described accurately: never sent, therefore never ran. The old
    // wording called this "sent but unfinished".
    expect(result.current.error).toMatch(/never ran/i);

    // The run resolves — after the connection it belonged to is gone.
    await act(async () => {
      releaseRun("run-1");
      await runId;
    });

    // The reconnect must carry nothing.
    await waitFor(() => expect(sockets).toHaveLength(2), { timeout: 4_000 });
    await act(async () => {
      sockets[1]!.open();
    });
    expect(sockets[1]!.sent).toHaveLength(0);
    expect(first.sent).toHaveLength(0);

    // One failure, reported once — not a second line from the late continuation.
    expect(
      result.current.messages.filter((m) => m.role === "system" && /never ran/i.test(m.content)),
    ).toHaveLength(1);
  }, 10_000);

  /**
   * The same window across an `enabled` toggle — the cockpit clears the project
   * and re-picks it. Nothing reports the turn in that path, so the late
   * continuation has to both refuse to enqueue AND release the composer itself.
   */
  it("does not enqueue a turn whose run resolved while the socket was disabled", async () => {
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useOrchestratorSocket({ enabled }),
      { initialProps: { enabled: true } },
    );
    await waitFor(() => expect(sockets).toHaveLength(1));
    await act(async () => {
      socket().open();
    });

    let releaseRun: (id: string) => void = () => {};
    const runId = new Promise<string>((resolve) => {
      releaseRun = resolve;
    });
    await act(async () => {
      result.current.send({
        text: "scan it",
        agent: "security",
        resolveRunId: () => runId,
      });
    });
    await waitFor(() => expect(result.current.busy).toBe(true));

    await act(async () => {
      rerender({ enabled: false });
    });
    await act(async () => {
      releaseRun("run-1");
      await runId;
    });
    await act(async () => {
      rerender({ enabled: true });
    });

    await waitFor(() => expect(sockets).toHaveLength(2));
    await act(async () => {
      sockets[1]!.open();
    });

    expect(sockets[1]!.sent).toHaveLength(0);
    // Nobody else spoke for this turn, so the continuation had to.
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toMatch(/never ran/i);
  }, 10_000);

  /**
   * The regression this test exists for: `send` set `busy` and queued the frame,
   * but when the connection never opened at all, nothing ever cleared `busy` —
   * so the composer sat disabled reading "the X agent is working…" forever, with
   * no agent and no socket. A dead connection wearing the costume of work in
   * progress is the exact failure this engine was rebuilt to end.
   */
  it("ends the turn when the connection gives up, rather than claiming the agent is still working", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          throw new Error("network is down");
        }),
      );

      const { result } = renderHook(() => useOrchestratorSocket({ enabled: true }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(sockets).toHaveLength(0);

      await act(async () => {
        result.current.send({
          text: "go",
          agent: "deployment",
          resolveRunId: async () => "run-1",
        });
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current.busy).toBe(true);

      // Burn through every backoff (1+2+4+8+8s) and the give-up that follows.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });

      expect(result.current.connState).toBe("closed");
      expect(result.current.busy).toBe(false);
      expect(result.current.error).toMatch(/never ran/i);
      expect(
        result.current.messages.some((m) => m.role === "system" && /never ran/i.test(m.content)),
      ).toBe(true);
      // The empty bubble that was standing in for the reply is gone too.
      expect(result.current.messages.some((m) => m.role === "agent")).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a failure when no run could be created, and sends nothing", async () => {
    const { result } = await mount();

    await act(async () => {
      result.current.send({
        text: "go",
        agent: "design",
        resolveRunId: async () => {
          throw new Error("Budget exceeded for this project");
        },
      });
    });

    await waitFor(() =>
      expect(result.current.error).toContain("Budget exceeded for this project"),
    );
    expect(socket().sent).toHaveLength(0);
    expect(result.current.busy).toBe(false);
  });
});

// Keep React in scope for the classic JSX transform some toolchains fall back to.
void React;
