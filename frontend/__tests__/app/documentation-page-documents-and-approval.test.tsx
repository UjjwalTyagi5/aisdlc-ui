// @vitest-environment jsdom
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The Documentation page gets what Requirements, Code Review and Security have: its
 * documents in one panel, a document opened in the centre by its artifact row with its
 * approval state and a Raise for approval, a model picker, the chat's history — and a
 * prepared workspace that survives a refresh.
 */

const state = vi.hoisted(() => ({
  drawer: null as null | Record<string, unknown>,
  listProps: null as null | Record<string, unknown>,
  opened: [] as string[],
  submitted: [] as string[],
  status: "draft" as string,
  prepared: { status: "ready", mode: "branch", repo_name: "QuickLink", ado_project: "QuickLink", branch: "main",
    head_sha: "082f91e", languages: ["JavaScript"], upstream_summary: "" } as Record<string, unknown>,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "11111111-1111-1111-1111-111111111111" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));
vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ user: { id: "u1", email: "architect@gmail.com" }, permissions: ["run:create", "run:trigger"] }),
}));
vi.mock("@/hooks/use-agent-chat", () => ({
  useAgentChat: () => ({
    busy: false, messages: [], sessions: [{ id: "c1", title: "Handover chat" }], sessionId: "c1", attachments: [],
    send: vi.fn(), cancel: vi.fn(), newChat: vi.fn(), selectSession: vi.fn(),
    attachFiles: vi.fn(), removeAttachment: vi.fn(), documents: [],
  }),
}));
vi.mock("@/components/app/doc-target-dialog", () => ({ DocTargetDialog: () => null }));
vi.mock("@/components/app/model-selector", () => ({ ModelSelector: () => null }));
vi.mock("@/components/app/stage-version-panel", () => ({ StageVersionPanel: () => null }));
vi.mock("@/components/app/document-list", () => ({
  DocumentList: (props: Record<string, unknown>) => {
    state.listProps = props;
    const items = (props.items as { id: string; title: string }[] | null) ?? [];
    const onSelect = props.onSelect as ((a: unknown) => void) | undefined;
    return (
      <ul>
        {items.map((a) => (
          <li key={a.id}><button type="button" onClick={() => onSelect?.(a)}>{a.title}</button></li>
        ))}
      </ul>
    );
  },
}));
vi.mock("@/components/app/document-report-view", () => ({
  hasReportView: (d: { documentId?: string | null }) => !!d.documentId,
  DocumentReportView: ({ doc, status, actions }: { doc: { documentId: string }; status?: string; actions?: React.ReactNode }) => {
    state.opened.push(doc.documentId);
    return <section aria-label="Open document"><p>{status}</p>{actions}</section>;
  },
}));
vi.mock("@/components/app/agent-chat-drawer", () => ({
  AgentChatDrawer: (props: Record<string, unknown>) => { state.drawer = props; return null; },
}));
vi.mock("@/lib/api/projects", () => ({ getProject: async () => ({ id: "p", name: "TEST Project", displayName: "TEST Project" }) }));
vi.mock("@/lib/api/documentation", () => ({
  getPreparedDocs: async () => state.prepared,
  getDocSet: async () => ({ documents: [], doc_list: [], pr_url: null,
    context: { repo_name: "QuickLink", ado_project: "QuickLink", mode: "branch", source_branch: "main", languages: [], upstream_summary: "" } }),
}));
vi.mock("@/lib/api/capabilities", () => ({ getMyAgentAccess: async () => ({}) }));
vi.mock("@/lib/api/artifacts", () => ({
  listArtifacts: async () => [{
    id: "doc-1", projectId: "p", runId: "r1", type: "document", scope: "agent", stage: "documentation",
    title: "handover-quicklink.docx", status: state.status, version: 1, contentHash: "h".repeat(64),
    body: { kind: "document", filename: "handover-quicklink.docx", stored: true },
    phase: "documentation", createdBy: "agent", createdAt: "2026-09-16T12:00:00Z", updatedAt: "2026-09-16T12:00:00Z",
    approvedBy: null, approvedAt: null, downloadUrl: "/api/artifacts/doc-1/download",
  }],
  submitArtifact: async (id: string) => { state.submitted.push(id); return {}; },
}));

import DocumentationPage from "@/app/(app)/projects/[id]/documentation/page";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><DocumentationPage /></QueryClientProvider>);
}

beforeEach(() => {
  state.drawer = null; state.listProps = null; state.opened = []; state.submitted = []; state.status = "draft";
  state.prepared = { status: "ready", mode: "branch", repo_name: "QuickLink", ado_project: "QuickLink", branch: "main",
    head_sha: "082f91e", languages: ["JavaScript"], upstream_summary: "" };
});
afterEach(cleanup);

describe("Documentation page", () => {
  it("keeps the prepared workspace across a refresh", async () => {
    renderPage();
    expect(await screen.findByText("QuickLink @ main")).toBeTruthy();
    expect(screen.queryByText("No documentation workspace yet")).toBeNull();
    await waitFor(() => expect(state.drawer?.disabledReason).toBeUndefined());
  });

  it("opens a document from the panel with its approval state, and raises it", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "handover-quicklink.docx" }));
    const doc = await screen.findByRole("region", { name: "Open document" });
    expect(state.opened).toContain("doc-1");
    expect(doc.textContent).toContain("Draft · not yet raised for approval");
    fireEvent.click(screen.getByRole("button", { name: "Raise for approval" }));
    await waitFor(() => expect(state.submitted).toEqual(["doc-1"]));
  });

  it("offers no Raise for a document already raised", async () => {
    state.status = "pending";
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "handover-quicklink.docx" }));
    const doc = await screen.findByRole("region", { name: "Open document" });
    expect(doc.textContent).toContain("Raised for approval · waiting on the approver");
    expect(screen.queryByRole("button", { name: "Raise for approval" })).toBeNull();
  });

  it("lists the stage's documents once, beside the document, and gives the chat its history", async () => {
    renderPage();
    await screen.findByRole("button", { name: "handover-quicklink.docx" });
    expect(screen.getByRole("complementary", { name: "Documents" })).toBeTruthy();
    expect(state.listProps?.stage).toBe("documentation");
    expect(screen.queryByText(/document\(s\) generated/)).toBeNull();
    expect(state.drawer?.sessions).toEqual([{ id: "c1", title: "Handover chat" }]);
    expect(typeof state.drawer?.onSelectSession).toBe("function");
    expect(typeof state.drawer?.onNewChat).toBe("function");
  });
});
