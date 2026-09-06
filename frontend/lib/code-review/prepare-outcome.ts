import type { PrepareResult } from "@/lib/schemas/code-review";

/**
 * Whether a freshly-prepared target has ALREADY been reviewed at this exact commit,
 * and if so, which review to point the reader at.
 *
 * PRD §21.4 — "Skips redundant re-review when nothing changed since the last pass" —
 * is a thing to TELL the reader, not a decision to make for them. An earlier cut had
 * this hijack the page: it reopened the prior review, dropped the prepared target, and
 * so greyed out "Run review" with no way back, while the header still named the older
 * review's branch as though the selection had been ignored. Preparing a target now
 * always stages that target (matching the Security agent's identical flow — clean
 * slate, past runs in the switcher); this only decides whether to raise a notice.
 *
 * Lives here rather than in the page module because an App Router `page.tsx` may only
 * export its default component and Next's own reserved exports (`metadata`,
 * `dynamic`, `revalidate`, …) — a stray named export from a page is not a supported
 * shape, and the unit test needs to import this without pulling a route component in.
 */
export function unchangedReviewNotice(
  result: PrepareResult,
): { reviewId: string; matchedBranch: string | null } | null {
  if (!result.unchanged_since_last_review || !result.existing_review_id) return null;
  return {
    reviewId: result.existing_review_id,
    // Only worth naming when it is NOT the branch just picked: two names, one commit
    // is the case a bare "nothing changed" cannot explain.
    matchedBranch:
      result.existing_review_branch && result.existing_review_branch !== result.source_branch
        ? result.existing_review_branch
        : null,
  };
}
