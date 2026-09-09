import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";
import {
  AdoPr,
  PrepareResult,
  ReviewSummaryRow,
  CodeReviewArtifact,
} from "@/lib/schemas/code-review";

import { api } from "./client";

const enc = encodeURIComponent;

// ADO project/repo/branch cascade is reused from the dev-workspace client
// (listAdoProjects / listAdoRepos / listAdoBranches) — same generic picker.

export const listOpenPrs = (
  projectId: ProjectId,
  adoProject: string,
  repo: string,
  provider?: string,
) =>
  api(
    `/code-review/${enc(projectId)}/ado/repos/${enc(adoProject)}/${enc(repo)}/prs${
      provider ? `?provider=${encodeURIComponent(provider)}` : ""
    }`,
    { schema: z.array(AdoPr) },
  );

export interface PrepareBody {
  /** Where the code lives — omitted when the project has a single source. */
  provider?: string;
  mode: "branch" | "pr";
  ado_project: string;
  repo_name: string;
  source_branch?: string;
  base_branch?: string;
  pr_id?: string;
}

export const prepareReview = (projectId: ProjectId, body: PrepareBody) =>
  api(`/code-review/${enc(projectId)}/review/prepare`, {
    method: "POST",
    body,
    schema: PrepareResult,
  });

export const listReviews = (projectId: ProjectId) =>
  api(`/code-review/${enc(projectId)}/reviews`, { schema: z.array(ReviewSummaryRow) });

export const getReview = (projectId: ProjectId, runId: string) =>
  api(`/code-review/${enc(projectId)}/reviews/${enc(runId)}`, {
    schema: CodeReviewArtifact,
  });
