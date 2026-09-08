// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The Documents panel is reachable on a stage that has never run.
 *
 * THE BUG, FOUR TIMES OVER. Every hand-built stage page opens with a full-page empty
 * state — "No scan yet", "No review yet", "No deployment yet", "No documentation
 * workspace yet" — and puts its tab bar or sidebar in the OTHER branch. Dropping a
 * Documents tab into that branch put it behind the very condition it does not depend
 * on: no scan, no Documents. On a brand-new project, which is every project on the day
 * somebody uploads the first document, the panel did not exist.
 *
 * It is invisible in review because the tab is right there in the source, correctly
 * wired, one `? :` away from being unreachable. And it is invisible in manual testing
 * on any project that HAS run the stage. It reproduces only on an empty one.
 *
 * A document is a blob somebody approved into the project's record. It has no
 * dependency on a scan, a review, a release or a docs workspace — that is the whole
 * premise of the feature, and it is exactly what the page structure contradicted.
 *
 * WHY IT RENDERS THE REAL PAGES. The bug is entirely in JSX structure: which branch
 * holds the panel. Any test asserting on props, imports or a helper would have passed
 * on all four broken pages, because nothing was wrong with any of those.
 */

const documentStages = vi.hoisted(() => ({ seen: [] as string[] }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "11111111-1111-1111-1111-111111111111" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ user: { id: "u1", email: "bruno@abcbank.com" }, permissions: ["*"] }),
}));

vi.mock("@/hooks/use-agent-chat", () => ({
  useAgentChat: () => ({
    busy: false, messages: [], sessions: [], sessionId: null, attachments: [],
    send: vi.fn(), newChat: vi.fn(), selectSession: vi.fn(),
    attachFiles: vi.fn(), removeAttachment: vi.fn(), documents: [],
  }),
}));

/** The sentinel. The panel's own behaviour is `document-list-filtering.test.tsx`'s
 *  subject; all this file asks is whether the page renders it at all. */
vi.mock("@/components/app/document-list", () => ({
  DocumentList: ({ stage }: { stage?: string }) => {
    documentStages.seen.push(stage ?? "project-wide");
    return <div data-testid="documents">Documents</div>;
  },
}));

// Dialogs, drawers and heavy children are not what this is about.
vi.mock("@/components/app/agent-chat-drawer", () => ({ AgentChatDrawer: () => null }));
vi.mock("@/components/app/scan-target-dialog", () => ({ ScanTargetDialog: () => null }));
vi.mock("@/components/app/review-target-dialog", () => ({ ReviewTargetDialog: () => null }));
vi.mock("@/components/app/deploy-target-dialog", () => ({ DeployTargetDialog: () => null }));
vi.mock("@/components/app/doc-target-dialog", () => ({ DocTargetDialog: () => null }));
vi.mock("@/components/app/deployment-approvals", () => ({ DeploymentApprovals: () => null }));
vi.mock("@/components/app/stage-version-panel", () => ({ StageVersionPanel: () => null }));
vi.mock("@/components/app/markdown-message", () => ({ MarkdownMessage: () => null }));
vi.mock("@/components/app/code-viewer", () => ({ CodeViewer: () => null }));
vi.mock("@/components/app/model-selector", () => ({ ModelSelector: () => null }));

// The empty stage. Every list is empty and every detail fetch finds nothing — the
// state a project is in before anybody has run the agent.
vi.mock("@/lib/api/projects", () => ({
  getProject: async () => ({ id: "p", name: "Test Demo", displayName: "Test Demo" }),
}));
vi.mock("@/lib/api/security", () => ({ listScans: async () => [], getScan: async () => null }));
vi.mock("@/lib/api/code-review", () => ({ listReviews: async () => [], getReview: async () => null }));
vi.mock("@/lib/api/deployment", () => ({
  getPreparedDeploy: async () => null, getRelease: async () => null,
}));
vi.mock("@/lib/api/documentation", () => ({ getDocSet: async () => null }));

import SecurityPage from "@/app/(app)/projects/[id]/security/page";
import CodeReviewPage from "@/app/(app)/projects/[id]/code-review/page";
import DeploymentPage from "@/app/(app)/projects/[id]/deployment/page";
import DocumentationPage from "@/app/(app)/projects/[id]/documentation/page";

afterEach(() => {
  documentStages.seen = [];
  cleanup();
});

function renderPage(Page: React.ComponentType) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Page />
    </QueryClientProvider>,
  );
}

/** [page, component, the empty state it opens on, the stage it must scope documents to] */
const PAGES: [string, React.ComponentType, RegExp, string][] = [
  ["Security", SecurityPage, /No scan yet/i, "security"],
  ["Code Review", CodeReviewPage, /No review yet/i, "code_review"],
  ["Deployment", DeploymentPage, /No deployment yet/i, "deployment"],
  ["Documentation", DocumentationPage, /No documentation workspace yet/i, "documentation"],
];

describe.each(PAGES)("%s, on a project that has never run it", (name, Page, emptyCopy, stage) => {
  it("still renders the Documents panel", async () => {
    renderPage(Page);

    expect(await screen.findAllByTestId("documents")).not.toHaveLength(0);
  });

  it("scopes it to this stage, not to the whole project", async () => {
    /** Getting the panel onto the page is half of it. Passing the wrong stage — or
     *  omitting it, which reads as project-wide — would show another agent's documents
     *  here and hide this one's. */
    renderPage(Page);
    await screen.findAllByTestId("documents");

    expect(documentStages.seen).toContain(stage);
  });

  it("is genuinely the empty stage, not a page that quietly rendered its data", async () => {
    /** NON-VACUITY, and the load-bearing assertion in this file. If the mocks ever stop
     *  producing the empty state — a renamed api module, a changed `prepared` check —
     *  the page falls into its populated branch, where the panel has always worked, and
     *  the two tests above would pass while testing nothing at all. */
    renderPage(Page);

    expect(await screen.findByText(emptyCopy)).toBeTruthy();
  });
});
