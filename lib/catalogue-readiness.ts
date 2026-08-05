import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import type { CatalogueAgent } from "@/lib/catalogue";
import type { ConnectorKind } from "@/lib/schemas/enums";

/**
 * Which connectors an agent needs, DERIVED from its tags rather than declared
 * a second time.
 *
 * `CatalogueAgent.tags` already mixes connector names ("Jira", "GitHub
 * Actions") with artifact types ("BRD", "Gherkin"), and the connector half is
 * the same eight strings `CONNECTOR_KIND_LABEL` owns. A parallel
 * `requiredConnectors` field would be a second place to say the same thing —
 * and the first one to go stale when an agent gains a connector, because
 * nothing would force the two to agree.
 *
 * Matching is exact against the label, case-folded. Deliberately not fuzzy:
 * "GitHub" must not also match "GitHub Actions", which is a different grant
 * with a different approver, and a substring match would silently claim an
 * agent needs CI when it only reads a repo.
 */
const KIND_BY_LABEL = new Map(
  (Object.entries(CONNECTOR_KIND_LABEL) as [ConnectorKind, string][]).map(
    ([kind, label]) => [label.toLowerCase(), kind] as const,
  ),
);

export function connectorKindsForAgent(agent: CatalogueAgent): ConnectorKind[] {
  const out: ConnectorKind[] = [];
  for (const tag of agent.tags) {
    const kind = KIND_BY_LABEL.get(tag.trim().toLowerCase());
    if (kind && !out.includes(kind)) out.push(kind);
  }
  return out;
}

/** One connector an agent needs, and whether the viewer's scope holds it. */
export interface ConnectorReadiness {
  kind: ConnectorKind;
  label: string;
  granted: boolean;
}

/**
 * An agent's connector requirements against a set of granted kinds.
 *
 * `granted` is the viewer's OWN grants, so this answers "can I run this",
 * never "does the organization have it". An Org Admin passes every kind and so
 * sees everything ready, which is correct: they grant it to themselves by
 * definition.
 */
export function agentConnectorReadiness(
  agent: CatalogueAgent,
  granted: ReadonlySet<string>,
): ConnectorReadiness[] {
  return connectorKindsForAgent(agent).map((kind) => ({
    kind,
    label: CONNECTOR_KIND_LABEL[kind],
    granted: granted.has(kind),
  }));
}
