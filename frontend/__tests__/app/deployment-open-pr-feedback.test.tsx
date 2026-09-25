// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * "Open deployment PR" reports what happened, where it was pressed.
 *
 * The button's work happens inside the agent chat, and the page used to CLOSE that
 * chat before sending — so the reply, including every refusal, arrived in a panel the
 * clicker could not see. Pressed twice against a project whose Azure DevOps credential
 * the PR path could not resolve, it looked each time exactly like a button that had
 * never been pressed at all, while a branch was quietly pushed to the remote.
 *
 * So: a pending toast on the click, replaced by the outcome — the PR's link when one
 * was opened, and otherwise the agent's own sentence, which is the only place the
 * reason exists.
 */

const state = vi.hoisted(() => ({
  busy: false,
  messages: [] as { role: string; content: string }[],
  sent: [] as string[],
  prUrl: null as string | null,
  toasts: { loading: [] as unknown[], success: [] as unknown[], error: [] as unknown[] },
}));

vi.mock("sonner", () => ({
  toast: {
    loading: (...a: unknown[]) => state.toasts.loading.push(a),
    success: (...a: unknown[]) => state.toasts.success.push(a),
    error: (...a: unknown[]) => state.toasts.error.push(a),
  },
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "11111111-1111-1111-1111-111111111111" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));
vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({
    user: { id: "u1", email: "devops@gmail.com" },
    permissions: ["run:create", "run:trigger"],
  }),
}));
// The button is wrapped in RequireRole, which reads the session CONTEXT rather than
// the hook — without this it renders its disabled fallback, whose click does nothing
// and whose accessible name is identical to the real button's.
vi.mock("@/components/auth/session-provider", () => ({
  useRawSession: () => ({ role: "admin", permissions: ["run:trigger"] }),
}));
vi.mock("@/hooks/use-agent-chat", () => ({
  useAgentChat: () => ({
    busy: state.busy,
    messages: state.messages,
    sessions: [{ id: "c1", title: "Staging release" }],
    sessionId: "c1",
    attachments: [],
    send: (text: string) => { state.sent.push(text); return Promise.resolve(); },
    cancel: vi.fn(), newChat: vi.fn(), selectSession: vi.fn(),
    attachFiles: vi.fn(), removeAttachment: vi.fn(), documents: [],
  }),
}));
vi.mock("@/hooks/use-chat-deep-link", () => ({ useChatDeepLink: () => null }));
vi.mock("@/components/app/deploy-target-dialog", () => ({ DeployTargetDialog: () => null }));
vi.mock("@/components/app/deployment-approvals", () => ({ DeploymentApprovals: () => null }));
vi.mock("@/components/app/code-viewer", () => ({ CodeViewer: () => null }));
vi.mock("@/components/app/model-selector", () => ({ ModelSelector: () => null }));
vi.mock("@/components/app/document-list", () => ({ DocumentList: () => null }));
vi.mock("@/components/app/agent-chat-drawer", () => ({ AgentChatDrawer: () => null }));
vi.mock("@/lib/api/projects", () => ({
  getProject: async () => ({ id: "p", name: "QuickLink", displayName: "QuickLink" }),
}));
vi.mock("@/lib/api/artifacts", () => ({ listArtifacts: async () => [], submitArtifact: async () => ({}) }));
vi.mock("@/lib/api/deployment", () => ({
  getPreparedDeploy: async () => ({
    status: "ready", mode: "branch", repo_name: "QuickLink", ado_project: "QuickLink",
    branch: "main", pr_id: null, pr_title: "", head_sha: "082f91e", environment: "staging",
    deploy_via: "azure_pipelines", image_registry: "", image_name: "quicklink", namespace: "",
  }),
  getRelease: async () => ({
    release: {
      context: {
        repo_name: "QuickLink", ado_project: "QuickLink", mode: "branch",
        source_branch: "main", head_sha: "082f91e", environment: "staging",
        deploy_via: "azure_pipelines",
      },
      summary: "", readiness: "conditional", risk_score: "low", risk_rationale: "",
      gate_summary: [], deploy_runbook: "", rollback_runbook: "", iac_findings: [],
      generated_files: [{ path: "Dockerfile", contents: "FROM node:20-alpine", language: "docker" }],
      compliance_evidence: {
        captured_at: "", gate_approvals: [], test_summary: "", security_summary: "",
        sbom_present: false, notes: "",
      },
      release_decision: "conditional", release_justification: "", status: "assessed",
      pr_url: state.prUrl, pr_title: null,
      document: { filename: "readiness.docx", url: "http://x/readiness.docx", artifact_id: "doc-7" },
    },
    staged_files: [],
  }),
}));

import DeploymentPage from "@/app/(app)/projects/[id]/deployment/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // A FRESH ELEMENT EVERY TIME. Re-rendering the same element object lets React bail
  // out without re-running the component, so `state.busy` was never re-read and a
  // turn appeared never to end — which every assertion here depends on.
  const ui = () => (
    <QueryClientProvider client={client}>
      <DeploymentPage />
    </QueryClientProvider>
  );
  const view = render(ui());
  return { ...view, rerender: () => view.rerender(ui()) };
}

/** The button lives on the Artifacts tab; the page opens on Readiness. */
async function openPrFromArtifactsTab() {
  const artifactsTab = await screen.findByRole("button", { name: /artifacts/i });
  fireEvent.click(artifactsTab);
  const btn = await screen.findByRole("button", { name: /open deployment pr/i });
  fireEvent.click(btn);
}

beforeEach(() => {
  state.busy = false;
  state.messages = [];
  state.sent = [];
  state.prUrl = null;
  state.toasts = { loading: [], success: [], error: [] };
});
afterEach(cleanup);

describe("open deployment PR — feedback", () => {
  it("acknowledges the click immediately, and still asks the agent", async () => {
    renderPage();
    await openPrFromArtifactsTab();

    expect(state.sent).toEqual(["Open the deployment PR now with the staged files."]);
    await waitFor(() => expect(state.toasts.loading).toHaveLength(1));
  });

  it("reports the agent's own reason when no PR was opened", async () => {
    const { rerender } = renderPage();
    await openPrFromArtifactsTab();

    // The turn runs, and ends having pushed a branch but opened nothing.
    state.busy = true;
    rerender();
    state.messages = [
      { role: "user", content: "Open the deployment PR now with the staged files." },
      { role: "agent", content: "ERROR: branch deploy/staging-0d2966c5 WAS pushed, but the pull request could not be opened." },
    ];
    state.busy = false;
    rerender();

    await waitFor(() => expect(state.toasts.error).toHaveLength(1));
    const [, opts] = state.toasts.error[0] as [string, { description: string }];
    expect(opts.description).toContain("deploy/staging-0d2966c5");
    expect(state.toasts.success).toHaveLength(0);
  });

  it("offers the PR when one was opened", async () => {
    const { rerender } = renderPage();
    await openPrFromArtifactsTab();

    // The PR appears on the release only once the turn has run — set before the
    // render, it would replace the very button this test has to press.
    state.prUrl = "https://dev.azure.com/acme/QuickLink/_git/QuickLink/pullrequest/7";
    state.busy = true;
    rerender();
    state.messages = [{ role: "agent", content: "Deployment PR opened." }];
    state.busy = false;
    rerender();

    await waitFor(() => expect(state.toasts.success).toHaveLength(1));
    const [, opts] = state.toasts.success[0] as [string, { action: { label: string } }];
    expect(opts.action.label).toMatch(/open pr/i);
    expect(state.toasts.error).toHaveLength(0);
  });

  it("stays quiet when a turn that nobody asked to open a PR finishes", async () => {
    const { rerender } = renderPage();
    await screen.findByRole("button", { name: /artifacts/i });

    state.busy = true;
    rerender();
    state.busy = false;
    rerender();

    await waitFor(() => expect(state.toasts.loading).toHaveLength(0));
    expect(state.toasts.error).toHaveLength(0);
    expect(state.toasts.success).toHaveLength(0);
  });
});
