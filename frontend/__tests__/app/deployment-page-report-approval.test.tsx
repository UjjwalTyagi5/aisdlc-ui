// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The Deployment page gets what Security and Code Review got: the assessment's filed
 * Deployment Readiness Report with its approval state and a Raise for approval beside the
 * decision, and the Documents panel beside the release instead of a tab.
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
  useSession: () => ({ user: { id: "u1", email: "devops@gmail.com" }, permissions: ["run:create", "run:trigger"] }),
}));
vi.mock("@/hooks/use-agent-chat", () => ({
  useAgentChat: () => ({
    busy: false, messages: [], sessions: [{ id: "c1", title: "Staging release" }], sessionId: "c1", attachments: [],
    send: vi.fn(), cancel: vi.fn(), newChat: vi.fn(), selectSession: vi.fn(),
    attachFiles: vi.fn(), removeAttachment: vi.fn(), documents: [],
  }),
}));
vi.mock("@/hooks/use-chat-deep-link", () => ({ useChatDeepLink: () => null }));
vi.mock("@/components/app/deploy-target-dialog", () => ({ DeployTargetDialog: () => null }));
vi.mock("@/components/app/deployment-approvals", () => ({ DeploymentApprovals: () => null }));
vi.mock("@/components/app/code-viewer", () => ({ CodeViewer: () => null }));
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
    id: "doc-7", projectId: "p", runId: "r1", type: "document", scope: "agent", stage: "deployment",
    title: "QuickLink_Deployment_Readiness_staging_main_082f91e.docx", status: state.reportStatus, version: 1,
    contentHash: "h".repeat(64), body: { kind: "document", filename: "QuickLink_Deployment_Readiness_staging_main_082f91e.docx", stored: true },
    phase: "deployment", createdBy: "agent", createdAt: "2026-09-16T12:00:00Z", updatedAt: "2026-09-16T12:00:00Z",
    approvedBy: null, approvedAt: null,
  }],
  submitArtifact: async (id: string) => { state.submitted.push(id); return {}; },
}));
vi.mock("@/lib/api/deployment", () => ({
  getPreparedDeploy: async () => ({
    status: "ready", mode: "branch", repo_name: "QuickLink", ado_project: "QuickLink", branch: "main", pr_id: null,
    pr_title: "", head_sha: "082f91e", environment: "staging", deploy_via: "azure_pipelines", image_registry: "", image_name: "quicklink", namespace: "",
  }),
  getRelease: async () => ({
    release: {
      context: { repo_name: "QuickLink", ado_project: "QuickLink", mode: "branch", source_branch: "main", head_sha: "082f91e", environment: "staging", deploy_via: "azure_pipelines" },
      summary: "Node 20 service.", readiness: "conditional", risk_score: "high", risk_rationale: "tar via sqlite3",
      gate_summary: [], generated_files: [], deploy_runbook: "", rollback_runbook: "", iac_findings: [],
      compliance_evidence: { captured_at: "", gate_approvals: [], test_summary: "", security_summary: "", sbom_present: false, notes: "" },
      release_decision: "conditional", release_justification: "Read the quality gate first.", pr_url: null, pr_title: null, status: "assessed",
      document: { filename: "QuickLink_Deployment_Readiness_staging_main_082f91e.docx", url: "http://localhost:8004/generated/u/deployment/s/output/r.docx", artifact_id: "doc-7" },
    },
    staged_files: [],
  }),
}));

import DeploymentPage from "@/app/(app)/projects/[id]/deployment/page";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><DeploymentPage /></QueryClientProvider>);
}

beforeEach(() => { state.drawer = null; state.documentStages = []; state.submitted = []; state.reportStatus = "draft"; });
afterEach(cleanup);

describe("Deployment page", () => {
  it("shows the filed readiness report's state beside the decision, with Download and Raise for approval", async () => {
    renderPage();
    expect(await screen.findByText("Draft · not yet raised for approval")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download report/ }).getAttribute("href")).toContain("/deployment/");
    fireEvent.click(screen.getByRole("button", { name: "Raise for approval" }));
    await waitFor(() => expect(state.submitted).toEqual(["doc-7"]));
  });

  it("offers no Raise once the report is raised", async () => {
    state.reportStatus = "pending";
    renderPage();
    expect(await screen.findByText("Raised for approval · waiting on the approver")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Raise for approval" })).toBeNull();
  });

  it("keeps the Deployment documents beside the release, not in a tab, and gives the chat its history", async () => {
    renderPage();
    await screen.findByText("Draft · not yet raised for approval");
    expect(screen.getByRole("complementary", { name: "Documents" })).toBeTruthy();
    expect(state.documentStages).toContain("deployment");
    expect(screen.queryByRole("button", { name: /^Documents$/ })).toBeNull();
    expect(state.drawer?.sessions).toEqual([{ id: "c1", title: "Staging release" }]);
  });
});
