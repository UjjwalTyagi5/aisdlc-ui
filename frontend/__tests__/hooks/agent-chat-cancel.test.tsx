// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Stop actually stops.
 *
 * The button is the visible half; this is the half that has to work. `cancel` aborts
 * the in-flight request and clears `busy` — if it did not, the composer would stay
 * disabled after a Stop press and the only way out would be reloading the page, which
 * is worse than having no button.
 *
 * WHAT THIS DOES NOT COVER, and cannot from jsdom: whether closing the stream stops the
 * AGENT. That is the BFF's job — `app/api/chat/route.ts` closes its upstream WebSocket
 * when the request signal aborts — and it needs a live backend to observe.
 */

vi.mock("@/lib/api/conversations", () => ({
  listConversations: vi.fn().mockResolvedValue([]),
  createConversation: vi.fn().mockResolvedValue({ id: "s1" }),
  getConversationMessages: vi.fn().mockResolvedValue([]),
  uploadAttachments: vi.fn().mockResolvedValue([]),
}));

import { useAgentChat } from "@/hooks/use-agent-chat";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** A response whose body never ends, so the turn stays in flight until aborted. */
function neverEndingStream(signal: AbortSignal) {
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: () =>
          new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () => {
              const err = new Error("aborted");
              err.name = "AbortError";
              reject(err);
            });
          }),
        releaseLock: () => {},
      }),
    },
  };
}

function Harness({ onReady }: { onReady: (c: ReturnType<typeof useAgentChat>) => void }) {
  const chat = useAgentChat({ agent: "requirements", projectId: "p-1" });
  React.useEffect(() => onReady(chat));
  return null;
}

function mount() {
  let latest: ReturnType<typeof useAgentChat> | null = null;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Harness onReady={(c) => { latest = c; }} />
    </QueryClientProvider>,
  );
  return () => latest!;
}

describe("stopping a turn", () => {
  it("aborts the request and clears busy", async () => {
    const fetchMock = vi.fn((_url: string, init: RequestInit) =>
      Promise.resolve(neverEndingStream(init.signal as AbortSignal) as unknown as Response),
    );
    vi.stubGlobal("fetch", fetchMock);

    const chat = mount();
    await act(async () => {
      void chat().send("write me a very long document");
    });
    await waitFor(() => expect(chat().busy).toBe(true));

    const signal = (fetchMock.mock.calls[0]![1] as RequestInit).signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    await act(async () => {
      chat().cancel();
    });

    // THE TWO THINGS A STOP MUST DO. The request is torn down, so nothing further is
    // received or billed; and `busy` clears, so the composer reopens — without that the
    // only escape from a stopped turn is a page reload.
    expect(signal.aborted).toBe(true);
    await waitFor(() => expect(chat().busy).toBe(false));
  });

  it("leaves no message stuck mid-stream", async () => {
    /** A bubble left with `streaming: true` blinks its cursor forever and reads as an
     *  agent still thinking about a turn that was stopped. */
    const fetchMock = vi.fn((_url: string, init: RequestInit) =>
      Promise.resolve(neverEndingStream(init.signal as AbortSignal) as unknown as Response),
    );
    vi.stubGlobal("fetch", fetchMock);

    const chat = mount();
    await act(async () => {
      void chat().send("hello");
    });
    await waitFor(() => expect(chat().busy).toBe(true));

    await act(async () => {
      chat().cancel();
    });

    await waitFor(() =>
      expect(chat().messages.some((m) => m.streaming)).toBe(false),
    );
  });
});
