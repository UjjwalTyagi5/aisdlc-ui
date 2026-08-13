import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * Per-project adjustments to what a role may do with an agent — the layer that
 * narrows or widens `AGENT_OWNERSHIP` for one project.
 *
 * NOT IMPLEMENTED BY THE BACKEND. There is no override table; a role's agent
 * access is the shipped matrix and nothing modifies it per project.
 *
 * Empty is the correct read either way: no overrides means every project uses the
 * built-in matrix, which is exactly the state of this database.
 *
 * BACKLOG: FastAPI `GET/PUT/DELETE /projects/{id}/agent-access-overrides`.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function PUT() {
  return notImplemented("PUT /projects/{id}/agent-access-overrides");
}

export async function DELETE() {
  return notImplemented("DELETE /projects/{id}/agent-access-overrides");
}
