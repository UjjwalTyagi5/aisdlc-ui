import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * What each built-in role grants — proxied to FastAPI
 * `GET/PUT /admin/role-permissions`.
 *
 * TWO TABLES BEHIND THIS, and the split is the whole design. `role_permissions` is
 * the SHIPPED DEFAULT and is reconciled from code on every backend boot, so it is
 * not writable — an edit there would work until the next restart and then silently
 * revert. The PUT writes `role_permission_overrides`, which the seeder never
 * touches, so a retuned role survives restarts. `reset: true` deletes the override
 * rows and hands the role back to its default.
 *
 * The previous implementation here returned the shipped defaults with
 * `isOverridden: false` and 501'd every write — and returned the wrong SHAPE while
 * doing it (`permissions`/`isOverridden` where the client validates
 * `effective`/`overridden`), so it would have failed Zod at runtime rather than
 * merely being read-only.
 *
 * Read is open to anyone signed in: "what can my own role do" is a fair question,
 * and answering it only for administrators makes the permission model something you
 * have to be told rather than look up. The WRITE checks org-wideness in the backend
 * — not `role:manage`, which a Business Unit Admin holds and which would let them
 * redefine what "Developer" means for the whole organisation.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/admin/role-permissions");
}

export async function PUT(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/admin/role-permissions", { method: "PUT", body });
}
