// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * When a Code Review turn ends, the page opens the review THAT TURN saved — never merely
 * the newest one on file.
 *
 * THE BUG. The busy → idle effect refetched the list and opened `reviews[0]` whatever
 * happened in the turn. A real run on a model whose key had been revoked failed without
 * saving anything; the page then dropped the staged whole-branch target and opened the
 * previous review, so an error read as a finished review of something else. A plain chat
 * question ("send the report for approval") did the same.
 */

const state = vi.hoisted(() => ({
  busy: false,
  reviews: [] as { id: string; label: string; repo_name: string; merge_recommendation: string;
    findings_count: number; critical_high: number; created_at: string }[],
  opened: [] as string[],
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "11111111-1111-1111-1111-111111111111" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));
vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ user: { id: "u1", email: "sec@abcbank.com" }, permissions: ["*"] }),
}));
vi.mock("@/hooks/use-agent-chat", () => ({
  useAgentChat: () => ({
    busy: state.busy, messages: [], sessions: [], sessionId: null, attachments: [],
    send: vi.fn(), cancel: vi.fn(), newChat: vi.fn(), selectSession: vi.fn(),
    attachFiles: vi.fn(), removeAttachment: vi.fn(), documents: [],
  }),
}));

const PREPARED = {
  status: "ready", mode: "repo", repo_name: "QuickLink", ado_project: "QuickLink",
  source_branch: "feature/116-117-link-management", base_branch: "", head_sha: "082f91e",
  base_sha: "", truncated: false, diff: "", files: [],
};
vi.mock("@/components/app/review-target-dialog", () => ({
  ReviewTargetDialog: ({ onPrepared }: { onPrepared: (r: unknown) => void }) => (
    <button type="button" onClick={() => onPrepared(PREPARED)}>stage the branch</button>
  ),
}));
vi.mock("@/components/app/code-review-report", () => ({
  CodeReviewReport: ({ artifact }: { artifact: { id: string } }) => <div>report {artifact.id}</div>,
  BranchFilesView: ({ prepared }: { prepared: { source_branch: string } | null }) => (
    <div>{prepared ? `staged ${prepared.source_branch}` : "no staged branch"}</div>
  ),
  SecurityView: () => null,
  SbomView: () => null,
  scanRan: () => false,
}));
vi.mock("@/components/app/document-list", () => ({ DocumentList: () => null }));
vi.mock("@/components/app/agent-chat-drawer", () => ({ AgentChatDrawer: () => null }));
vi.mock("@/components/app/model-selector", () => ({ ModelSelector: () => null }));
vi.mock("@/lib/api/projects", () => ({
  getProject: async () => ({ id: "p", name: "TEST Project", displayName: "TEST Project" }),
}));
vi.mock("@/lib/api/code-review", () => ({
  listReviews: async () => state.reviews,
  getReview: async (_p: string, rid: string) => {
    state.opened.push(rid);
    return {
      id: rid, findings: [], diff: "",
      context: { mode: "repo", repo_name: "QuickLink", source_branch: "feature/116-117-link-management" },
    };
  },
}));

import CodeReviewPage from "@/app/(app)/projects/[id]/code-review/page";

function row(id: string) {
  return { id, label: `review ${id}`, repo_name: "QuickLink", merge_recommendation: "request_changes",
    findings_count: 5, critical_high: 3, created_at: "2026-09-16T17:31:00Z" };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={qc}><CodeReviewPage /></QueryClientProvider>);
  const rerender = () => view.rerender(<QueryClientProvider client={qc}><CodeReviewPage /></QueryClientProvider>);
  return { rerender };
}

/** Stage the whole branch, then run one turn that ends with `after` on file. */
async function runTurn(after: ReturnType<typeof row>[]) {
  const { rerender } = renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "stage the branch" }));
  expect(await screen.findByText("staged feature/116-117-link-management")).toBeTruthy();

  await act(async () => { state.busy = true; rerender(); });
  state.reviews = after;
  await act(async () => { state.busy = false; rerender(); });
}

beforeEach(() => {
  state.busy = false;
  state.reviews = [row("old")];
  state.opened = [];
});
afterEach(cleanup);

describe("Code Review page, after a turn ends", () => {
  it("keeps the staged branch when the turn saved no review", async () => {
    await runTurn([row("old")]);

    await new Promise((r) => setTimeout(r, 50));
    expect(state.opened).not.toContain("old");
    expect(screen.getByText("staged feature/116-117-link-management")).toBeTruthy();
    expect(screen.queryByText("report old")).toBeNull();
  });

  it("opens the review the turn saved", async () => {
    await runTurn([row("new"), row("old")]);

    await waitFor(() => expect(state.opened).toContain("new"));
    expect(await screen.findByText("report new")).toBeTruthy();
  });
});
