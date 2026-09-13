import { describe, expect, it } from "vitest";

import { ApiRequestError } from "@/lib/api/client";

/**
 * FastAPI raises `HTTPException(404, detail="Trace not found")`, which serialises to a
 * bare `{detail: "..."}` rather than the `{code, message}` envelope. That is most of
 * this backend's refusals, and every one of them used to arrive labelled
 * `unknown_error` — so the trace detail page showed "Trace not found" underneath a red
 * UNKNOWN_ERROR chip, the message knowing exactly what happened while the code claimed
 * nobody did.
 */
describe("ApiRequestError code", () => {
  it("derives a code from the status when the backend names none", () => {
    const e = new ApiRequestError(404, { detail: "Trace not found" });
    expect(e.code).toBe("not_found");
    expect(e.message).toBe("Trace not found");
  });

  it.each([
    [400, "bad_request"],
    [401, "unauthorized"],
    [403, "forbidden"],
    [409, "conflict"],
    [422, "invalid_request"],
    [429, "rate_limited"],
    [500, "server_error"],
    [503, "server_error"],
  ])("maps %i to %s", (status, code) => {
    expect(new ApiRequestError(status, { detail: "x" }).code).toBe(code);
  });

  it("keeps unknown_error only for a status that explains nothing", () => {
    expect(new ApiRequestError(418, { detail: "x" }).code).toBe("unknown_error");
  });

  it("never overrides a code the backend did supply", () => {
    const e = new ApiRequestError(403, {
      detail: { code: "SELF_APPROVAL_BLOCKED", message: "You raised this request" },
    });
    expect(e.code).toBe("SELF_APPROVAL_BLOCKED");
    expect(e.explained).toBe(true);
  });

  /**
   * `explained` replaced `code === "unknown_error"` as the proxy's test for "did the
   * backend bother to say why". Deriving the code broke that comparison — a bare 403
   * now arrives as `forbidden` — and without this distinction the proxy would stop
   * substituting its generic refusal wording and leak the backend's raw text instead.
   */
  it("marks a status-derived code as unexplained", () => {
    expect(new ApiRequestError(403, { detail: "role check failed" }).explained).toBe(
      false,
    );
  });
});
