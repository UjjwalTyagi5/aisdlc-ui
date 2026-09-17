// @vitest-environment jsdom
/**
 * Select a review target: a PR with nothing in it is SAID, and the whole branch is one
 * click away; a whole branch can also be picked directly.
 *
 * QuickLink's PR #35 prepared "0 files changed" — two branch names on one commit — and the
 * page staged an empty diff with nothing to do next.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const prepareReview = vi.fn();

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/lib/api/dev-workspace", () => ({
  listSourceProviders: vi.fn(async () => ({ providers: [{ id: "ado", label: "Azure DevOps" }] })),
  listAdoProjects: vi.fn(async () => [{ id: "proj-1", name: "QuickLinkProject" }]),
  listAdoRepos: vi.fn(async () => [{ id: "repo-1", name: "QuickLinkRepo" }]),
  listAdoBranches: vi.fn(async () => [
    { name: "feature/quicklink-initial", is_default: true },
    { name: "feature/116-117-link-management", is_default: false },
  ]),
}));
vi.mock("@/lib/api/code-review", () => ({
  listOpenPrs: vi.fn(async () => [
    { id: "35", title: "feat: Core URL Shortening", source_branch: "feature/116-117-link-management", target_branch: "feature/quicklink-initial", created_by: "" },
  ]),
  prepareReview: (...args: unknown[]) => prepareReview(...args),
}));

import { ReviewTargetDialog } from "@/components/app/review-target-dialog";

const base = {
  repo_name: "QuickLinkRepo", ado_project: "QuickLinkProject", source_branch: "feature/116-117-link-management",
  head_sha: "082f91e49bf0", base_sha: "082f91e49bf0", truncated: false, diff: "",
  unchanged_since_last_review: false, existing_review_id: null, existing_review_branch: null,
};

function renderDialog(onPrepared = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ReviewTargetDialog open onOpenChange={vi.fn()} projectId={"p1" as never} onPrepared={onPrepared} />
    </QueryClientProvider>,
  );
  return onPrepared;
}

afterEach(cleanup);
beforeEach(() => prepareReview.mockReset());

describe("ReviewTargetDialog", () => {
  it("answers an empty PR with the reason and reviews the whole branch instead", async () => {
    const user = userEvent.setup();
    prepareReview
      .mockResolvedValueOnce({
        ...base, status: "no_changes", mode: "pr", base_branch: "feature/quicklink-initial", pr_id: "35", files: [],
        no_changes_reason: "PR #35 (feature/116-117-link-management → feature/quicklink-initial) contains no changes: both branches point at the same commit (082f91e).",
      })
      .mockResolvedValueOnce({
        ...base, status: "ready", mode: "repo", base_branch: "", pr_id: null,
        files: [{ path: "src/index.js", status: "T", added: 40, removed: 0, language: "JavaScript", reviewable: true }],
      });
    const onPrepared = renderDialog();

    await user.click(screen.getByRole("button", { name: /Open PR/ }));
    await user.click(await screen.findByText("QuickLinkProject"));
    await user.click(await screen.findByText("QuickLinkRepo"));
    await user.click(await screen.findByText(/#35 · feat: Core URL Shortening/));
    await user.click(screen.getByRole("button", { name: "Prepare diff" }));

    expect(await screen.findByText("Nothing to review in this diff")).toBeInTheDocument();
    expect(screen.getByText(/both branches point at the same commit \(082f91e\)/)).toBeInTheDocument();
    expect(onPrepared).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /Review the whole branch instead/ }));
    await waitFor(() => expect(onPrepared).toHaveBeenCalledTimes(1));
    expect(prepareReview.mock.calls[1]![1]).toEqual({
      provider: "ado", mode: "repo", ado_project: "QuickLinkProject", repo_name: "QuickLinkRepo",
      source_branch: "feature/116-117-link-management",
    });
    expect(onPrepared.mock.calls[0]![0].mode).toBe("repo");
  });

  it("prepares a whole branch picked directly", async () => {
    const user = userEvent.setup();
    prepareReview.mockResolvedValueOnce({ ...base, status: "ready", mode: "repo", base_branch: "", pr_id: null, files: [] });
    const onPrepared = renderDialog();

    await user.click(screen.getByRole("button", { name: /Whole branch/ }));
    await user.click(await screen.findByText("QuickLinkProject"));
    await user.click(await screen.findByText("QuickLinkRepo"));
    expect(await screen.findByText("Branch to review")).toBeInTheDocument();
    await user.click(await screen.findByText("feature/116-117-link-management"));
    await user.click(screen.getByRole("button", { name: "Prepare branch" }));

    await waitFor(() => expect(onPrepared).toHaveBeenCalledTimes(1));
    expect(prepareReview.mock.calls[0]![1]).toMatchObject({ mode: "repo", source_branch: "feature/116-117-link-management" });
    expect(prepareReview.mock.calls[0]![1].base_branch).toBeUndefined();
  });
});
