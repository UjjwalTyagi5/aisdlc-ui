import { describe, expect, it } from "vitest";
import { z } from "zod";

import { Connector } from "@/lib/schemas/connector";
import { ConnectorKind } from "@/lib/schemas/enums";
import { CONNECTOR_KIND_LABEL, CONNECTOR_CATALOG_KINDS } from "@/lib/connectors";

/**
 * `GET /connectors` is parsed as `z.array(Connector)`, and `kind` is an enum. So a
 * connector the backend probes but this enum does not list fails the WHOLE array —
 * the Integrations page renders nothing but SCHEMA_MISMATCH, with every other
 * connector working fine and nothing on screen to say which one was the problem.
 *
 * That is exactly what happened when the deployment agent's Azure Pipelines
 * connector was added to the backend probe (2026-09-03) and the enum was not
 * updated. The list below mirrors `_probe_all_connectors` in backend/process_api.py,
 * after `ConnectorOut.from_health_entry` canonicalises `github_issues` -> `github`.
 *
 * If a new connector is added to that probe, add it here and to `ConnectorKind`.
 */
const KINDS_THE_BACKEND_PROBES = [
  "azure_devops",
  "jira",
  "github", // canonicalised from the internal "github_issues"
  "azure_repos",
  "azure_pipelines",
  "slack",
  "github_actions",
  "ms_teams",
  "sharepoint",
  "figma",
  "confluence",
  "sonarqube",
] as const;

describe("connector kind contract", () => {
  it.each(KINDS_THE_BACKEND_PROBES)("accepts %s, which the backend probes", (kind) => {
    expect(ConnectorKind.safeParse(kind).success).toBe(true);
  });

  it("parses a full connector list containing every probed kind", () => {
    const payload = KINDS_THE_BACKEND_PROBES.map((kind) => ({
      id: kind,
      tenantId: "dfee0d2f-345e-430e-8084-7ab7276cc5b8",
      kind,
      name: kind,
      installed: true,
      health: "disconnected",
      capabilities: [],
      lastCheckedAt: "2026-09-04T05:45:58.712530+00:00",
      account: null,
      granted: false,
    }));

    const parsed = z.array(Connector).safeParse(payload);

    expect(parsed.success).toBe(true);
    expect(parsed.success && parsed.data).toHaveLength(KINDS_THE_BACKEND_PROBES.length);
  });

  it("names every kind it accepts, so no connector renders as a raw slug", () => {
    for (const kind of ConnectorKind.options) {
      expect(CONNECTOR_KIND_LABEL[kind], `no label for ${kind}`).toBeTruthy();
    }
  });

  it("keeps Azure DevOps plumbing out of the presented catalogue", () => {
    // One credential covers boards, repos and CI/CD, so these are accepted by the
    // API but folded into the single Azure DevOps tile rather than shown alone.
    expect(CONNECTOR_CATALOG_KINDS).not.toContain("azure_repos");
    expect(CONNECTOR_CATALOG_KINDS).not.toContain("azure_pipelines");
    expect(CONNECTOR_CATALOG_KINDS).toContain("azure_devops");
  });
});
