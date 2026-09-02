/**
 * The whole integration estate crossed with who has it — the Integrations
 * page's access matrix in one payload.
 *
 * WHY ONE CALL. The matrix is "every connector and MCP server × every Business
 * Unit that may use it × every project actually using it". Composing that in
 * the browser meant one reach request per integration, and the count of
 * integrations is exactly the thing that grows. The server already walks these
 * arrays; it walks them once.
 *
 * The rows are deliberately uniform across connectors and MCP servers. They
 * are governed identically now — granted to units, consumed by projects — and
 * two shapes for one screen would put the difference in the reader's way
 * rather than in the data's.
 *
 * Plain functions over the fixture arrays, server-safe, shared by the Next
 * route handlers and mocks/handlers.ts ([[msw-dual-runtime-mutation-rule]]).
 */
import { PROJECTS, CONNECTORS } from "@/mocks/fixtures";
import { listConnectorGrants } from "@/lib/mock/connector-grants";
import { listMcpGrants, MCP_SERVERS } from "@/lib/mock/mcp-fixtures";
import { listWorkspaces } from "@/lib/mock/workspace-fixtures";
import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import type { IntegrationAccessRow, AccessUnitEntry } from "@/lib/schemas/integration-access";

/** Archived projects consume nothing; counting them inflates a figure that
 *  exists to be acted on. */
function liveProjects() {
  return PROJECTS.filter((p) => !p.archived);
}

/**
 * Every integration, with the units that may use it and the projects that do.
 *
 * `allowedWorkspaceIds: null` is the org-wide viewer. A bounded viewer gets
 * rows narrowed to their own units — a row reaching none of them disappears
 * entirely rather than appearing with an empty unit list, which would say "you
 * have this, nobody uses it" about something they do not have.
 */
export function listIntegrationAccess(
  allowedWorkspaceIds: string[] | null,
): IntegrationAccessRow[] {
  const units = listWorkspaces();
  const unitName = new Map(units.map((w) => [String(w.id), w.displayName] as const));
  const visible = allowedWorkspaceIds === null ? null : new Set(allowedWorkspaceIds.map(String));
  const projects = liveProjects();

  /**
   * The units + projects for one integration.
   *
   * EVERY unit the viewer may see is a candidate, granted or not — `via: "none"`
   * marks the ones without it. Returning only the granted ones made granting
   * unreachable: the screen listed who had it and offered no way to add
   * anyone, so access could be taken away and never given back.
   *
   * A connection's own `scope` is deliberately IGNORED. It used to pin a
   * connector to the unit that onboarded it, which mattered while a Business
   * Unit Admin held their own credential for it. Nobody does now — the
   * organization decides which integrations exist and who may use them, and
   * projects supply the identity. Honouring the old scope here made a
   * unit-scoped connector ungrantable to anyone else, with no way to say why.
   */
  const entriesFor = (
    grantedTo: string[],
    uses: (projectId: string) => string[],
  ): AccessUnitEntry[] => {
    const granted = new Set(grantedTo.map(String));
    return units
      .filter((w) => (visible ? visible.has(String(w.id)) : true))
      .map((w) => {
        const id = String(w.id);
        const via = granted.has(id) ? ("granted" as const) : ("none" as const);
        return {
          id,
          name: unitName.get(id) ?? id,
          via,
          // Mock mode has no grant table, so it cannot know a per-unit level. It
          // reports the widest, matching how these fixtures have always behaved —
          // every granted unit could do everything. A null here would render the
          // picker empty and read as a bug rather than as "not modelled".
          // EVERY project in the unit, using it or not — an empty `stages`
          // means "could, doesn't". Filtering to users only made granting a
          // project unreachable, the same hole the unit list had.
          projects:
            via === "none"
              ? []
              : projects
                  .filter((p) => String(p.workspaceId) === id)
                  .map((p) => ({ id: String(p.id), name: p.name, stages: uses(String(p.id)) })),
        };
      });
  };

  const rows: IntegrationAccessRow[] = [];

  // ── Connectors, by kind ───────────────────────────────────────────────────
  for (const grant of listConnectorGrants()) {
    const connection = CONNECTORS.find((c) => c.kind === grant.kind);

    const entries = entriesFor(grant.businessUnitIds, (projectId) => {
      const p = projects.find((x) => String(x.id) === projectId);
      return Object.entries(p?.connectors ?? {})
        .filter(([, kinds]) => (kinds ?? []).includes(grant.kind))
        .map(([stage]) => stage);
    });
    if (entries.length === 0) continue;

    rows.push({
      kind: "connector",
      // Mock mode does not introspect connector manifests, so it states no ceiling
      // rather than inventing one. null is "unknown", which the picker treats as
      // "no cap" — the same answer the server gives for a connector it cannot read.
      id: grant.kind,
      name: connection?.name ?? CONNECTOR_KIND_LABEL[grant.kind] ?? grant.kind,
      description: null,
      origin: "organization",
      onboarded: Boolean(connection?.installed),
      // Connectors have no tool list — that is an MCP server's answer.
      tools: [],
      units: entries,
      grantedUnitCount: entries.filter((u) => u.via !== "none").length,
      // Counts projects USING it, not projects that could — the figure on the
      // card is about consumption.
      projectCount: entries.reduce(
        (n, u) => n + u.projects.filter((p) => p.stages.length > 0).length,
        0,
      ),
    });
  }

  // ── MCP servers, by id ────────────────────────────────────────────────────
  const mcpGrants = listMcpGrants();
  for (const server of MCP_SERVERS) {
    const granted = mcpGrants.find((g) => g.serverId === server.id)?.businessUnitIds ?? [];

    const entries = entriesFor(granted, (projectId) => {
      const p = projects.find((x) => String(x.id) === projectId);
      return Object.entries(p?.mcpServers ?? {})
        .filter(([, ids]) => (ids ?? []).includes(server.id))
        .map(([stage]) => stage);
    });
    if (entries.length === 0) continue;

    rows.push({
      kind: "mcp",
      // MCP servers have no capability manifest at all, server-side included.
      id: server.id,
      name: server.server_name,
      description: server.description ?? null,
      origin: "organization",
      onboarded: server.is_active,
      // The fixture's own snapshot, if it carries one. Mock mode never probes,
      // so an absent list means "not probed" here exactly as it does live.
      tools: server.tools_snapshot ?? [],
      units: entries,
      grantedUnitCount: entries.filter((u) => u.via !== "none").length,
      // Counts projects USING it, not projects that could — the figure on the
      // card is about consumption.
      projectCount: entries.reduce(
        (n, u) => n + u.projects.filter((p) => p.stages.length > 0).length,
        0,
      ),
    });
  }

  return rows;
}

/**
 * Take an integration away from ONE project.
 *
 * Revocation is per project, not per person: a connector is wired to a
 * project's STAGES and its agents run on behalf of whoever triggered them, so
 * there is no per-person axis in the data to revoke along. Removing it from
 * every stage is what "this project no longer uses it" means — leaving it on
 * one stage would leave it reachable.
 *
 * Returns false when the project never had it, so the caller can say nothing
 * changed rather than reporting a success that did nothing.
 */
export function revokeProjectIntegration(
  projectId: string,
  kind: "connector" | "mcp",
  targetId: string,
): boolean {
  const project = PROJECTS.find((p) => String(p.id) === projectId);
  if (!project) return false;

  const map = kind === "connector" ? project.connectors : project.mcpServers;
  if (!map) return false;

  let changed = false;
  for (const [stage, ids] of Object.entries(map)) {
    const next = (ids ?? []).filter((id) => id !== targetId);
    if (next.length !== (ids ?? []).length) {
      changed = true;
      if (next.length === 0) delete map[stage];
      else map[stage] = next;
    }
  }
  return changed;
}
