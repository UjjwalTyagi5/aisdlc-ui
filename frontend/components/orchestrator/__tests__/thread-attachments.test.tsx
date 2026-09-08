// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

/**
 * A sent turn says which files went with it.
 *
 * The composer chips already say what the RUN holds. They are not the same statement:
 * the run's list is everything ever attached, and it sits below the thread whatever the
 * conversation has since moved on to. Once a turn has gone, the user is reading their
 * own message and asking "did the PRD go with THAT?" — a question the run-level list
 * cannot answer, because it looks identical whether the file went with this turn, the
 * one before it, or is still sitting unattached.
 *
 * So the name is stamped on the message. This mirrors the standalone agent chat
 * (`agent-chat-drawer.tsx`, which renders `message.attachments` under the bubble) —
 * the same gesture in both surfaces, because it is the same question.
 */

import { Thread } from "@/components/orchestrator/thread";
import type { OrchestratorMessage } from "@/lib/orchestrator/types";

function message(over: Partial<OrchestratorMessage> = {}): OrchestratorMessage {
  return {
    id: "m1",
    role: "user",
    phase: null,
    content: "Turn this PRD into stories",
    createdAt: 0,
    ...over,
  };
}

function renderThread(messages: OrchestratorMessage[]) {
  return render(
    <Thread
      messages={messages}
      busy={false}
      placeholder="Message"
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

describe("attachments on a sent turn", () => {
  it("names the file that went with the message", () => {
    renderThread([
      message({
        attachments: [
          { name: "QuickLink_PRD.docx", url: "/generated/u1/attachments/r1/QuickLink_PRD.docx" },
        ],
      }),
    ]);

    const bubble = screen.getByTestId("message-attachments-m1");
    expect(within(bubble).getByText("QuickLink_PRD.docx")).toBeInTheDocument();
  });

  it("links the name to the stored file, so it can be opened back", () => {
    renderThread([
      message({ attachments: [{ name: "brd.md", url: "/generated/u1/attachments/r1/brd.md" }] }),
    ]);

    expect(screen.getByRole("link", { name: /brd\.md/ })).toHaveAttribute(
      "href",
      "/generated/u1/attachments/r1/brd.md",
    );
  });

  it("says nothing on a turn that carried no file", () => {
    // The run-level list is NOT the fallback here. Rendering it on every bubble would
    // tell the user a file went with a turn they sent before it existed.
    renderThread([message()]);

    expect(screen.queryByTestId("message-attachments-m1")).not.toBeInTheDocument();
  });

  it("says nothing on a turn whose list came back empty", () => {
    // The hook omits the field rather than stamping `[]`, so this is the component
    // holding its own line: `Thread` takes messages from `hydrate` too, and a replayed
    // conversation could hand it one. An empty list must not draw the rule.
    renderThread([message({ attachments: [] })]);

    expect(screen.queryByTestId("message-attachments-m1")).not.toBeInTheDocument();
  });

  it("names every file when a turn carried more than one", () => {
    renderThread([
      message({
        attachments: [
          { name: "brd.md", url: "/a/brd.md" },
          { name: "scope.xlsx", url: "/a/scope.xlsx" },
        ],
      }),
    ]);

    const bubble = screen.getByTestId("message-attachments-m1");
    expect(within(bubble).getByText("brd.md")).toBeInTheDocument();
    expect(within(bubble).getByText("scope.xlsx")).toBeInTheDocument();
  });

  it("stamps the agent's reply with nothing, even when the user's turn carried a file", () => {
    // Attachments belong to the turn that CARRIED them. An agent reply repeating the
    // name would read as the agent having produced the file.
    renderThread([
      message({ attachments: [{ name: "brd.md", url: "/a/brd.md" }] }),
      message({ id: "m2", role: "agent", content: "Six stories, drafted." }),
    ]);

    expect(screen.queryByTestId("message-attachments-m2")).not.toBeInTheDocument();
  });
});
