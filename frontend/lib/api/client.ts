import { type z } from "zod";

import { ApiError } from "@/lib/schemas";

export class ApiRequestError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly requestId?: string;
  public readonly details?: Record<string, unknown>;
  /**
   * The raw, unvalidated response body — always populated when the server
   * returned parseable JSON, even if it doesn't match the {code,message,details}
   * ApiError envelope (e.g. a bespoke 422 shape like {detail: {violations}}).
   * Callers that need a non-standard error shape intact should read this
   * instead of `details`, which is only populated when `body` matches ApiError.
   */
  public readonly rawBody: unknown;

  constructor(status: number, body: unknown, fallback?: string) {
    const parsed = ApiError.safeParse(body);
    // FastAPI raises HTTPException as `{detail: …}` — not the `{code, message}`
    // ApiError shape — and `detail` comes in two forms, both of which are real:
    //
    //   {detail: "Budget exceeded"}                    a bare message
    //   {detail: {code: "SELF_APPROVAL_BLOCKED", …}}   a machine-readable refusal
    //
    // The second is what the approvals and governance routers raise, and it was
    // being dropped: only the string form was unwrapped, so a caller got
    // `unknown_error` / "Bad Request" in place of "You raised this request — it
    // escalates rather than self-approving". The code is what a client branches on
    // and the message is what a person reads; flattening both to the statusText
    // loses the two things the backend went to the trouble of distinguishing.
    const rawDetail = (body as { detail?: unknown } | null | undefined)?.detail;
    const detail =
      typeof rawDetail === "string"
        ? { message: rawDetail }
        : rawDetail && typeof rawDetail === "object"
          ? (rawDetail as { code?: unknown; message?: unknown })
          : undefined;
    const detailMessage =
      typeof detail?.message === "string" ? detail.message : undefined;
    const detailCode = typeof detail?.code === "string" ? detail.code : undefined;

    const payload = parsed.success
      ? parsed.data
      : {
          code: detailCode ?? "unknown_error",
          message: detailMessage ?? fallback ?? "Request failed",
        };
    super(payload.message);
    this.status = status;
    this.code = payload.code;
    this.requestId = parsed.success ? parsed.data.requestId : undefined;
    this.details = parsed.success ? parsed.data.details : undefined;
    this.rawBody = body;
    this.name = "ApiRequestError";
  }
}

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "/api";

interface RequestOptions<TSchema extends z.ZodTypeAny> {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  /** Zod schema — response is validated against it. No schema → raw JSON. */
  schema?: TSchema;
  signal?: AbortSignal;
  /** Override headers; `Authorization` is added automatically in auth0 mode (Chunk 15). */
  headers?: Record<string, string>;
}

/**
 * Typed fetch wrapper. Validates responses with Zod, normalizes errors,
 * and is the single place to add auth headers / retries / tracing later.
 *
 *   const projects = await api("/projects", { schema: paginated(Project) });
 */
export async function api<TSchema extends z.ZodTypeAny>(
  path: string,
  opts: RequestOptions<TSchema> = {},
): Promise<TSchema extends z.ZodTypeAny ? z.infer<TSchema> : unknown> {
  const { method = "GET", body, query, schema, signal, headers } = opts;

  const url = new URL(path.startsWith("http") ? path : `${API_BASE}${path}`, "http://localhost");
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
  }

  const res = await fetch(url.pathname + url.search, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...headers,
    },
    credentials: "include",
    signal,
  });

  if (!res.ok) {
    const errBody = await res.json().catch(() => null);
    throw new ApiRequestError(res.status, errBody, res.statusText);
  }

  if (res.status === 204) {
    return undefined as never;
  }

  const json = await res.json();
  if (!schema) {
    return json as never;
  }

  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    console.error("[api] response did not match schema", path, parsed.error.issues);
    throw new ApiRequestError(500, {
      code: "schema_mismatch",
      message: "Server returned an unexpected shape.",
    });
  }
  return parsed.data;
}
