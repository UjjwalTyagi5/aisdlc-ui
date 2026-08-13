import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * A project's roster — who works on it, and in which role.
 *
 * NOT IMPLEMENTED BY THE BACKEND, and the seam comment here said so from the
 * start: "a net-new project-scoped role model with no backend equivalent yet".
 * `role_bindings` does have a `scope_kind` that admits `project`, so the storage
 * exists; no endpoint reads or writes those rows.
 *
 * This is what put "Payments API — SCA exemption defect · Developer" beside
 * people on the Users page over a `projects` table holding nothing.
 *
 * BACKLOG: FastAPI `GET/POST /projects/{id}/members` over project-scope
 * `role_bindings`, plus the PATCH/DELETE in the sibling route.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function POST() {
  return notImplemented("POST /projects/{id}/members");
}
