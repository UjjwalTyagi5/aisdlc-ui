import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";
import { AdoPr } from "@/lib/schemas/code-review";
import {
  DeployConnector,
  PrepareDeployResult,
  ReleaseResponse,
} from "@/lib/schemas/deployment";

import { api } from "./client";

const enc = encodeURIComponent;

// ADO project/repo/branch cascade reused from the dev-workspace client.

export const listDeployConnectors = (projectId: ProjectId) =>
  api(`/deployment/${enc(projectId)}/connectors`, {
    schema: z.object({ connectors: z.array(DeployConnector) }),
  });

export const listOpenPrs = (projectId: ProjectId, adoProject: string, repo: string) =>
  api(`/deployment/${enc(projectId)}/ado/repos/${enc(adoProject)}/${enc(repo)}/prs`, {
    schema: z.array(AdoPr),
  });

export interface PrepareDeployBody {
  mode: "branch" | "pr";
  ado_project: string;
  repo_name: string;
  branch?: string;
  pr_id?: string;
  environment: string;
  deploy_via?: string;
  image_registry?: string;
  image_name?: string;
  namespace?: string;
}

export const prepareDeploy = (projectId: ProjectId, body: PrepareDeployBody) =>
  api(`/deployment/${enc(projectId)}/deploy/prepare`, {
    method: "POST",
    body,
    schema: PrepareDeployResult,
  });

export const getRelease = (projectId: ProjectId, sessionId: string) =>
  api(`/deployment/${enc(projectId)}/release/${enc(sessionId)}`, {
    schema: ReleaseResponse,
  });
