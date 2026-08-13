import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * The integration access map — every connector and MCP server, with the units
 * that may use it and the projects that do.
 *
 * NOT IMPLEMENTED BY THE BACKEND. It is a join across three things FastAPI does
 * not expose: connector grants (no grant table at all — see
 * app/api/connectors/grants/route.ts), MCP grants (`mcp_servers` records who
 * CREATED a server, not who may use it), and per-project integration wiring
 * (there is no project→integration relation).
 *
 * This used to serve `lib/mock/integration-access` joined against the fixture
 * PROJECTS array. With the database holding zero `mcp_servers` and zero
 * `workspace_connectors`, every unit and project count that page printed was
 * invented here.
 *
 * BACKLOG: FastAPI `GET /integrations/access`, plus `POST`/`DELETE` for the
 * unit-level grant and project-level revoke this route used to fake.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function POST() {
  return notImplemented("POST /integrations/access");
}

export async function DELETE() {
  return notImplemented("DELETE /integrations/access");
}
