import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * The integration access map — proxied to FastAPI `/integrations/access`.
 *
 * THREE LEVELS, and this route is about the middle one: the organisation ONBOARDS a
 * connection, a Business Unit is GRANTED permission to use it, and a project WIRES it
 * to its stages. Only the middle level is a decision made about somebody else, which
 * is why it is the only one with an authorisation story — and it is the one that did
 * not exist, which is why this route used to return fabricated unit and project counts.
 *
 * Granting is the Organization Admin's alone (a unit that could grant itself an
 * integration has no grant). Revoking at PROJECT level is either admin tier's, because
 * an admin taking something away has to be able to stop one team without punishing the
 * rest of the unit.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/integrations/access");
}

export async function POST(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/integrations/access${qs ? `?${qs}` : ""}`, { method: "POST" });
}

export async function DELETE(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/integrations/access${qs ? `?${qs}` : ""}`, { method: "DELETE" });
}
