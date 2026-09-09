/**
 * The source a person picked survives the trip to FastAPI.
 *
 * THE BUG THIS CATCHES. The picker, the API client, the Next route handler and the
 * backend route each have to carry `provider`, and three of the four can be right
 * while the page still lists the wrong host's repositories. If the proxy drops the
 * query string — which is the default, since `bffFetch` is handed a path the handler
 * builds by hand — the backend falls back to the project's first configured source
 * and answers confidently with somebody else's projects. Nothing errors. The list
 * looks like a list.
 *
 * `tsc` cannot see it: the handler's path is a template literal, and forgetting to
 * append a query string is not a type error. `every-api-path-has-a-proxy` cannot see
 * it either — it matches directories, and the handler exists.
 *
 * So this calls the handlers and reads the path they forward.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

const bffFetch = vi.fn(async () => []);

vi.mock("@/lib/bff/client", () => ({ bffFetch: (path: string) => bffFetch(path) }));
vi.mock("@/lib/auth/session", () => ({
  getSession: async () => ({ user: { id: "u1" }, accessToken: "t" }),
}));

/** Next's handlers only read `nextUrl.searchParams`, which a plain Request supplies. */
function req(url: string) {
  return new Request(url) as unknown as import("next/server").NextRequest & {
    nextUrl: URL;
  };
}
function withUrl(url: string) {
  const r = req(url) as unknown as { nextUrl: URL };
  r.nextUrl = new URL(url);
  return r as unknown as import("next/server").NextRequest;
}

beforeEach(() => bffFetch.mockClear());

/** Every picker route that a multi-source project can reach, and its parameters. */
const ROUTES: {
  name: string;
  load: () => Promise<{ GET: (req: unknown, ctx: unknown) => Promise<Response> }>;
  url: string;
  params: Record<string, string>;
}[] = [
  {
    name: "dev · projects",
    load: () => import("@/app/api/dev/[id]/ado/projects/route"),
    url: "http://x/api/dev/p1/ado/projects?provider=github",
    params: { id: "p1" },
  },
  {
    name: "dev · repos",
    load: () => import("@/app/api/dev/[id]/ado/projects/[project]/repos/route"),
    url: "http://x/api/dev/p1/ado/projects/acme/repos?provider=github",
    params: { id: "p1", project: "acme" },
  },
  {
    name: "dev · branches",
    load: () => import("@/app/api/dev/[id]/ado/repos/[project]/[repo]/branches/route"),
    url: "http://x/api/dev/p1/ado/repos/acme/web/branches?provider=github",
    params: { id: "p1", project: "acme", repo: "web" },
  },
  {
    name: "documentation · open PRs",
    load: () =>
      import("@/app/api/documentation/[id]/ado/repos/[project]/[repo]/prs/route"),
    url: "http://x/api/documentation/p1/ado/repos/acme/web/prs?provider=github",
    params: { id: "p1", project: "acme", repo: "web" },
  },
  {
    name: "deployment · open PRs",
    load: () => import("@/app/api/deployment/[id]/ado/repos/[project]/[repo]/prs/route"),
    url: "http://x/api/deployment/p1/ado/repos/acme/web/prs?provider=github",
    params: { id: "p1", project: "acme", repo: "web" },
  },
];

describe("the chosen source reaches the backend", () => {
  for (const route of ROUTES) {
    it(`${route.name} forwards ?provider`, async () => {
      const { GET } = await route.load();
      await GET(withUrl(route.url), { params: Promise.resolve(route.params) });

      expect(bffFetch).toHaveBeenCalledTimes(1);
      expect(bffFetch.mock.calls[0]![0]).toContain("provider=github");
    });

    it(`${route.name} sends no provider when none was picked`, async () => {
      // A single-source project must behave exactly as it did before the picker
      // existed — an empty `?provider=` would be a value, not an absence, and the
      // backend would treat it as naming a host it has no credential for.
      const { GET } = await route.load();
      const bare = route.url.split("?")[0]!;
      await GET(withUrl(bare), { params: Promise.resolve(route.params) });

      expect(bffFetch.mock.calls[0]![0]).not.toContain("provider");
    });
  }
});

describe("the sources route exists in its own right", () => {
  it("asks the backend which hosts this person can clone from", async () => {
    const { GET } = await import("@/app/api/dev/[id]/sources/route");
    await GET(withUrl("http://x/api/dev/p1/sources"), {
      params: Promise.resolve({ id: "p1" }),
    });

    expect(bffFetch.mock.calls[0]![0]).toBe("/dev/p1/sources");
  });
});
