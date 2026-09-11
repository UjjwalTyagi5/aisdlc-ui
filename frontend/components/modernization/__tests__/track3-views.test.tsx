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
