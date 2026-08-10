import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { createMcpServer, listMcpServersForScope } from "@/lib/mock/mcp-fixtures";
import type { McpServer } from "@/lib/schemas/mcp";

// DUMMY-DATA SEAM: reads and mutates the fixture registry directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]]. When a real MCP
// registry service lands, replace with bffProxy(...) as before.
export const dynamic = "force-dynamic";

/** The registry, filtered to what this viewer may read. */
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const activeOnly = req.nextUrl.searchParams.get("active_only") === "true";
  const scope = resolveSessionScope(session);
  return Response.json(
    listMcpServersForScope(scope.isOrgWide ? null : scope.businessUnitIds, activeOnly),
  );
}

/**
 * Register a server. Organization Admin only, and it reaches nobody until it
 * is granted to a Business Unit — registering is not granting.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  if (effectivePlatformRole(session) !== "org_admin") {
    return Response.json(
      { code: "forbidden", message: "Only an Organization Admin registers an MCP server." },
      { status: 403 },
    );
  }

  const body = (await req.json()) as Partial<McpServer> & { server_name?: string };
  if (!body.server_name) {
    return Response.json({ code: "bad_request", message: "Name the server." }, { status: 400 });
  }

  return Response.json(createMcpServer({ ...body, server_name: body.server_name }));
}
