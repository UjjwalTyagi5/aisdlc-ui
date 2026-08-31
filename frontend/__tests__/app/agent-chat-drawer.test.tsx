// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// jsdom has no ResizeObserver; the drawer's message list uses Radix
// ScrollArea, whose viewport ref effect calls `.observe()` on mount. Without
// this stub that throws during a layout effect and derails later
// interactions in the same test (e.g. a click's state update never commits).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

// Stub the (Monaco-backed) DiffViewer entirely — this test only needs to
// verify the chat drawer wires the right props into it and keeps it
// collapsed by default; DiffViewer's own rendering is covered where it's
// already used on the Code Review page.
const diffViewerSpy = vi.fn();
vi.mock("@/components/app/diff-viewer", () => ({
  DiffViewer: (props: unknown) => {
    diffViewerSpy(props);
    return <div data-testid="diff-viewer-stub">diff-viewer-stub</div>;
  },
}));

import { AgentChatDrawer, type AgentChatMessage } from "@/components/app/agent-chat-drawer";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function messageWithDiff(overrides: Partial<AgentChatMessage> = {}): AgentChatMessage {
  return {
    id: "a1",
    role: "agent",
    content: "Updated the file.",
    createdAt: "2026-08-31T12:00:00.000Z",
    diffs: [
      {
        path: "src/foo.ts",
        original: "export const foo = 1;\n",
        modified: "export const foo = 2;\n",
        changeKind: "edited",
      },
    ],
    ...overrides,
  };
}

describe("AgentChatDrawer — code.diff rendering", () => {
  it("renders a DiffViewer with the right original/modified/filename props", () => {
    render(
      <AgentChatDrawer
        open
        onOpenChange={() => {}}
        messages={[messageWithDiff()]}
        onSend={vi.fn()}
      />,
    );

    expect(diffViewerSpy).toHaveBeenCalledTimes(1);
    const props = diffViewerSpy.mock.calls[0]![0] as {
      original: string;
      modified: string;
      filename: string;
    };
    expect(props.original).toBe("export const foo = 1;\n");
    expect(props.modified).toBe("export const foo = 2;\n");
    expect(props.filename).toBe("src/foo.ts");
  });

  it("is collapsed by default and expands on click", async () => {
    render(
      <AgentChatDrawer
        open
        onOpenChange={() => {}}
        messages={[messageWithDiff()]}
        onSend={vi.fn()}
      />,
    );

    const toggle = screen.getByRole("button", { name: /src\/foo\.ts/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const stub = screen.getByTestId("diff-viewer-stub");
    expect(stub).not.toBeVisible();

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(stub).toBeVisible();
  });

  it("renders one card per path and shows the changeKind badge", () => {
    render(
      <AgentChatDrawer
        open
        onOpenChange={() => {}}
        messages={[
          messageWithDiff({
            id: "a2",
            diffs: [
              {
                path: "src/new-file.ts",
                original: "",
                modified: "export const x = 1;\n",
                changeKind: "created",
              },
              {
                path: "src/foo.ts",
                original: "old",
                modified: "new",
                changeKind: "edited",
              },
            ],
          }),
        ]}
        onSend={vi.fn()}
      />,
    );

    expect(diffViewerSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/created/i)).toBeInTheDocument();
    expect(screen.getByText(/edited/i)).toBeInTheDocument();
  });

  it("does not render diff cards for user messages", () => {
    render(
      <AgentChatDrawer
        open
        onOpenChange={() => {}}
        messages={[
          {
            id: "u1",
            role: "user",
            content: "please fix the bug",
            createdAt: "2026-08-31T12:00:00.000Z",
            // A diffs array on a user message shouldn't happen in practice, but
            // rendering only ever surfaces diffs on agent messages.
            diffs: [
              { path: "src/x.ts", original: "a", modified: "b", changeKind: "edited" },
            ],
          },
        ]}
        onSend={vi.fn()}
      />,
    );

    expect(diffViewerSpy).not.toHaveBeenCalled();
  });
});
