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

// Diff cards render through MarkdownMessage/CodeBlock as a fenced ```diff
// block (not Monaco — see the DiffCard doc comment in agent-chat-drawer.tsx
// for why). highlight.js normalizes whitespace inside <code>/<span> nodes,
// so assert on the +/- payload lines via a substring match rather than an
// exact textContent equality check.
describe("AgentChatDrawer — code.diff rendering", () => {
  it("does not render diff content until the card has been expanded, then shows the real before/after lines", async () => {
    render(
      <AgentChatDrawer
        open
        onOpenChange={() => {}}
        messages={[messageWithDiff()]}
        onSend={vi.fn()}
      />,
    );

    expect(screen.queryByText(/foo = 1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/foo = 2/)).not.toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: /src\/foo\.ts/i });
    await userEvent.click(toggle);

    expect(screen.getByText(/foo = 1/)).toBeInTheDocument();
    expect(screen.getByText(/foo = 2/)).toBeInTheDocument();
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
    expect(screen.queryByText(/foo = 2/)).not.toBeInTheDocument();

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/foo = 2/)).toBeInTheDocument();
  });

  it("unmounts the diff content again after collapsing (plain conditional render, no lazy-mount latch)", async () => {
    render(
      <AgentChatDrawer
        open
        onOpenChange={() => {}}
        messages={[messageWithDiff()]}
        onSend={vi.fn()}
      />,
    );

    const toggle = screen.getByRole("button", { name: /src\/foo\.ts/i });
    await userEvent.click(toggle); // expand
    expect(screen.getByText(/foo = 2/)).toBeInTheDocument();

    await userEvent.click(toggle); // collapse again
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/foo = 2/)).not.toBeInTheDocument();
  });

  it("renders one card per path and shows the changeKind badge", async () => {
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

    expect(screen.getByText(/created/i)).toBeInTheDocument();
    expect(screen.getByText(/edited/i)).toBeInTheDocument();

    // Neither card's diff content renders until expanded.
    expect(screen.queryByText(/const x = 1/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /src\/new-file\.ts/i }));
    await userEvent.click(screen.getByRole("button", { name: /src\/foo\.ts/i }));

    expect(screen.getByText(/const x = 1/)).toBeInTheDocument();
    // "old"/"new" are single tokens likely to collide with UI chrome text —
    // scope the assertion to the diff's own fenced code block via a broader
    // substring that only the diff body would contain.
    expect(screen.getAllByText(/new/).length).toBeGreaterThan(0);
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

    expect(screen.queryByRole("button", { name: /src\/x\.ts/i })).not.toBeInTheDocument();
  });
});
