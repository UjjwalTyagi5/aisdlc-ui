/**
 * Every path `lib/api/*` calls has a Next route handler under `app/api/`.
 *
 * THE BUG THIS CATCHES, which shipped. The artifact-version panel called
 * `/artifact-versions/{project}/stages/{stage}/versions`. The backend route existed,
 * was protected, and had 300-odd passing tests behind it. The browser still got
 * "Not Found" on every agent page — because the browser never talks to FastAPI. It
 * calls `/api/...`, and Next answers with its own 404 when no handler matches.
 *
 * NOTHING ELSE LOOKED. The backend tests use TestClient and never cross this
 * boundary; `tsc` cannot see that a fetch path has no handler; the API client and the
 * route handlers are separate files that agree only by convention. The failure read
 * as a backend problem and was not one.
 *
 * So this compares the two sides directly: every `api("/...")` call in `lib/api` must
 * have a `route.ts` whose directory matches, with dynamic segments (`[id]`) treated
 * as wildcards.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..", "..");
const API_CLIENT_DIR = join(ROOT, "lib", "api");
const API_ROUTES_DIR = join(ROOT, "app", "api");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

/** Every route handler, as a path with `[x]` segments kept for matching. */
function handlerPatterns(): string[][] {
  return walk(API_ROUTES_DIR)
    .filter((f) => f.endsWith(`${sep}route.ts`) || f.endsWith(`${sep}route.tsx`))
    .map((f) =>
      relative(API_ROUTES_DIR, f)
        .split(sep)
        .slice(0, -1)
        .filter(
          // Route GROUPS `(name)` are organisational and contribute no URL segment.
          (s) => !(s.startsWith("(") && s.endsWith(")")),
        ),
    );
}

/**
 * The literal prefix of every path passed to `api(...)`, with `${...}` interpolations
 * replaced by a wildcard. Template literals are how every dynamic path is built here.
 */
function requestedPaths(): { path: string[]; file: string }[] {
  const found: { path: string[]; file: string }[] = [];
  for (const file of walk(API_CLIENT_DIR)) {
    if (!file.endsWith(".ts")) continue;
    const src = readFileSync(file, "utf8");
    // api(`/foo/${x}/bar`  or  api("/foo")
    const re = /\bapi\(\s*[`"](\/[^`"]*)[`"]/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(src)) !== null) {
      const segments = m[1]
        .split("?")[0]
        .split("/")
        .filter(Boolean)
        .map((s) => (s.includes("${") ? "*" : s));
      found.push({ path: segments, file: relative(ROOT, file) });
    }
  }
  return found;
}

function matches(request: string[], handler: string[]): boolean {
  // A catch-all `[...x]` swallows the rest.
  const catchAllAt = handler.findIndex((s) => s.startsWith("[..."));
  if (catchAllAt !== -1) {
    return (
      request.length >= catchAllAt &&
      handler
        .slice(0, catchAllAt)
        .every((h, i) => h === request[i] || h.startsWith("["))
    );
  }
  if (request.length !== handler.length) return false;
  return handler.every((h, i) => {
    if (h.startsWith("[")) return true; // dynamic segment
    if (request[i] === "*") return true; // interpolated value against a literal
    return h === request[i];
  });
}

/**
 * KNOWN-BROKEN, PRE-EXISTING, and deliberately not fixed here.
 *
 * These four are live code — `lib/api/audit.ts` is used by both audit pages,
 * `eval.ts` by the eval indicator, `evidence.ts` by the audit tab — and every one of
 * them 404s in the browser today for exactly the reason this file exists. They were
 * found BY this test, not introduced by it.
 *
 * Registered rather than fixed because the fix is a guess without knowing what those
 * backend routes expect, and a wrong proxy is worse than an absent one: it turns a
 * clean 404 into a plausible-looking error. Removing an entry here is the fix.
 */
const KNOWN_MISSING = new Set([
  "/runs/*/audit",
  "/runs/*/eval",
  "/runs/*/evidence",
  "/runs/*/evidence/*/status",
]);

describe("BFF coverage", () => {
  it("finds both sides, so the assertion below is not vacuous", () => {
    expect(handlerPatterns().length).toBeGreaterThan(50);
    expect(requestedPaths().length).toBeGreaterThan(50);
  });

  it("every path lib/api calls is served by a route handler", () => {
    const handlers = handlerPatterns();
    const missing = requestedPaths()
      .filter(({ path }) => !handlers.some((h) => matches(path, h)))
      .filter(({ path }) => !KNOWN_MISSING.has(`/${path.join("/")}`))
      .map(({ path, file }) => `/${path.join("/")}  (${file})`);

    expect(
      missing,
      "These paths are fetched by the browser but have no Next route handler. " +
        "The browser never reaches FastAPI directly — it gets Next's own 404 " +
        '("Not Found"), which reads as a backend fault and is not one. ' +
        "Add a handler under app/api/ mirroring the backend path:\n  " +
        missing.join("\n  "),
    ).toEqual([]);
  });

  it("the known-broken register is not stale", () => {
    // An allowlist that outlives the problem starts hiding the next one. If a path
    // here has gained a handler, delete it from the set.
    const handlers = handlerPatterns();
    const fixed = [...KNOWN_MISSING].filter((p) =>
      handlers.some((h) => matches(p.split("/").filter(Boolean), h)),
    );
    expect(
      fixed,
      `these are listed as known-broken but now have handlers — remove them from ` +
        `KNOWN_MISSING: ${fixed.join(", ")}`,
    ).toEqual([]);
  });

  it("covers the artifact-version paths specifically", () => {
    // The regression, named so a failure says which feature broke rather than
    // "some path somewhere".
    const handlers = handlerPatterns();
    for (const path of [
      ["artifact-versions", "*", "stages", "*", "versions"],
      ["artifact-versions", "*", "stages", "*", "versions", "published"],
      ["artifact-versions", "*", "stages", "*", "versions", "*", "publish"],
      ["artifact-versions", "*", "stages", "*", "versions", "*", "reject"],
      ["artifact-versions", "*", "matrix"],
      ["artifact-versions", "*", "runs", "*", "evidence"],
    ]) {
      expect(
        handlers.some((h) => matches(path, h)),
        `no route handler for /${path.join("/")}`,
      ).toBe(true);
    }
  });
});
