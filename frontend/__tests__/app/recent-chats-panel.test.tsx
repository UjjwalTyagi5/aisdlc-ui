// @vitest-environment jsdom
/**
 * The panel that replaced Recent runs on the project overview.
 *
 * WHAT IT HAS TO GET RIGHT, and what the href test next door cannot see: the rows have
 * to render from a real session payload, each one has to be a link back into its own
 * conversation, and the list has to scroll inside itself. That last one is not a detail
 * — this panel sits above the artifacts list on a page that already scrolls, and a
 * ten-row list that grows to full height pushes everything below it off the screen.
 */
import * as React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { RecentChatsList } from "@/components/app/recent-chats-list";
import type { ProjectId } from "@/lib/schemas";

const PROJECT = "11111111-1111-1111-1111-111111111111" as ProjectId;

const SESSIONS = [
  {
    id: "aaaaaaaa-0000-0000-0000-000000000001",
    title: "hi can you generate a brd",
    agent_id: "requirement",
    created_at: "2026-09-08T19:15:31Z",
    updated_at: "2026-09-09T05:20:01Z",
  },
  {
    id: "aaaaaaaa-0000-0000-0000-000000000002",
    title: "attach-check.md",
    agent_id: "deployment",
    created_at: "2026-09-09T06:00:00Z",
    updated_at: "2026-09-09T06:32:37Z",
  },
  {
    id: "aaaaaaaa-0000-0000-0000-000000000003",
    title: "",
    agent_id: "code_review",
    created_at: "2026-09-07T10:00:00Z",
    updated_at: null,
  },
];

afterEach(cleanup);

describe("the recent chats panel", () => {
  it("lists a conversation per row, whichever agent it was with", () => {
    render(<RecentChatsList projectId={PROJECT} sessions={SESSIONS} />);

    expect(screen.getByText("hi can you generate a brd")).toBeDefined();
    expect(screen.getByText("attach-check.md")).toBeDefined();
    // The agent is named, because "which agent was that?" is the first thing you ask
    // of a list that deliberately mixes them.
    expect(screen.getByText("Requirements")).toBeDefined();
    expect(screen.getByText("Deployment")).toBeDefined();
    expect(screen.getByText("Code Review")).toBeDefined();
  });

  it("every row links into its own conversation", () => {
    render(<RecentChatsList projectId={PROJECT} sessions={SESSIONS} />);

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(3);
    expect(links[0]!.getAttribute("href")).toBe(
      `/projects/${PROJECT}/requirements?session=${SESSIONS[0]!.id}`,
    );
    expect(links[1]!.getAttribute("href")).toBe(
      `/projects/${PROJECT}/deployment?session=${SESSIONS[1]!.id}`,
    );
    // `code_review` is one of the two ids that does not match its route.
    expect(links[2]!.getAttribute("href")).toBe(
      `/projects/${PROJECT}/code-review?session=${SESSIONS[2]!.id}`,
    );
  });

  it("scrolls inside itself instead of growing the page", () => {
    const { container } = render(
      <RecentChatsList projectId={PROJECT} sessions={SESSIONS} />,
    );

    const list = container.querySelector("ul");
    expect(list?.className).toContain("overflow-y-auto");
    expect(list?.className).toMatch(/max-h-/);
  });

  it("an untitled session still reads as something", () => {
    render(<RecentChatsList projectId={PROJECT} sessions={[SESSIONS[2]!]} />);

    // "New chat" is what the backend calls a session nobody renamed; a blank line
    // would be a row you cannot tell apart from the next one.
    expect(screen.getByText("New chat")).toBeDefined();
  });

  it("says so when there is nothing yet, rather than rendering an empty card", () => {
    render(<RecentChatsList projectId={PROJECT} sessions={[]} />);

    expect(screen.getByText("No chats yet")).toBeDefined();
  });

  it("shows a loading state rather than an empty one while it waits", () => {
    const { container } = render(
      <RecentChatsList projectId={PROJECT} sessions={null} />,
    );

    expect(screen.queryByText("No chats yet")).toBeNull();
    expect(container.querySelector("ul")).toBeNull();
  });
});
