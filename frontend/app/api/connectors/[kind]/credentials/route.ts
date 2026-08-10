import { type NextRequest } from "next/server";

import { CONNECTORS } from "@/mocks/fixtures";
import { permittedConnectorKinds } from "@/lib/mock/connector-grants";
import { onboardingScopeFor, recordConnectorCredentials } from "@/lib/mock/connector-scope";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { getSession } from "@/lib/auth/session";

/**
 * POST /api/connectors/[kind]/credentials
 *
 * DUMMY-DATA SEAM: stores the pasted credential against the fixture list and
 * reports the verify result the real backend would. Mirrored in
 * mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
 *
 * This used to proxy straight to FastAPI, which meant the entire "Add
 * credentials" flow hard-failed with no backend running — the same defect as
 * the Capabilities and MCP pages, in a flow a Business Unit Admin now depends
 * on. The secret itself is read and discarded: nothing echoes it back, and a
 * fixture store is not somewhere to keep one.
 */
export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ kind: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { kind } = await params;
  const role = effectivePlatformRole(session);
  const scope = resolveSessionScope(session);

  // The Org Admin's own grants can't exclude them; anyone else may only
  // onboard a kind granted to a unit they belong to.
  const permitted =
    scope.isOrgWide ||
    scope.businessUnitIds.some((id) => (permittedConnectorKinds(id) as string[]).includes(kind));
  if (!permitted) {
    return Response.json(
      {
        code: "forbidden",
        message:
          "Your Organization Admin hasn't permitted this connector for your business unit.",
      },
      { status: 403 },
    );
  }

  const body = (await req.json().catch(() => ({}))) as {
    org_url?: string;
    base_url?: string;
    owner?: string;
    workspaceId?: string | null;
  };
  const account = body.org_url ?? body.base_url ?? body.owner ?? null;

  // The unit the connection lands in. A caller may name one; it must be theirs.
  // Falling back to their only unit is safe — falling back to the FIRST of
  // several would be the arbitrary pick this parameter exists to remove.
  const requested = body.workspaceId ? String(body.workspaceId) : null;
  if (requested && !scope.isOrgWide && !scope.businessUnitIds.includes(requested)) {
    return Response.json({ code: "forbidden", message: "Not your business unit." }, { status: 403 });
  }
  const target =
    requested ?? (scope.businessUnitIds.length === 1 ? scope.businessUnitIds[0]! : null);

  const onboardAt = onboardingScopeFor(role);
  if (onboardAt.requiresWorkspace && !target) {
    return Response.json(
      { code: "invalid_input", message: "workspaceId is required — you belong to several." },
      { status: 422 },
    );
  }

  const connector = recordConnectorCredentials(
    CONNECTORS,
    kind,
    {
      scope: onboardAt.scope,
      workspaceId: target,
      tenantId: String(CONNECTORS[0]?.tenantId ?? ""),
    },
    account,
  );
  if (!connector) return Response.json({ code: "not_found" }, { status: 404 });

  return Response.json({ kind, status: "valid", account: connector.account ?? null });
}
