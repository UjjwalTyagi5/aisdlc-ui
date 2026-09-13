import type { z } from "zod";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";

const FORBIDDEN_MESSAGE =
  "You don't have access to this at your current role. Ask an admin to grant you the right role.";

/**
 * Shared BFF route-handler proxy. Resolves the session, calls FastAPI via
 * bffFetch, and — crucially — PASSES THROUGH the backend status instead of
 * letting an error throw (which Next renders as a generic 500 "unknown_error").
 *
 * A backend 403 is normalized to `{ code: "forbidden", message }` so the UI's
 * ApiErrorState can render a clear "you don't have access" state for any role
 * that lacks a permission, instead of a confusing internal-error message.
 */
export async function bffProxy<TSchema extends z.ZodTypeAny>(
  path: string,
  opts: {
    method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
    body?: unknown;
    schema?: TSchema;
  } = {},
): Promise<Response> {
  const session = await getSession();
  if (!session) {
    return Response.json(
      { code: "unauthenticated", message: "Please sign in to continue." },
      { status: 401 },
    );
  }

  try {
    const data = await bffFetch(path, {
      session,
      method: opts.method,
      body: opts.body,
      schema: opts.schema,
    });
    return Response.json(data ?? null);
  } catch (err) {
    if (err instanceof ApiRequestError) {
      // A 403 the backend EXPLAINED keeps its own words. The generic message below
      // is for a bare permission refusal — "you don't hold the right role" — and
      // saying that about a request which is merely waiting on somebody else sends
      // the reader looking for a permission they already have. `err.explained`
      // is that test. It was `err.code === "unknown_error"`, which stopped
      // meaning "unexplained" once the code began being derived from the status:
      // a bare 403 now arrives as `forbidden`, so that comparison would never
      // match again and every bare refusal would leak the backend's own wording.
      if (err.status === 403 && !err.explained) {
        return Response.json(
          { code: "forbidden", message: FORBIDDEN_MESSAGE },
          { status: 403 },
        );
      }
      return Response.json(
        err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}
