// @vitest-environment jsdom
/**
 * Contract test: useAgentChat POSTs context.requirements through to /chat.
 *
 * Why jsdom: the hook uses React state (useState/useCallback/useRef) and must
 * run inside a React reconciler. The project vitest.config.ts defaults to
 * "node" for pure-logic tests; this file overrides to "jsdom" per Vitest's
 * per-file environment directive so renderHook works without changing the
 * global config.
 *
 * Why these two cases: Task 4 (Requirements page) passes selected_story_refs
 * as either a populated array (user has checked items) or [] (no selection /
 * "send all"). Both must survive the POST body round-trip so the backend
 * format_pipeline_context receives them under the `requirements` key.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// API_BASE is the only import from the api client used by useAgentChat.
vi.mock("@/lib/api/client", () => ({ API_BASE: "http://test.local/api" }));

import { useAgentChat } from "@/hooks/use-agent-chat";

/**
 * `useAgentChat` calls `useQueryClient`, so it cannot render outside a
 * provider — it gained that dependency after these tests were written, which
 * is what broke them ("No QueryClient set").
 *
 * A FRESH CLIENT PER TEST. A shared one would carry cache and in-flight state
 * between cases, and the whole point of these two is that each observes its
 * own single POST. Retries are off for the same reason: a retry would call the
 * fetch mock twice and fail the count assertion for a reason that has nothing
 * to do with the context payload being tested.
 *
 * `React.createElement` rather than JSX because this file is `.ts` — adding a
 * `.tsx` rename would churn the path for one wrapper.
 */
function withQueryClient() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client }, children);
  };
}

function emptySseResponse(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

describe("useAgentChat context channel", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(emptySseResponse());
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("POSTs requirements.selected_story_refs in the context to /chat", async () => {
    const context = {
      page: "Requirements",
      requirements: {
        selected_story_refs: ["1234", "1235"],
        all_story_refs: ["1234", "1235", "1236"],
      },
    };
    const { result } = renderHook(() => useAgentChat({ context }), {
      wrapper: withQueryClient(),
    });

    await act(async () => {
      await result.current.send("create a story");
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://test.local/api/chat");
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.message).toBe("create a story");
    expect(body.context.requirements.selected_story_refs).toEqual(["1234", "1235"]);
    expect(body.context.requirements.all_story_refs).toEqual(["1234", "1235", "1236"]);
  });

  it("POSTs an empty selected_story_refs array when none are selected", async () => {
    const context = {
      page: "Requirements",
      requirements: {
        selected_story_refs: [],
        all_story_refs: ["1234"],
      },
    };
    const { result } = renderHook(() => useAgentChat({ context }), {
      wrapper: withQueryClient(),
    });

    await act(async () => {
      await result.current.send("normalise everything");
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0]!;
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.context.requirements.selected_story_refs).toEqual([]);
    expect(body.context.requirements.all_story_refs).toEqual(["1234"]);
  });
});
