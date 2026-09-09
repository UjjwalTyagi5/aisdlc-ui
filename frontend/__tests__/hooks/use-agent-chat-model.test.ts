// @vitest-environment jsdom
/**
 * The model picker on an agent page has to reach the agent.
 *
 * It did not. `agentModel` was set by the ModelSelector on every standalone agent page
 * and read by nothing — the useState and the selector's own `value`, and that was the
 * whole of it. So `/api/chat` was posted with no model, the BFF's WS frame carried no
 * model, and every agent resolved with `offering_id=None` and fell through to
 * `offerings[0]` — which `_load_enabled` orders by provider display name.
 *
 * Alphabetical order is not a choice anyone made. On a tenant whose alphabetically
 * first connection had exhausted its spend cap, every turn came back
 * "Agent error: BadRequestError" while a working key sat one row below, selectable in
 * the UI and unreachable in fact.
 *
 * An OFFERING id, not a model id: two provider connections can expose the same model,
 * so only the offering says which key gets spent.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/client", () => ({ API_BASE: "http://test.local/api" }));

import { useAgentChat } from "@/hooks/use-agent-chat";

function withQueryClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client }, children);
  };
}

function emptySseResponse(): Response {
  return new Response(
    new ReadableStream<Uint8Array>({ start: (c) => c.close() }),
    { status: 200 },
  );
}

function bodyOf(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0]!;
  return JSON.parse((init as RequestInit).body as string) as Record<string, unknown>;
}

describe("useAgentChat carries the page's model choice", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(emptySseResponse());
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("POSTs the selected offering id to /chat", async () => {
    const { result } = renderHook(
      () => useAgentChat({ agent: "development", offeringId: "off-azure-1" }),
      { wrapper: withQueryClient() },
    );

    await act(async () => {
      await result.current.send("build the thing");
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(bodyOf(fetchMock).offeringId).toBe("off-azure-1");
  });

  it("sends the NEW offering after the picker changes, not the first one", async () => {
    // The regression this guards is subtler than "never sent": the send callback is
    // memoised, so an offering left out of its dependency list would be captured once
    // and every later turn would keep spending the key the user had already moved off.
    const { result, rerender } = renderHook(
      ({ offeringId }: { offeringId: string }) =>
        useAgentChat({ agent: "development", offeringId }),
      { wrapper: withQueryClient(), initialProps: { offeringId: "off-anthropic" } },
    );

    rerender({ offeringId: "off-azure-1" });

    await act(async () => {
      await result.current.send("build the thing");
    });

    expect(bodyOf(fetchMock).offeringId).toBe("off-azure-1");
  });

  it("omits the offering when the page has no pick, so the org default still applies", async () => {
    const { result } = renderHook(() => useAgentChat({ agent: "development" }), {
      wrapper: withQueryClient(),
    });

    await act(async () => {
      await result.current.send("build the thing");
    });

    expect(bodyOf(fetchMock).offeringId).toBeUndefined();
  });
});
