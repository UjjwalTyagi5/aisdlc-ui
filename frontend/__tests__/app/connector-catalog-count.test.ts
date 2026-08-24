/**
 * The dashboard's "N available" and the Integrations page's tiles must count the
 * same universe.
 *
 * They didn't: the dashboard read its total from the backend's connector
 * accept-set (eleven kinds, including azure_repos and the two SSO kinds) while
 * the page rendered one tile per catalogue category (eight). An Org Admin was
 * told "0 of 11 available" above a page showing eight connectors, with no way to
 * find the missing three.
 *
 * The catalogue now lives in one place per runtime. These tests pin the three
 * copies together — the shared constant, the page's category grouping that
 * decides tile order, and the mock rollup — so a kind added to one of them and
 * not the others fails here rather than as a number nobody can account for.
 */
import { describe, expect, it } from "vitest";

import { CONNECTOR_CATALOG_KINDS } from "@/lib/connectors";
import { buildOrgOverview } from "@/lib/mock/org-overview-fixtures";

/** The tile grouping from app/(app)/integrations/page.tsx, flattened.
 *  Kept as a literal: the page's CATEGORIES is module-private to that client
 *  component, and importing the page here would drag React rendering into a
 *  data test. If the page's grouping changes, this list changes with it — which
 *  is the drift the assertion below is here to catch. */
const TILE_KINDS = [
  "jira",
  "azure_devops",
  "github",
  "github_actions",
  "slack",
  "ms_teams",
  "sharepoint",
  "figma",
  "confluence",
  "sonarqube",
];

describe("connector catalogue", () => {
  it("lists exactly the kinds the Integrations page renders as tiles", () => {
    expect([...CONNECTOR_CATALOG_KINDS].sort()).toEqual([...TILE_KINDS].sort());
  });

  it("excludes kinds the API accepts but the page shows no tile for", () => {
    // azure_repos is folded into the consolidated Azure DevOps tile; the SSO
    // kinds are identity plumbing, configured elsewhere. Counting them is what
    // produced the phantom three.
    for (const absent of ["azure_repos", "sso_okta", "sso_entra"]) {
      expect(CONNECTOR_CATALOG_KINDS).not.toContain(absent);
    }
  });

  it("is what the org rollup reports as the total, not the seeded fixture count", () => {
    const overview = buildOrgOverview(null);
    expect(overview.connectorTotalCount).toBe(CONNECTOR_CATALOG_KINDS.length);
  });

  it("never reports more connected than the catalogue holds", () => {
    const overview = buildOrgOverview(null);
    expect(overview.connectorCount).toBeLessThanOrEqual(overview.connectorTotalCount);
  });
});
