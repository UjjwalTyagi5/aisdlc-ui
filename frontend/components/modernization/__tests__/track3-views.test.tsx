// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AssessmentView } from "@/components/modernization/assessment-view";
import { MigrationBriefCard } from "@/components/modernization/migration-brief-card";
import {
  DiscoveryResponse,
  MigrationIntentResponse,
} from "@/lib/schemas/modernization";

import fixtures from "./fixtures.json";

/**
 * Track 3's two views, rendered against payloads the BACKEND produced
 * (`fixtures.json` is `assess_repository` + `MigrationIntentArtifact` output for the
 * legacy fixture repository), so a drift between the stored shape and these schemas
 * fails here instead of blanking a page.
 */
afterEach(cleanup);

const assessment = DiscoveryResponse.parse({
  projectId: "p1", runId: "r1", updatedAt: "2026-09-10T09:00:00Z", payload: fixtures.assessment,
}).payload!;
const brief = MigrationIntentResponse.parse({
  projectId: "p1", runId: "r1", updatedAt: "2026-09-10T08:00:00Z", payload: fixtures.brief,
}).payload!;

describe("the stored shapes", () => {
  it("parse without loss of the modules or the brief's intake", () => {
    expect(assessment.modules).toHaveLength(5);
    expect(assessment.summary.tier_counts.manual).toBe(1);
    expect(brief.target_state.stack).toBe(".NET 8 on Azure App Service");
  });

  it("tolerates an empty payload envelope", () => {
    expect(DiscoveryResponse.parse({ projectId: "p", runId: null, updatedAt: null, payload: null }).payload).toBeNull();
  });
});

describe("the migration brief", () => {
  it("shows why, from → to, scope, constraints and success criteria", () => {
    render(<MigrationBriefCard brief={brief} updatedAt="2026-09-10T08:00:00Z" />);
    expect(screen.getByRole("heading", { name: "Billing" })).toBeTruthy();
    expect(screen.getByText(".NET Framework 4.5.2 is out of support")).toBeTruthy();
    expect(screen.getByText(".NET Framework 4.5.2 WebForms on IIS")).toBeTruthy();
    expect(screen.getByText(".NET 8 on Azure App Service")).toBeTruthy();
    expect(screen.getByText("Live before 31 March")).toBeTruthy();
    expect(screen.getByText("Identical invoices for recorded Q1 inputs")).toBeTruthy();
    expect(screen.getByText("Reporting database")).toBeTruthy();
  });
});

describe("the brief's recorded time", () => {
  it("is when the BRIEF was recorded, not when its run last changed", () => {
    // Found live: Discovery wrote to the same Orchestrator run a minute later, and the
    // card showed that time as "Recorded".
    const recorded = "2026-09-10T16:05:53Z";
    render(
      <MigrationBriefCard brief={{ ...brief, recorded_at: recorded }} updatedAt="2026-09-10T16:06:58Z" />,
    );
    expect(screen.getByText(`Recorded ${new Date(recorded).toLocaleString()}`)).toBeTruthy();
  });

  it("falls back to the run's time for a brief saved without one", () => {
    const updated = "2026-09-10T16:06:58Z";
    render(<MigrationBriefCard brief={{ ...brief, recorded_at: null }} updatedAt={updated} />);
    expect(screen.getByText(`Recorded ${new Date(updated).toLocaleString()}`)).toBeTruthy();
  });
});

describe("the assessment", () => {
  it("summarises tiers and flags", () => {
    render(<AssessmentView assessment={assessment} />);
    const summary = screen.getByRole("region", { name: "Assessment summary" });
    expect(within(summary).getByText("Manual-only")).toBeTruthy();
    expect(within(summary).getByText("Target:", { exact: false })).toBeTruthy();
  });

  it("filters the module list by tier", async () => {
    const user = userEvent.setup();
    render(<AssessmentView assessment={assessment} />);
    await user.click(screen.getByRole("button", { name: /Manual-only \(1\)/ }));
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(within(rows[0]!).getByText("Billing.Web")).toBeTruthy();
  });

  it("explains a module's score when it is selected", async () => {
    const user = userEvent.setup();
    render(<AssessmentView assessment={assessment} />);
    await user.click(screen.getByText("Billing.Web"));
    const detail = screen.getByRole("complementary", { name: "Billing.Web detail" });
    expect(within(detail).getByText(/Risk \d+\/100/)).toBeTruthy();
    expect(within(detail).getByText("platform")).toBeTruthy(); // the factor, by its exact name
    expect(within(detail).getByText("Newtonsoft.Json", { exact: false })).toBeTruthy();
  });

  it("says when vendored library files were left out of the line count", async () => {
    const user = userEvent.setup();
    expect(assessment.summary.vendored_files).toBe(0); // the fixture carries the field
    const { unmount } = render(<AssessmentView assessment={assessment} />);
    expect(screen.queryByText(/vendored front-end/)).toBeNull();
    unmount();

    const withVendored = {
      ...assessment,
      summary: { ...assessment.summary, vendored_files: 228 },
      modules: assessment.modules.map((m) => (m.name === "Billing.Web" ? { ...m, vendored_files: 3 } : m)),
    };
    render(<AssessmentView assessment={withVendored} />);
    expect(screen.getByText(/leave out 228 vendored front-end library files/)).toBeTruthy();
    await user.click(screen.getByText("Billing.Web"));
    const detail = screen.getByRole("complementary", { name: "Billing.Web detail" });
    expect(within(detail).getByText(/3 vendored front-end files/)).toBeTruthy();
  });

  it("lists end-of-life, deprecated and vulnerable findings", async () => {
    const user = userEvent.setup();
    render(<AssessmentView assessment={assessment} />);
    await user.click(screen.getByRole("tab", { name: /Flags/ }));
    expect(await screen.findByText("End-of-life runtimes")).toBeTruthy();
    expect(screen.getByText("WindowsAzure.Storage")).toBeTruthy();
    expect(screen.getByText(/CVE-2024-21907/)).toBeTruthy();
  });
});

describe("the version-2 brief (the agent's recommendation, structured)", () => {
  // `brief_v2` is a MigrationIntentArtifact the backend produced (v2 fields, statuses
  // filled in from the pulled code) — so a drift between the stored shape and the view
  // fails here.
  const v2 = MigrationIntentResponse.parse({
    projectId: "p1", runId: "r1", updatedAt: null, payload: fixtures.brief_v2,
  }).payload!;

  it("opens with the goal and the key facts", () => {
    render(<MigrationBriefCard brief={v2} />);
    expect(screen.getByRole("heading", { name: "ClaimTrack" })).toBeTruthy();
    expect(screen.getByText(/without changing anything brokers, the bank or the regulator see/)).toBeTruthy();
    const fact = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling?.textContent;
    expect(fact("Deadline")).toBe("30 Jun 2027");
    expect(fact("Budget")).toBe("$450,000");
    expect(fact("End of life today")).toBe("3 parts"); // from the code, not the model
    expect(fact("Target stack")).toBe("Recommended");
  });

  it("shows the change at a glance: today (by support status) → target, and the kind of change", () => {
    render(<MigrationBriefCard brief={v2} />);
    const glance = screen.getByRole("region", { name: "The change at a glance" });
    const row = within(glance).getByText("Broker portal").closest("tr")!;
    expect(within(row).getByText("AngularJS 1.5 · Gulp 3 on Node 8")).toBeTruthy();
    expect(within(row).getByText("End of life")).toBeTruthy();
    expect(within(row).getByText("React 18 · TypeScript · Vite on Node 22 LTS")).toBeTruthy();
    expect(within(row).getByText("Rewrite")).toBeTruthy();
  });

  it("labels the target as the agent's recommendation, with its reasons and alternatives", () => {
    render(<MigrationBriefCard brief={v2} />);
    const rec = screen.getByRole("region", { name: "Recommended target stack" });
    expect(within(rec).getByText("Recommended by the Migration Intent agent")).toBeTruthy();
    expect(within(rec).getByText("Accepted when this brief is signed off.")).toBeTruthy();
    expect(within(rec).getByText("Azure Kubernetes Service")).toBeTruthy();
  });

  it("shows each module's change, with the change mix and effort", () => {
    render(<MigrationBriefCard brief={v2} />);
    const modules = screen.getByRole("region", { name: "What changes in each module" });
    expect(within(modules).getByLabelText("Change mix")).toBeTruthy();
    expect(within(modules).getByText("4 upgrade")).toBeTruthy();
    expect(within(modules).getByText("1 rewrite")).toBeTruthy();
    expect(within(modules).getByText("High effort")).toBeTruthy();
    expect(within(modules).getByText("javax.* → jakarta.* namespace")).toBeTruthy();
  });

  it("shows the trade-offs, the timeline in date order, and the measures", () => {
    render(<MigrationBriefCard brief={v2} />);
    const trade = screen.getByRole("region", { name: "Trade-offs" });
    expect(within(trade).getAllByText("What we gain")).toHaveLength(4);
    const timeline = screen.getByRole("region", { name: "Timeline" });
    const items = within(timeline).getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("Legacy change freeze");
    expect(items[items.length - 1]).toContain("Legacy servers decommissioned");
    const success = screen.getByRole("region", { name: "How we will measure success" });
    expect(within(success).getByText("≤ 300 ms")).toBeTruthy();
    expect(screen.getByText("Azure DevOps / Project 2")).toBeTruthy();
  });
});
