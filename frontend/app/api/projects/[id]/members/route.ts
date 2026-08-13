import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * A project's roster — proxied to FastAPI `GET/POST /projects/{id}/members`.
 *
 * The storage always existed: `role_bindings.scope_kind` admits `project`, and
 * `can_perform` has resolved permissions through project-scope rows all along. What
 * was missing was any way to read or write them, which is why this route used to
 * serve `lib/mock/project-membership-fixtures` and put "Payments API — Developer"
 * beside people on the Users page over a projects table holding nothing.
 *
 * ADDING SOMEONE DOES NOT ONBOARD THEM. The backend 404s an email nobody in the
 * organisation uses, rather than creating the account: admitting a person is an
 * Organization Admin act, and doing it here would put account creation behind
 * `member:manage`, which a Project Admin holds.
 */
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/members`);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = (await req.json()) as { email?: string; roleName?: string };
  if (!body?.email || !body?.roleName) {
    return Response.json(
      { code: "invalid_input", message: "email and roleName are required" },
      { status: 422 },
    );
  }
  return bffProxy(`/projects/${encodeURIComponent(id)}/members`, { method: "POST", body });
}
