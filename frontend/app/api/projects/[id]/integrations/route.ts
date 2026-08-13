import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * Which integrations one project uses, and the credentials it holds for them.
 *
 * NOT IMPLEMENTED BY THE BACKEND. FastAPI models connectors at the TENANT level
 * (`/connectors`) and enablement at the unit level (`workspace_connectors`);
 * there is no project→integration relation and no per-project credential store.
 *
 * BACKLOG: FastAPI `GET/PUT /projects/{id}/integrations`.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function PUT() {
  return notImplemented("PUT /projects/{id}/integrations");
}
