/**
 * The model picker's list belongs to a PROJECT, not to whichever unit is ambient.
 *
 * `getModelOptions` used to take no arguments, and `ModelSelector` called it that way
 * from every project screen. The backend then had no project to resolve against and
 * fell back to the active-workspace selector — a cookie that nothing sets outside the
 * create-BU dialog, so it resolved to the organisation's OLDEST unit. A project in
 * Lending was offered Payments' models.
 *
 * The sharper half is what happens once grants exist at all:
 * `effective_project_offerings` deliberately fails CLOSED when it has no project
 * context, returning an empty set rather than silently offering everything. So the
 * moment an Org Admin granted their first model, the omission stopped being "wrong
 * unit" and became "no models anywhere" — the picker showing "Connect a model
 * provider" on a project whose unit had models granted.
 *
 * These pin the query string, because that is the entire defect: a missing parameter,
 * with no type error and no failing request to show for it.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const apiMock = vi.fn((_path: string, _opts?: unknown) =>
  Promise.resolve({ options: [], default_offering_id: null }),
);

vi.mock("@/lib/api/client", () => ({
  api: (path: string, opts?: unknown) => apiMock(path, opts),
  ApiRequestError: class extends Error {},
}));

/** The path of the Nth call, asserted to exist so the tests fail loudly rather than
 *  passing vacuously if `api` was never reached. */
function calledPath(n = 0): string {
  const call = apiMock.mock.calls[n];
  expect(call, `expected api() call #${n}`).toBeDefined();
  return call![0];
}

const { getModelOptions } = await import("@/lib/api/models");
const { qk } = await import("@/lib/api/query-keys");

beforeEach(() => apiMock.mockClear());

describe("getModelOptions", () => {
  it("sends the project so the backend can resolve its unit", async () => {
    await getModelOptions("proj-abc");
    expect(apiMock).toHaveBeenCalledTimes(1);
    expect(calledPath()).toBe("/model/options?projectId=proj-abc");
  });

  it("encodes a project id that needs it, rather than splicing it in raw", async () => {
    await getModelOptions("a/b c&d");
    expect(calledPath()).toBe("/model/options?projectId=a%2Fb%20c%26d");
  });

  it("omits the parameter entirely when there is no project context", async () => {
    // Not the same as sending an empty one: `?projectId=` is a present-but-blank value
    // the backend would have to special-case.
    await getModelOptions();
    expect(calledPath()).toBe("/model/options");
  });
});

describe("the options query key", () => {
  it("separates projects, so two units' lists cannot share a cache entry", () => {
    expect(qk.model.options("proj-a")).not.toEqual(qk.model.options("proj-b"));
  });

  it("is stable for the same project", () => {
    expect(qk.model.options("proj-a")).toEqual(qk.model.options("proj-a"));
  });

  it("treats no project and an explicit null alike", () => {
    expect(qk.model.options()).toEqual(qk.model.options(null));
  });
});
