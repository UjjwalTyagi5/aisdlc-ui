// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * "Raise for approval" follows the route's rule: `run:create`, OR use of the agent the
 * document belongs to (product decision, 2026-09-16).
 *
 * THE CASE THAT PROMPTED IT. security@gmail.com is QA on the project with Code Review
 * granted as an extra agent. The Code Review page's Documents panel listed the review
 * report as a draft with no way to send it — the button followed `run:create` alone,
 * which QA does not hold. The same rule kept a tester from sending test cases.
 */

const state = vi.hoisted(() => ({
  permissions: [] as string[],
  reach: {} as Record<string, string>,
}));

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ user: { id: "u1", email: "security@gmail.com" }, permissions: state.permissions }),
}));
vi.mock("@/hooks/use-delete-artifact", () => ({
  useDeleteArtifact: () => ({ onDelete: undefined, deletingId: null, dialog: null }),
}));
vi.mock("@/lib/api/artifacts", () => ({
  listArtifacts: vi.fn(), approveArtifact: vi.fn(), rejectArtifact: vi.fn(),
  uploadArtifact: vi.fn(), submitArtifact: vi.fn(),
}));
vi.mock("@/lib/api/capabilities", () => ({
  getMyAgentAccess: vi.fn(async () => ({ projectId: "p1", role: "qa", extraAgents: ["review"], reach: state.reach })),
}));

import { DocumentList } from "@/components/app/document-list";

const draft = (stage: string | null, title = "QuickLink_Code_Review.docx") => ({
  id: `id-${title}`, projectId: "p1", runId: "r1", type: "document",
  scope: stage ? "agent" : "project", stage, title, status: "draft", version: 1, contentHash: "h",
  body: { kind: "document", filename: title, stored: false },
  phase: stage === "code_review" ? "review" : (stage ?? "requirements"),
  createdBy: "agent", createdAt: "2026-09-16T12:00:00Z", updatedAt: "2026-09-16T12:00:00Z",
});

function renderList(stage: string, items: unknown[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DocumentList projectId={"p1" as never} stage={stage} items={items as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  state.permissions = ["artifact:view"];
  state.reach = {};
  vi.clearAllMocks(); // call history only — the mocks keep their implementations
});
afterEach(cleanup);

describe("Raise for approval", () => {
  it("is offered to a user of the document's agent without run:create", async () => {
    state.reach = { review: "use", testing: "owner" };
    renderList("code_review", [draft("code_review")]);
    expect(await screen.findByRole("button", { name: "Raise for approval" })).toBeTruthy();
  });

  it("is not offered to someone who can neither create runs nor use the agent", async () => {
    state.reach = { testing: "owner" };
    renderList("code_review", [draft("code_review")]);
    await screen.findByText("QuickLink_Code_Review.docx");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Raise for approval" })).toBeNull());
  });

  it("is not offered on a project-wide document without run:create", async () => {
    state.reach = { review: "use" };
    renderList("code_review", [draft(null, "Security_Policy.pdf")]);
    await screen.findByText("Security_Policy.pdf");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Raise for approval" })).toBeNull());
  });

  it("is offered to run:create without asking for agent access", async () => {
    state.permissions = ["run:create"];
    const { getMyAgentAccess } = await import("@/lib/api/capabilities");
    renderList("code_review", [draft("code_review")]);
    expect(await screen.findByRole("button", { name: "Raise for approval" })).toBeTruthy();
    expect(getMyAgentAccess).not.toHaveBeenCalled();
  });
});
