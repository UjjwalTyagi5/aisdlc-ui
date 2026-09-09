// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The Orchestrator's composer can attach files, and a chip means the file is STORED.
 *
 * The failure this is written against is not a missing button. It is the one that looks
 * like success: a file that appears as a chip and never reaches the agent. The user then
 * reads the agent's answer as informed, which is strictly worse than no attach control
 * at all — so the chips here are rendered from what the SERVER returned, never from the
 * local `File` objects the browser picked.
 *
 * The other half is the run. The upload route resolves the run through
 * `_get_run_or_404`, so a file cannot be stored against a run that does not exist yet —
 * and this cockpit creates runs LAZILY, on the first turn. Attaching before typing
 * anything therefore has to mint the run first, which is what
 * `it("creates the run before uploading…")` pins. Without it the first attachment of
 * every conversation 404s.
 */

const sendTurn = vi.fn();

vi.mock("@/lib/orchestrator/use-orchestrator-socket", () => ({
  useOrchestratorSocket: () => ({
    messages: [],
    send: sendTurn,
    connState: "connected" as const,
    activeAgent: null,
    error: null,
    busy: false,
    activity: [],
    deliverables: [],
    reset: () => {},
    hydrate: () => {},
  }),
}));

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({
    user: { id: "u1", name: "Ana", email: "ana@example.com", initials: "A" },
    tenant: { id: "t1", name: "ABC Bank", plan: "pro" },
    permissions: ["artifact:view"],
  }),
}));

const accessScope = { role: "project_admin" as string | null, isLoading: false };
vi.mock("@/hooks/use-access-scope", () => ({ useAccessScope: () => accessScope }));

vi.mock("@/lib/api/projects", () => ({
  getProject: vi.fn(async () => ({
    id: "p1", name: "Core ledger", track: "greenfield", workspaceId: "w1",
  })),
  listProjects: vi.fn(async () => ({ items: [], total: 0, page: 1, pageSize: 100 })),
}));

vi.mock("@/lib/api/models", () => ({
  getModelOptions: vi.fn(async () => ({
    options: [{
      offering_id: "off-1", provider_id: "prov-1", display_name: "Anthropic",
      provider: "anthropic", model_id: "claude-opus-4-5", is_default: true,
    }],
    default_offering_id: "off-1",
    default_model_id: null,
  })),
}));

vi.mock("@/lib/api/conversations", () => ({
  listConversations: vi.fn(async () => []),
  getConversationMessages: vi.fn(async () => []),
  renameConversation: vi.fn(async () => ({})),
  deleteConversation: vi.fn(async () => ({})),
}));

const createRun = vi.fn(async () => ({ runId: "run-1" }));

/**
 * A stored file, as both endpoints describe it.
 *
 * The two mocks below share this list on purpose: on the real server an upload is
 * immediately visible to `GET /runs/{id}/attachments`
 * (`test_attachment_upload_api.py::test_the_listing_returns_what_was_uploaded` pins
 * that). A stub whose listing ignored its own uploads would force the component to
 * render chips from the local `File` objects instead — which is exactly the bug this
 * file exists to prevent, invented by the test rather than found by it.
 */
interface StoredFile {
  name: string;
  url: string;
  content_type: string;
  size: number;
  path: string;
}
let stored: StoredFile[] = [];

const uploadRunAttachments = vi.fn(async (_runId: string, files: File[]) => {
  const refs = files.map((f) => ({
    name: f.name,
    url: `/generated/u1/attachments/run-1/${f.name}`,
    content_type: "text/markdown",
    size: 12,
    path: `/files/u1/attachments/run-1/${f.name}`,
  }));
  stored = [...stored, ...refs];
  return refs;
});
const listRunAttachments = vi.fn(async () => stored);

vi.mock("@/lib/api/runs", () => ({
  createRun: (...a: unknown[]) => createRun(...(a as [])),
  uploadRunAttachments: (...a: unknown[]) =>
    uploadRunAttachments(...(a as unknown as [string, File[]])),
  listRunAttachments: (...a: unknown[]) => listRunAttachments(...(a as [])),
}));

vi.mock("@/components/orchestrator/model-picker", () => ({
  ModelPicker: () => <div data-testid="model-picker" />,
}));

import { ApiRequestError } from "@/lib/api/client";
import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";

function renderCockpit() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OrchestratorCockpit lockedProjectId="p1" variant="embedded" />
    </QueryClientProvider>,
  );
}

function sendMessage(text: string) {
  const composer = screen.getByRole("textbox", { name: /message the orchestrator/i });
  fireEvent.change(composer, { target: { value: text } });
  fireEvent.keyDown(composer, { key: "Enter" });
}

function attach(...files: File[]) {
  const input = screen.getByTestId("orchestrator-attach-input");
  fireEvent.change(input, { target: { files } });
}

const brd = () => new File(["# BRD"], "brd.md", { type: "text/markdown" });

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  sendTurn.mockClear();
  createRun.mockClear();
  uploadRunAttachments.mockClear();
  listRunAttachments.mockClear();
  stored = [];
  accessScope.role = "project_admin";
});

afterEach(cleanup);

describe("Orchestrator composer attachments", () => {
  it("offers an attach control, like the standalone agent chats do", async () => {
    renderCockpit();
    expect(
      await screen.findByRole("button", { name: /attach files/i }),
    ).toBeInTheDocument();
  });

  it("creates the run before uploading, because the route resolves a real run", async () => {
    // Runs are minted lazily on the first turn. A file attached before anything is
    // typed has no run to belong to, and `POST /runs/{id}/attachments` resolves the
    // run through `_get_run_or_404` — so without this the first attachment of every
    // conversation 404s while the chip appears anyway.
    renderCockpit();
    await screen.findByRole("button", { name: /attach files/i });

    attach(brd());

    await waitFor(() => expect(uploadRunAttachments).toHaveBeenCalledTimes(1));
    expect(createRun).toHaveBeenCalledTimes(1);
    expect(uploadRunAttachments.mock.calls[0]![0]).toBe("run-1");
  });

  it("uploads to the run's endpoint, not the conversation's", async () => {
    // `POST /conversations/{id}/attachments` authorises through the conversation row,
    // which the SOCKET creates on the first turn — so it 404s on exactly the path this
    // feature needs. The run route holds from the moment the run exists.
    renderCockpit();
    await screen.findByRole("button", { name: /attach files/i });

    attach(brd());

    await waitFor(() => expect(uploadRunAttachments).toHaveBeenCalled());
    const [runId, files] = uploadRunAttachments.mock.calls[0]!;
    expect(runId).toBe("run-1");
    expect((files as File[]).map((f) => f.name)).toEqual(["brd.md"]);
  });

  it("shows a chip only after the server confirms the file was stored", async () => {
    renderCockpit();
    await screen.findByRole("button", { name: /attach files/i });

    attach(brd());

    expect(await screen.findByText("brd.md")).toBeInTheDocument();
  });

  it("refreshes the chips when a second file is attached to the same run", async () => {
    // Found by mutation: dropping the post-upload refresh left every test green,
    // because the FIRST upload creates the run and the chip query starts on that
    // transition. On a run that already exists nothing re-reads the list, so the
    // second file uploads successfully and never appears — a file the agent has and
    // the user cannot see, which is the same class of lie as the reverse.
    renderCockpit();
    await screen.findByRole("button", { name: /attach files/i });

    attach(brd());
    await screen.findByText("brd.md");

    attach(new File(["notes"], "risks.md", { type: "text/markdown" }));
    expect(await screen.findByText("risks.md")).toBeInTheDocument();
    expect(screen.getByText("brd.md")).toBeInTheDocument();
  });

  it("shows no chip and says why when the upload fails", async () => {
    // THE LOAD-BEARING ONE. A chip rendered from the local File object would appear
    // whether or not the file was stored, and the user would then believe the agent
    // had a document the agent has never seen.
    //
    // A REAL `ApiRequestError` carrying FastAPI's real body shape, not a plain Error.
    // That distinction is not decoration: the class unwraps `{detail: "…"}` into
    // `.message` itself, and a stubbed `new Error("…")` would pass just as happily
    // against a component that read a `.detail` property which does not exist — the
    // user would then see "Bad Request" in place of the store's own wording. That is
    // exactly the mistake this test was written after making.
    uploadRunAttachments.mockRejectedValueOnce(
      new ApiRequestError(
        400,
        { detail: "File type '.exe' is not accepted. Allowed: .csv, .doc, .docx" },
        "Bad Request",
      ),
    );
    renderCockpit();
    await screen.findByRole("button", { name: /attach files/i });

    attach(new File(["MZ"], "payload.exe", { type: "application/octet-stream" }));

    const error = await screen.findByText(/not accepted/i);
    expect(error).toBeInTheDocument();
    expect(error.textContent).toMatch(/\.exe/);
    expect(error.textContent).not.toMatch(/bad request/i);
    expect(screen.queryByText("payload.exe")).toBeNull();
  });

  it("lists the files already on a run, so a reopened chat shows what the agent has", async () => {
    // The attachments keep reaching every turn whether or not the composer draws them.
    // Chips that vanish on reload describe a prompt that is not the one being sent.
    stored = [{
      name: "earlier.md",
      url: "/generated/u1/attachments/run-1/earlier.md",
      content_type: "text/markdown",
      size: 4,
      path: "/files/u1/attachments/run-1/earlier.md",
    }];
    renderCockpit();
    await screen.findByRole("button", { name: /attach files/i });

    // Minting the run is what makes the listing addressable.
    attach(brd());

    expect(await screen.findByText("earlier.md")).toBeInTheDocument();
  });

  it("tells the user attachments stay with the run rather than one message", async () => {
    // The backend scopes attachments to the RUN, not the turn they were attached to,
    // because this engine has no agent order. The composer must not imply otherwise —
    // a user who thinks a file is per-message will re-upload it for every agent.
    renderCockpit();
    await screen.findByRole("button", { name: /attach files/i });
    attach(brd());
    await screen.findByText("brd.md");

    const region = screen.getByTestId("orchestrator-attachments");
    expect(region.textContent).toMatch(/every agent|this run|all agents/i);
  });

  it("closes the attach control to a reader who cannot drive the Orchestrator", async () => {
    accessScope.role = "ba";
    renderCockpit();
    const button = await screen.findByRole("button", { name: /attach files/i });
    expect(button).toBeDisabled();
  });
});

void React;

/**
 * The composer chips answer "what does this run hold?". Once a turn has gone, the user
 * is looking at their own message asking a narrower question — "did the PRD go with
 * THAT one?" — and the run-level list cannot answer it: it looks identical whether the
 * file went with this turn or arrived two turns later.
 *
 * So the turn carries the names of the files that were attached SINCE the last one, and
 * the thread stamps them on the bubble. Which is also why they are not re-sent: the
 * backend does feed every stored attachment into every turn, but a name repeated on
 * five consecutive bubbles reads as five uploads.
 */
describe("what a sent turn says it carried", () => {
  it("carries the file that was attached before it", async () => {
    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });

    attach(brd());
    await screen.findByTestId("orchestrator-attachments");

    sendMessage("turn this into stories");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    const turn = sendTurn.mock.calls[0]![0] as {
      attachments?: Array<{ name: string; url: string }>;
    };
    expect(turn.attachments).toEqual([
      { name: "brd.md", url: "/generated/u1/attachments/run-1/brd.md" },
    ]);
  });

  it("does not repeat the same file on the next turn", async () => {
    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });

    attach(brd());
    await screen.findByTestId("orchestrator-attachments");

    sendMessage("turn this into stories");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    sendMessage("now the acceptance criteria");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(2));

    const second = sendTurn.mock.calls[1]![0] as {
      attachments?: Array<{ name: string; url: string }>;
    };
    expect(second.attachments ?? []).toEqual([]);
  });

  it("carries only the newly attached file when a second one follows", async () => {
    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });

    attach(brd());
    await screen.findByTestId("orchestrator-attachments");
    sendMessage("stories, please");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    attach(new File(["cols"], "scope.xlsx", { type: "application/vnd.ms-excel" }));
    await waitFor(() =>
      expect(screen.getByTestId("orchestrator-attachments").textContent).toContain(
        "scope.xlsx",
      ),
    );

    sendMessage("and now against this scope");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(2));

    const second = sendTurn.mock.calls[1]![0] as {
      attachments?: Array<{ name: string; url: string }>;
    };
    expect(second.attachments).toEqual([
      { name: "scope.xlsx", url: "/generated/u1/attachments/run-1/scope.xlsx" },
    ]);
  });

  it("carries nothing when the turn had no file attached at all", async () => {
    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });

    sendMessage("what do you make of the ledger?");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    const turn = sendTurn.mock.calls[0]![0] as {
      attachments?: Array<{ name: string; url: string }>;
    };
    expect(turn.attachments ?? []).toEqual([]);
  });
});

/**
 * The chip has to still be there after the turn goes.
 *
 * Reported from a live run: the BRD attached, the agent read it, and neither the
 * composer chip nor the name on the sent message was on screen. The backend held the
 * file and had answered the listing with it, so whatever dropped it did so on this
 * side — and the tell in the server log was a SECOND `POST /runs`, which only happens
 * when `runIdRef` has been cleared. Clearing it disables the chips query, whose data
 * lives under the old run's key, so the chips vanish and nothing refetches.
 */
describe("the chips outlive the turn", () => {
  it("keeps the chip on screen after the message is sent", async () => {
    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });

    attach(brd());
    await screen.findByTestId("orchestrator-attachments");

    sendMessage("turn this into stories");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    expect(screen.getByTestId("orchestrator-attachments").textContent).toContain("brd.md");
  });

  it("mints exactly one run across attaching and then sending", async () => {
    // The second POST /runs in the live log is the symptom, not the cause: a run
    // created after the upload has no attachments, and the chips query moves to a key
    // nothing has ever filled.
    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });

    attach(brd());
    await screen.findByTestId("orchestrator-attachments");
    sendMessage("and now the criteria");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    expect(createRun).toHaveBeenCalledTimes(1);
    const turn = sendTurn.mock.calls[0]![0] as { resolveRunId: () => Promise<string> };
    await expect(turn.resolveRunId()).resolves.toBe("run-1");
    expect(createRun).toHaveBeenCalledTimes(1);
  });
});
