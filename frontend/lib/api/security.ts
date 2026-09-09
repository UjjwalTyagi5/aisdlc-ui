import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";
import { AdoPr } from "@/lib/schemas/code-review";
import {
  PrepareScanResult,
  ScanSummaryRow,
  SecurityArtifact,
} from "@/lib/schemas/security";

import { api } from "./client";

const enc = encodeURIComponent;

// ADO project/repo/branch cascade reused from the dev-workspace client.

export const listOpenPrs = (
  projectId: ProjectId,
  adoProject: string,
  repo: string,
  provider?: string,
) =>
  api(
    `/security/${enc(projectId)}/ado/repos/${enc(adoProject)}/${enc(repo)}/prs${
      provider ? `?provider=${encodeURIComponent(provider)}` : ""
    }`,
    { schema: z.array(AdoPr) },
  );

export interface PrepareScanBody {
  /** Where the code lives — omitted when the project has a single source. */
  provider?: string;
  mode: "branch" | "pr";
  ado_project: string;
  repo_name: string;
  branch?: string;
  pr_id?: string;
}

export const prepareScan = (projectId: ProjectId, body: PrepareScanBody) =>
  api(`/security/${enc(projectId)}/scan/prepare`, {
    method: "POST",
    body,
    schema: PrepareScanResult,
  });

export const listScans = (projectId: ProjectId) =>
  api(`/security/${enc(projectId)}/scans`, { schema: z.array(ScanSummaryRow) });

export const getScan = (projectId: ProjectId, runId: string) =>
  api(`/security/${enc(projectId)}/scans/${enc(runId)}`, { schema: SecurityArtifact });
