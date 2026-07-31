import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import {
  getProjectModelSelection,
  setProjectModelSelection,
} from "@/lib/mock/model-fixtures";
import { getProjectById } from "@/lib/mock/project-fixtures";
import type { ModelAllowEntry } from "@/lib/schemas/model";

// DUMMY-DATA SEAM: mutates the shared fixture store directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// The fourth tier of the model cascade: org allow-list → BU allow-list →
// what this project selected from it. The project's parent Business Unit is
// resolved here rather than in model-fixtures, so that module keeps its
// one-way dependency and never has to know what a project is.

function resolve(req: NextRequest) {
  const projectId = req.nextUrl.searchParams.get("projectId");
  if (!projectId) {
    return { error: Response.json({ code: "invalid_input", message: "projectId is required" }, { status: 422 }) };
  }
  const project = getProjectById(projectId);
  if (!project) {
    return { error: Response.json({ code: "not_found", message: "Unknown project" }, { status: 404 }) };
  }
  return { projectId, workspaceId: project.workspaceId ?? null };
}

export function GET(req: NextRequest) {
  const r = resolve(req);
  if ("error" in r) return r.error;
  return Response.json(getProjectModelSelection(r.projectId, r.workspaceId));
}

export async function PUT(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const r = resolve(req);
  if ("error" in r) return r.error;

  const body = (await req.json()) as {
    selected?: ModelAllowEntry[];
    defaultKey?: string | null;
  };
  return Response.json(
    setProjectModelSelection(r.projectId, r.workspaceId, {
      selected: body.selected ?? [],
      defaultKey: body.defaultKey,
    }),
  );
}
