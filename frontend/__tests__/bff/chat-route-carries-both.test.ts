/**
 * One turn carries BOTH the model choice and the cancel handle — and not each other's.
 *
 * THE BUG THIS CATCHES. `openChatWsBridge` takes its tail as optional positional
 * arguments, and two of them are adjacent and both `string | undefined`:
 *
 *     …, agentParams?, signal?, sessionId?, offeringId?
 *
 * Transposing the last two type-checks perfectly and then fails silently in two
 * directions at once. The turn spends whichever key sorts first alphabetically (the
 * defect this branch exists to fix, back again), and Stop posts a cancel for a
 * conversation id that is really an offering id — so it cancels nothing, while the
 * agent keeps generating and keeps billing.
 *
 * Neither half announces itself. The chat streams, the Stop button greys out, and the
 * only visible symptom is a bill and a model nobody picked. These two features were
 * merged from different branches into the same argument list, which is exactly when
 * this happens, so it is pinned by behaviour rather than by reading the source.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

import type { NextRequest } from "next/server";

// Typed with the parameters it is actually called with, so `mock.calls[0][0]` is a
// string rather than `never` — an untyped `vi.fn()` here type-checks at the call site
// and then makes every assertion about the path unreachable.
const bffFetch = vi.fn(async (_path: string, _init?: unknown): Promise<unknown> => ({}));
const sent: string[] = [];

vi.mock("@/lib/bff/client", () => ({
  bffFetch: (path: string, init?: unknown) => bffFetch(path, init),
}));
vi.mock("@/lib/auth/session", () => ({
  getSession: async () => ({ user: { id: "u1" }, accessToken: "t" }),
}));
vi.mock("@/lib/bff/ws-ticket", () => ({
  mintWsTicket: async () => "ticket-1",
  fastapiWsUrl: () => "ws://test.local",
}));

/**
 * The bridge opens a real WebSocket, which jsdom does not provide and a unit test must
 * not need. This stands in for one: it records what the route sends and never
 * connects. `onopen` fires on the microtask queue so the route's handler is attached
 * before it runs — the real thing is async too.
 */
class FakeWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    queueMicrotask(() => this.onopen?.());
  }
  send(payload: string) {
    sent.push(payload);
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  bffFetch.mockClear();
  sent.length = 0;
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
});

/** A POST the route handler will accept, with a live abort signal attached. */
function chatRequest(body: Record<string, unknown>, signal: AbortSignal): NextRequest {
  return {
    json: async () => body,
    signal,
  } as unknown as NextRequest;
}

/** Lets the microtask queue drain so `onopen` (and the send inside it) has run. */
const settle = () => new Promise((r) => setTimeout(r, 0));

describe("a chat turn carries the model choice and the cancel handle", () => {
  it("sends the OFFERING as offering_id — not the session id", async () => {
    const { POST } = await import("@/app/api/chat/route");
    const ac = new AbortController();

    await POST(
      chatRequest(
        {
          message: "build it",
          sessionId: "sess-abc",
          agent: "development",
          offeringId: "off-azure-1",
        },
        ac.signal,
      ),
    );
    await settle();

    expect(sent).toHaveLength(1);
    const payload = JSON.parse(sent[0]!) as Record<string, unknown>;
    expect(payload.offering_id).toBe("off-azure-1");
    // The transposition would put the session id here and look entirely plausible.
    expect(payload.offering_id).not.toBe("sess-abc");
  });

  it("cancels the SESSION on abort — not the offering", async () => {
    const { POST } = await import("@/app/api/chat/route");
    const ac = new AbortController();

    await POST(
      chatRequest(
        {
          message: "build it",
          sessionId: "sess-abc",
          agent: "development",
          offeringId: "off-azure-1",
        },
        ac.signal,
      ),
    );
    await settle();

    ac.abort();
    await settle();

    const cancels = bffFetch.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.includes("/cancel"));
    expect(cancels).toHaveLength(1);
    expect(cancels[0]).toBe("/conversations/sess-abc/cancel");
  });

  it("still sends no offering_id when the page picked nothing", async () => {
    // Guards the guard: if the payload always carried an offering, the first case
    // would pass on a route that ignores the argument entirely. The org default has
    // to keep working for a page with no picker.
    const { POST } = await import("@/app/api/chat/route");
    const ac = new AbortController();

    await POST(
      chatRequest({ message: "build it", sessionId: "sess-abc", agent: "development" }, ac.signal),
    );
    await settle();

    const payload = JSON.parse(sent[0]!) as Record<string, unknown>;
    expect(payload).not.toHaveProperty("offering_id");
  });
});
