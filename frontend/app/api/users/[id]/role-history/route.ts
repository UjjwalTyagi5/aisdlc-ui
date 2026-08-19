import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * One person's role changes — proxied to FastAPI `/users/{id}/role-history`.
 *
 * The trail has always been written; nothing read it. `record_rbac_change` appends
 * every grant and revoke to `audit_events` keyed on the SUBJECT, with the actor
 * beside it, and there was no way to ask for one person's.
 *
 * Who may read it is decided server-side: `member:manage` plus the same scope rule as
 * everywhere else, so a Business Unit Admin sees the people in units they administer
 * and nobody else. A role history names every unit somebody has been placed in, which
 * is more than a project-level admin is owed about a person passing through.
 */
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/users/${encodeURIComponent(id)}/role-history`);
}
