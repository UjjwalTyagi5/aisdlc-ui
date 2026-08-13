import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * Connector grants — which Business Units may use which connector kind.
 *
 * NOT IMPLEMENTED BY THE BACKEND. FastAPI has `/connectors` (the kinds, and
 * whether each is installed for the tenant) but no per-unit grant table: there
 * is no `workspace_connector_grants` relation and no endpoint that reads or
 * writes one. `workspace_connectors` is a different thing — it records that a
 * unit ENABLED a connector, not that the organisation PERMITTED it to.
 *
 * This used to serve `lib/mock/connector-grants`, which is why the Integrations
 * hub reported "Jira — 3 business units · 4 projects" over an empty database.
 *
 * Until the backend owns grants, the honest answer is that nothing is granted.
 * The page reads that as "no unit has been given this yet", which is true.
 *
 * BACKLOG: FastAPI `GET/PUT /connectors/grants`.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function PUT() {
  return notImplemented("PUT /connectors/grants");
}
