// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { RunAgentButton } from "@/components/app/run-agent-button";

/**
 * "Run agent" opens the Orchestrator, which only a Project Admin may use. Showing
 * it to a BA would put a dead end on the project page; the delivery roles start
 * their work from the agent tiles on Overview, which already enforce owner-only
 * access.
 */
describe("RunAgentButton", () => {
  it("links a project admin to that project's Orchestrator", () => {
    render(<RunAgentButton projectId="p1" role="project_admin" />);
    expect(screen.getByRole("link", { name: /run agent/i })).toHaveAttribute(
      "href",
      "/orchestrator?project=p1",
    );
  });

  it.each(["ba", "developer", "qa", "architect", "org_admin"] as const)(
    "renders nothing for %s",
    (role) => {
      const { container } = render(<RunAgentButton projectId="p1" role={role} />);
      expect(container).toBeEmptyDOMElement();
    },
  );
});
