// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The Security page gets what Code Review got: the scan's filed report with its approval
 * state and a Raise for approval beside the sign-off, the Documents panel beside the scan,
 * and the chat's history.
 */

const state = vi.hoisted(() => ({
  drawer: null as null | Record<string, unknown>,
  documentStages: [] as string[],
  submitted: [] as string[],
  reportStatus: "draft" as string,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "11111111-1111-1111-1111-111111111111" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));
vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ user: { id: "u1", email: "security@gmail.com" }, permissions: ["run:create"] }),
}));
vi.mock("@/hooks/use-agent-chat", () => ({
  useAgentChat: () => ({
    busy: false, messages: [], sessions: [{ id: "c1", title: "Earlier scan chat" }], sessionId: "c1", attachments: [],
    send: vi.fn(), cancel: vi.fn(), newChat: vi.fn(), selectSession: vi.fn(),
    attachFiles: vi.fn(), removeAttachment: vi.fn(), documents: [],
  }),
}));
vi.mock("@/components/app/scan-target-dialog", () => ({ ScanTargetDialog: () => null }));
vi.mock("@/components/app/model-selector", () => ({ ModelSelector: () => null }));
vi.mock("@/components/app/document-list", () => ({
  DocumentList: ({ stage }: { stage?: string }) => { state.documentStages.push(stage ?? "project-wide"); return null; },
}));
vi.mock("@/components/app/agent-chat-drawer", () => ({
  AgentChatDrawer: (props: Record<string, unknown>) => { state.drawer = props; return null; },
}));
vi.mock("@/lib/api/projects", () => ({ getProject: async () => ({ id: "p", name: "TEST Project", displayName: "TEST Project" }) }));
vi.mock("@/lib/api/artifacts", () => ({
  listArtifacts: async () => [{
    id: "doc-1", projectId: "p", runId: "r1", type: "document", scope: "agent", stage: "security",
    title: "QuickLink_Security_Review.docx", status: state.reportStatus, version: 1, contentHash: "h".repeat(64),
    body: { kind: "document", filename: "QuickLink_Security_Review.docx", stored: false },
    phase: "security", createdBy: "agent", createdAt: "2026-09-16T12:00:00Z", updatedAt: "2026-09-16T12:00:00Z",
    approvedBy: null, approvedAt: null,
  }],
  submitArtifact: async (id: string) => { state.submitted.push(id); return {}; },
}));
vi.mock("@/lib/api/security", () => ({
  listScans: async () => [{ id: "s1", label: "feature/x", repo_name: "QuickLink", risk_score: "high", signoff: "fail", findings_count: 2, created_at: "2026-09-16T12:00:05Z" }],
  getScan: async () => ({
    id: "s1", created_at: "2026-09-16T12:00:05Z",
    context: { repo_name: "QuickLink", ado_project: "QuickLink", mode: "branch", branch: "feature/x", head_sha: "082f91e" },
    summary: "One open redirect.", risk_score: "high", signoff: { decision: "fail", rationale: "Fix S-001." },
    findings: [], sbom: [], supply_chain: [], remediation_plan: "", suppression_log: [], compliance_frameworks: ["OWASP Top 10"],
    document: { filename: "QuickLink_Security_Review.docx", url: "http://localhost:8004/generated/u/security/s/output/QuickLink_Security_Review.docx", artifact_id: "doc-1" },
    metrics: { critical: 0, high: 1, medium: 0, low: 0, total: 1 },
  }),
}));

import SecurityPage from "@/app/(app)/projects/[id]/security/page";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SecurityPage /></QueryClientProvider>);
}

beforeEach(() => { state.drawer = null; state.documentStages = []; state.submitted = []; state.reportStatus = "draft"; });
afterEach(cleanup);

describe("Security page", () => {
  it("shows the filed report's state beside the sign-off, with Download and Raise for approval", async () => {
    renderPage();
    expect(await screen.findByText("Draft · not yet raised for approval")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download report/ }).getAttribute("href")).toContain("QuickLink_Security_Review.docx");
    fireEvent.click(screen.getByRole("button", { name: "Raise for approval" }));
    await screen.findByText(/Sign-off/);
    expect(state.submitted).toEqual(["doc-1"]);
  });

  it("says when the report has been raised, and offers no second raise", async () => {
    state.reportStatus = "awaiting_approval";
    renderPage();
    expect(await screen.findByText("Raised for approval · waiting on the approver")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Raise for approval" })).toBeNull();
  });

  it("keeps the Security documents beside the scan and gives the chat its history", async () => {
    renderPage();
    await screen.findByText(/Sign-off/);
    expect(screen.getByRole("complementary", { name: "Documents" })).toBeTruthy();
    expect(state.documentStages).toContain("security");
    expect(screen.queryByRole("button", { name: /^Documents$/ })).toBeNull();
    expect(state.drawer?.sessions).toEqual([{ id: "c1", title: "Earlier scan chat" }]);
    expect(typeof state.drawer?.onNewChat).toBe("function");
  });
});
