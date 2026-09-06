import { describe, it, expect } from "vitest";

import { unchangedReviewNotice } from "@/lib/code-review/prepare-outcome";
import type { PrepareResult } from "@/lib/schemas/code-review";

const base: PrepareResult = {
  status: "ready",
  mode: "branch",
  repo_name: "svc-payments",
  ado_project: "payments",
  source_branch: "feature/x",
  base_branch: "main",
  pr_id: null,
  pr_title: null,
  head_sha: "aaa",
  base_sha: "bbb",
  files: [],
  diff: "diff --git a/x b/x",
  truncated: false,
  unchanged_since_last_review: false,
  existing_review_id: null,
  existing_review_branch: null,
};

describe("unchangedReviewNotice — PRD §21.4 skip redundant re-review", () => {
  it("raises a notice pointing at the prior review of this exact commit", () => {
    const result = { ...base, unchanged_since_last_review: true, existing_review_id: "run-1" };
    expect(unchangedReviewNotice(result)).toEqual({ reviewId: "run-1", matchedBranch: null });
  });

  it("names the other branch when a DIFFERENT branch sits on the same commit", () => {
    // The real report: two branch names, one commit. Without naming the branch that
    // was already reviewed, "nothing changed" reads as "it ignored the branch I picked".
    const result = {
      ...base,
      source_branch: "feature/create-branch-and-pr",
      unchanged_since_last_review: true,
      existing_review_id: "run-1",
      existing_review_branch: "feature/dup-banner-purple",
    };
    expect(unchangedReviewNotice(result)).toEqual({
      reviewId: "run-1",
      matchedBranch: "feature/dup-banner-purple",
    });
  });

  it("does not name the branch when it is the one just picked — that says nothing", () => {
    const result = {
      ...base,
      unchanged_since_last_review: true,
      existing_review_id: "run-1",
      existing_review_branch: "feature/x",
    };
    expect(unchangedReviewNotice(result)).toEqual({ reviewId: "run-1", matchedBranch: null });
  });

  it("raises nothing when the diff changed", () => {
    expect(unchangedReviewNotice(base)).toBeNull();
  });

  it("raises nothing if the flag is set but the backend gave no review to point at", () => {
    const result = { ...base, unchanged_since_last_review: true, existing_review_id: null };
    expect(unchangedReviewNotice(result)).toBeNull();
  });
});
