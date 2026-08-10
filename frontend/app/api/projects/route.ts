import { type NextRequest } from "next/server";

import { createProjectRecord } from "@/lib/mock/project-fixtures";
import { PROJECTS } from "@/mocks/fixtures";
import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { canCreateProject } from "@/lib/auth/permissions";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canReadProject } from "@/lib/mock/access-scope";
import type { ProjectCreateInput } from "@/lib/schemas/project";

// DUMMY-DATA SEAM: reads/writes the shared PROJECTS array directly (via
// lib/mock/project-fixtures.ts) — approval-aware, so a Project Admin's
// creation goes through the same governance flow whether MSW or this route
// answers the request. See [[msw-dual-runtime-mutation-rule]]: mocks/
// handlers.ts's POST /api/projects mirrors this exactly.

function pageParams(req: NextRequest) {
  const url = req.nextUrl;
  return {
    archived: url.searchParams.get("archived"),
    search: url.searchParams.get("search")?.toLowerCase(),
    page: Number(url.searchParams.get("page") ?? "1"),
    pageSize: Number(url.searchParams.get("pageSize") ?? "12"),
  };
}

export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { archived, search, page, pageSize } = pageParams(req);
  // SCOPE FILTER FIRST, before archive/search/paging. Order matters: filtering
  // after paging would leak the real total and hand back short pages, so the
  // count in "12 projects" would describe a set the viewer cannot open.
  const scope = resolveSessionScope(session);
  let list = PROJECTS.filter((p) => canReadProject(scope, String(p.id)));
  if (archived !== null) list = list.filter((p) => p.archived === (archived === "true"));
  else list = list.filter((p) => !p.archived);
  if (search) list = list.filter((p) => p.name.toLowerCase().includes(search));

  const total = list.length;
  const start = (page - 1) * pageSize;
  const items = list.slice(start, start + pageSize);
  return Response.json({ items, pagination: { page, pageSize, total } });
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body = (await req.json()) as ProjectCreateInput;
  if (!body?.name || !body?.workspaceId) {
    return Response.json(
      { code: "invalid_input", message: "name and workspaceId are required" },
      { status: 422 },
    );
  }

  const role = effectivePlatformRole(session);
  // Project creation had NO server-side check — hiding the button was the
  // whole control, so any signed-in person could POST one. A project belongs
  // to a Business Unit and is created by that unit or by a Project Admin
  // inside it; the Org Admin creates the UNITS (PRD §15.2), not what runs in
  // them.
  if (!canCreateProject(role)) {
    return Response.json(
      { code: "forbidden", message: "Your role cannot create projects." },
      { status: 403 },
    );
  }
  const created = createProjectRecord(body, {
    role,
    displayName: session.user.name,
    userRef: {
      id: session.user.id,
      name: session.user.name,
      email: session.user.email,
      initials: session.user.initials,
    },
  });
  return Response.json(created, { status: 201 });
}
