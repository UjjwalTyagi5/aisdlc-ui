import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { notImplemented } from "@/lib/bff/not-implemented";
import type { ModelAllowEntry } from "@/lib/schemas/model";

/**
 * The last tier of the model cascade — what one project may actually use.
 *
 * Read from FastAPI `GET /model/allowed/project?projectId=…`, which returns a
 * bare list where this route's contract is an object. The reshaping below is not
 * a lossy adaptation, it is the backend's semantics spelled out: that endpoint is
 * documented as DERIVED, and `shared/routers/model_grants.py` states the rule
 * plainly — "only the org list is writable; a unit's entitlement is a consequence
 * of the org's grants, never a list its own admin curates".
 *
 * So a project's set is entirely inherited, nothing is curated, and the flags say
 * exactly that: `selected === inherited`, `usingDefaults` true. `inheritedFrom`
 * is null because the backend does not name the tier it descended from, and
 * naming one here would be a guess about provenance the caller would then print.
 *
 * The previous implementation read `lib/mock/model-fixtures` and let a project
 * curate its own selection against a fixture BU grant — a tier of the cascade the
 * backend does not have.
 *
 * BACKLOG: `inheritedFrom` on the FastAPI response, and a project-level PUT if
 * per-project curation is ever meant to exist.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const projectId = req.nextUrl.searchParams.get("projectId");
  if (!projectId) {
    return Response.json(
      { code: "invalid_input", message: "projectId is required" },
      { status: 422 },
    );
  }

  try {
    const allowed = (await bffFetch(
      `/model/allowed/project?projectId=${encodeURIComponent(projectId)}`,
      { session },
    )) as ModelAllowEntry[];

    return Response.json({
      inherited: allowed,
      inheritedFrom: null,
      selected: allowed,
      usingDefaults: true,
      defaultKey: null,
    });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}

/**
 * NOT IMPLEMENTED BY THE BACKEND, and deliberately so rather than merely
 * missing: FastAPI exposes a PUT for `/model/allowed/org` and `/model/allowed/bu`
 * and none for a project, because a project's set is derived from the tier above
 * it. Accepting a write here would store a preference nothing reads.
 */
export async function PUT() {
  return notImplemented("PUT /model/allowed/project");
}
