/**
 * Server-only BFF fetch client.
 *
 * Performs server-to-server calls from the Next.js BFF to the FastAPI resource
 * API. In enterprise OIDC mode it forwards the Auth0 RS256 access token
 * (getAccessToken — D-01); in local mode it forwards the token FastAPI issued at
 * login; in mock mode, and only there, it mints one. Either way the bearer is
 * injected as `Authorization: Bearer` — the browser never sees it (T-7.3-12).
 *
 * Error normalization mirrors `lib/api/client.ts` so callers get the same
 * ApiRequestError shape regardless of transport.
 */
import { cookies } from "next/headers";
import { type z } from "zod";

import type { Session } from "@/lib/auth/types";
import { ApiRequestError } from "@/lib/api/client";
import { mintBffToken } from "@/lib/bff/jwt";
import { isOidcEnabled, isMockAuth, isLocalAuth } from "@/lib/auth/mode";
import { TOKEN_COOKIE_NAME } from "@/lib/auth/token";
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
 * Three branches:
 *   Enterprise OIDC — forward the Auth0 RS256 access token via getAccessToken();
 *   audience is pre-configured in auth0.ts (Pitfall 4 prevention).
 *   Local — forward the token FastAPI itself issued at login, stored httpOnly.
 *   Everything else (mock, and auth0 with OIDC off) — mint an HS256 token,
 *   because there is no issued token to forward.
 *
 * THE LOCAL BRANCH USED TO MINT, AND THAT WAS THE HOLE. It signed
 * `session.permissions` — read from an unsigned, non-httpOnly cookie — into a
 * token the backend trusts verbatim, so editing the cookie granted `admin:*`.
 * Forwarding the backend's own token makes the backend the only issuer, and
 * incidentally makes the JTI denylist usable, since there is now one token
 * identity per session rather than a fresh one per request.
 *
 * The minting branch survives only where the session is built server-side and
 * carries nothing the client wrote — see lib/bff/jwt.ts.
 * See finding 1 in `docs/rbac-audit-2026-08-17.md`.
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

  if (!isLocalAuth) {
    // Mock, and auth0 with ENABLE_OIDC=false (the documented SC#4 rollback).
    // Both build the session server-side, so signing its permissions asserts
    // nothing the client supplied.
    return mintBffToken(session);
  }

  const token = (await cookies()).get(TOKEN_COOKIE_NAME)?.value;
  if (!token) {
    // Fail closed and loudly. Falling back to minting here would quietly
    // reinstate the bypass on exactly the path that matters — a request whose
    // token is missing or expired must be refused, not re-issued from a cookie.
    throw new ApiRequestError(401, {
      code: "unauthenticated",
      message: "No active session token. Sign in again.",
    });
  }
  return token;
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
