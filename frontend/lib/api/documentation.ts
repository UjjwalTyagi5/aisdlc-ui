import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";
import { AdoPr } from "@/lib/schemas/code-review";
import {
  DocConnector,
  DocSetResponse,
  PrepareDocResult,
} from "@/lib/schemas/documentation";

import { api } from "./client";

const enc = encodeURIComponent;

// ADO project/repo/branch cascade reused from the dev-workspace client.

export const listDocConnectors = (projectId: ProjectId) =>
  api(`/documentation/${enc(projectId)}/connectors`, {
    schema: z.object({ connectors: z.array(DocConnector) }),
  });

export const listOpenPrs = (projectId: ProjectId, adoProject: string, repo: string) =>
  api(`/documentation/${enc(projectId)}/ado/repos/${enc(adoProject)}/${enc(repo)}/prs`, {
    schema: z.array(AdoPr),
  });

export interface PrepareDocBody {
  mode: "branch" | "pr";
  ado_project: string;
  repo_name: string;
  branch?: string;
  pr_id?: string;
}

export const prepareDocs = (projectId: ProjectId, body: PrepareDocBody) =>
  api(`/documentation/${enc(projectId)}/prepare`, {
    method: "POST",
    body,
    schema: PrepareDocResult,
  });

export const getDocSet = (projectId: ProjectId, sessionId: string) =>
  api(`/documentation/${enc(projectId)}/docset/${enc(sessionId)}`, {
    schema: DocSetResponse,
  });
