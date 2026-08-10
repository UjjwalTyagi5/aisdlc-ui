/**
 * Server-only BFF fetch client.
 *
 * Performs server-to-server calls from the Next.js BFF to the FastAPI resource
 * API. In enterprise OIDC mode it forwards the Auth0 RS256 access token
 * (getAccessToken — D-01). In local/mock mode it mints a short-lived HS256 JWT
 * via mintBffToken (unchanged). Either way the bearer is injected as
 * `Authorization: Bearer` — the browser never sees the token (T-7.3-12).
 *
 * Error normalization mirrors `lib/api/client.ts` so callers get the same
 * ApiRequestError shape regardless of transport.
 */
import { cookies } from "next/headers";
import { type z } from "zod";

import type { Session } from "@/lib/auth/types";
import { ApiRequestError } from "@/lib/api/client";
import { mintBffToken } from "@/lib/bff/jwt";
import { isOidcEnabled, isMockAuth } from "@/lib/auth/mode";
import { getAuth0 } from "@/lib/auth/auth0";

/**
 * Base URL for the FastAPI internal API (server-only env var).
 * Defaults to localhost for local development.
 * Must NOT use a NEXT_PUBLIC_ prefix — this URL is server-only.
 */
export const FASTAPI_BASE =
  process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/**
 * Resolve the bearer token for a BFF → FastAPI request.
 *
 * Branch (D-01):
 *   Enterprise OIDC: forward the Auth0 RS256 access token via getAccessToken()
 *   — audience is pre-configured in auth0.ts (Pitfall 4 prevention).
 *   Local / mock: mint an HS256 token via mintBffToken (unchanged 7.2 path).
 *
 * Pitfall 6 lock (USER STANDING DIRECTIVE): getAuth0() MUST NOT be called
 * unless isOidcEnabled && !isMockAuth — it throws on missing AUTH0_DOMAIN,
 * breaking local dev entirely (T-7.3-15).
 *
 * Confirmed A5: Auth0Client.getAccessToken() (App Router path, no req/res)
 * returns Promise<{ token: string; expiresAt: number; ... }> in v4.22.0.
 */
export async function bearerForRequest(session: Session): Promise<string> {
  if (isOidcEnabled && !isMockAuth) {
    const { token } = await getAuth0().getAccessToken();
    return token;
  }
  return mintBffToken(session);
}

interface BffRequestOptions<TSchema extends z.ZodTypeAny> {
  session: Session;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Optional Zod schema — validates response JSON. No schema → raw JSON returned. */
  schema?: TSchema;
  signal?: AbortSignal;
}

/**
 * Server-side fetch to FastAPI with automatic JWT injection.
 *
 * Mints an HS256 BFF token from the session and adds `Authorization: Bearer`
 * to every request. Does NOT use `credentials: "include"` — this is a
 * server-to-server call, not a browser-to-server call.
 *
 * @param path - FastAPI path (e.g. "/sdlc/agent/requirements/…").
 * @param opts - Request options including the current session.
 * @returns Parsed JSON validated against `schema` when provided, otherwise raw JSON.
 * @throws {ApiRequestError} On non-OK HTTP responses or Zod schema mismatches.
 */
export async function bffFetch<TSchema extends z.ZodTypeAny>(
  path: string,
  opts: BffRequestOptions<TSchema>,
): Promise<TSchema extends z.ZodTypeAny ? z.infer<TSchema> : unknown> {
  const { session, method = "GET", body, schema, signal } = opts;

  const jwt = await bearerForRequest(session);

  // Forward the active-workspace selector (set by the workspace switcher) so the
  // backend scopes projects/models/etc to it. Read from the request cookie —
  // covers every BFF route without threading a param through each call.
  let workspaceHeader: Record<string, string> = {};
  try {
    const ws = (await cookies()).get("sdlc_active_workspace")?.value;
    if (ws) workspaceHeader = { "X-Workspace-Id": ws };
  } catch {
    // cookies() unavailable outside a request scope — no active workspace to forward.
  }

  const url = path.startsWith("http") ? path : `${FASTAPI_BASE}${path}`;

  const res = await fetch(url, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      Authorization: `Bearer ${jwt}`,
      ...workspaceHeader,
    },
    // Server-to-server call — no cookie forwarding (T-M4-07).
    signal,
  });

  if (!res.ok) {
    const errBody = await res.json().catch(() => null);
    throw new ApiRequestError(res.status, errBody, res.statusText);
  }

  if (res.status === 204) {
    return undefined as never;
  }

  const json: unknown = await res.json();

  if (!schema) {
    return json as never;
  }

  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    console.error("[bffFetch] response did not match schema", path, parsed.error.issues);
    throw new ApiRequestError(500, {
      code: "schema_mismatch",
      message: "FastAPI returned an unexpected shape.",
    });
  }

  return parsed.data;
}
