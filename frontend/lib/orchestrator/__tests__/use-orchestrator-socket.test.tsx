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

let sockets: StubSocket[] = [];

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

const socket = () => sockets[0] as FakeWebSocket;

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

  it.each([
    // An agent that does not exist — the union names all nine and only those.
    ["agent.selected naming an unknown agent", { type: "agent.selected", agent: "marketing" }],
    // The Orchestrator has no gates. One arriving is a backend bug, not a control.
    ["a gate event", { type: "gate.state", stage: "design", owner_role: "architect", can_approve: true }],
    // A type invented since this UI shipped.
    ["an unknown event type", { type: "stage.changed", stage: "design" }],
    // Right type, wrong payload.
    ["a chunk whose content is not text", { type: "stream_chunk", content: { nope: 1 } }],
    ["a tool call with no name", { type: "tool.call", status: "running" }],
    // Not an event at all.
    ["a bare string", "hello"],
    ["null", null],
  ])("drops %s", async (_label, frame) => {
    const { result } = await mount();
    await deliver(frame);
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.activeAgent).toBeNull();
    expect(result.current.error).toBeNull();
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
