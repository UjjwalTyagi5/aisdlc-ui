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

/**
 * Formats a StreamEvent-shaped payload as one `data: ...\n\n` SSE frame,
 * matching what /api/chat's real handler writes and what useAgentChat's
 * `send()` reader loop parses (splits on "\n\n", strips the "data: "
 * prefix, JSON.parses the rest).
 */
function sseFrame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}

/**
 * A response whose body streams the given already-formatted frames as
 * SEPARATE chunks (one `enqueue` per frame) — exercises the reader loop's
 * buffering across multiple `reader.read()` calls, not just a single
 * chunk containing everything.
 */
function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
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

/**
 * Task 2 code review (task-2-review.md, Important finding #1): the per-turn
 * `code.diff` merge rule in `applyEvent` — keyed by `path`, `original`
 * pinned to the FIRST event seen for that path this turn, `modified`
 * overwritten by the LATEST — had no test driving actual `code.diff`
 * StreamEvents through the reducer; `agent-chat-drawer.test.tsx` only ever
 * constructed pre-built `diffs` arrays as props. These tests close that gap
 * by streaming real SSE frames through `send()` (the same path a live
 * backend `file_diff` → `mapWsToSseEvent` → SSE frame takes) and asserting
 * on the resulting `AgentChatMessage.diffs`.
 */
describe("useAgentChat — code.diff per-turn merge (applyEvent)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  function agentDiffs(messages: ReturnType<typeof useAgentChat>["messages"]) {
    const agentMsg = messages.find((m) => m.role === "agent");
    return (agentMsg as { diffs?: unknown[] } | undefined)?.diffs as
      | Array<{ path: string; original: string; modified: string; changeKind: string }>
      | undefined;
  }

  it("a single code.diff event produces one diff entry with matching fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        sseFrame({
          type: "code.diff",
          runId: "run_test_001",
          path: "src/a.ts",
          original: "export const a = 1;\n",
          modified: "export const a = 2;\n",
          changeKind: "created",
          at: "2026-08-31T00:00:00.000Z",
        }),
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useAgentChat(), { wrapper: withQueryClient() });

    await act(async () => {
      await result.current.send("write a file");
    });

    expect(agentDiffs(result.current.messages)).toEqual([
      {
        path: "src/a.ts",
        original: "export const a = 1;\n",
        modified: "export const a = 2;\n",
        changeKind: "created",
      },
    ]);
  });

  it("two code.diff events for the SAME path keep the first original, take the latest modified, and pin changeKind to the first event", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        sseFrame({
          type: "code.diff",
          runId: "run_test_001",
          path: "src/b.ts",
          original: "orig-1",
          modified: "mod-1",
          changeKind: "edited",
          at: "2026-08-31T00:00:00.000Z",
        }),
        sseFrame({
          type: "code.diff",
          runId: "run_test_001",
          path: "src/b.ts",
          // A distinct original/changeKind on the repeat event proves these
          // are NOT re-read from the second event — the merge must ignore them.
          original: "orig-2-should-be-ignored",
          modified: "mod-2",
          changeKind: "created",
          at: "2026-08-31T00:00:01.000Z",
        }),
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useAgentChat(), { wrapper: withQueryClient() });

    await act(async () => {
      await result.current.send("edit_file called twice on the same file");
    });

    expect(agentDiffs(result.current.messages)).toEqual([
      {
        path: "src/b.ts",
        original: "orig-1", // pinned to the FIRST event
        modified: "mod-2", // overwritten by the LATEST event
        changeKind: "edited", // pinned to the FIRST event
      },
    ]);
  });

  it("code.diff events for two DIFFERENT paths produce two separate diff entries", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        sseFrame({
          type: "code.diff",
          runId: "run_test_001",
          path: "src/one.ts",
          original: "1-orig",
          modified: "1-mod",
          changeKind: "created",
          at: "2026-08-31T00:00:00.000Z",
        }),
        sseFrame({
          type: "code.diff",
          runId: "run_test_001",
          path: "src/two.ts",
          original: "2-orig",
          modified: "2-mod",
          changeKind: "edited",
          at: "2026-08-31T00:00:01.000Z",
        }),
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useAgentChat(), { wrapper: withQueryClient() });

    await act(async () => {
      await result.current.send("touch two files");
    });

    const diffs = agentDiffs(result.current.messages);
    expect(diffs).toHaveLength(2);
    expect(diffs).toEqual(
      expect.arrayContaining([
        { path: "src/one.ts", original: "1-orig", modified: "1-mod", changeKind: "created" },
        { path: "src/two.ts", original: "2-orig", modified: "2-mod", changeKind: "edited" },
      ]),
    );
  });
});

/**
 * The artifactId from `file_generated` is deliberately deterministic —
 * `artifact:<runId>:<filename>`, so the same file always maps to the same artifact
 * (lib/bff/ws-to-sse.ts). Agents emit that event more than once for one file (a
 * re-broadcast, or a document written and then registered), and citations appended
 * unconditionally, so the same id landed in the list twice. The chips key on it, so
 * React reported "Encountered two children with the same key" and reserved the right
 * to drop or duplicate one. Seen live with QuickLink_URL_Shortener_Architecture.docx.
 */
describe("useAgentChat — repeated artifact.updated for one file", () => {
  function agentCitations(messages: unknown[]): string[] | undefined {
    const agentMsg = (messages as Array<{ role: string; citations?: string[] }>).find(
      (m) => m.role === "agent",
    );
    return agentMsg?.citations;
  }

  const ARTIFACT = "artifact:run_test_001:QuickLink_URL_Shortener_Architecture.docx";

  it("records one citation when the same artifact is announced twice", async () => {
    const frame = () =>
      sseFrame({
        type: "artifact.updated",
        runId: "run_test_001",
        artifactId: ARTIFACT,
        status: "approved",
        name: "QuickLink_URL_Shortener_Architecture.docx",
        at: "2026-09-04T00:00:00.000Z",
      });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([frame(), frame()])));

    const { result } = renderHook(() => useAgentChat(), { wrapper: withQueryClient() });
    await act(async () => {
      await result.current.send("create the architecture doc");
    });

    expect(agentCitations(result.current.messages)).toEqual([ARTIFACT]);
  });

  it("still records both when two different artifacts are produced", async () => {
    const other = "artifact:run_test_001:Sprint_Plan.docx";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          sseFrame({
            type: "artifact.updated", runId: "run_test_001", artifactId: ARTIFACT,
            status: "approved", at: "2026-09-04T00:00:00.000Z",
          }),
          sseFrame({
            type: "artifact.updated", runId: "run_test_001", artifactId: other,
            status: "approved", at: "2026-09-04T00:00:01.000Z",
          }),
        ]),
      ),
    );

    const { result } = renderHook(() => useAgentChat(), { wrapper: withQueryClient() });
    await act(async () => {
      await result.current.send("create both docs");
    });

    expect(agentCitations(result.current.messages)).toEqual([ARTIFACT, other]);
  });

  it("keys stay unique, which is what React actually requires", async () => {
    const frame = () =>
      sseFrame({
        type: "artifact.updated", runId: "run_test_001", artifactId: ARTIFACT,
        status: "approved", at: "2026-09-04T00:00:00.000Z",
      });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse([frame(), frame(), frame()])),
    );

    const { result } = renderHook(() => useAgentChat(), { wrapper: withQueryClient() });
    await act(async () => {
      await result.current.send("regenerate");
    });

    const citations = agentCitations(result.current.messages) ?? [];
    expect(new Set(citations).size).toBe(citations.length);
  });
});
