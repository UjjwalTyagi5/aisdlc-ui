import { redirect } from "next/navigation";

/**
 * `/projects/[id]/review` and `/projects/[id]/code-review` were two routes for
 * the same agent: this one rendered a generic StageWorkbench stub, while
 * `code-review/` is the full ADO-integrated Code Review screen.
 *
 * The PRD has exactly one Code Review agent (§7, stage 04), so this route now
 * redirects rather than presenting a second, weaker version of it. Both URLs
 * keep working — nothing that linked here breaks.
 */
export default async function CodeReviewRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/projects/${id}/code-review`);
}
