import { z } from "zod";

import {
  SourceProviders,
  AdoProject,
  AdoRepo,
  AdoBranch,
  DevWorkspace,
  DevPr,
  WorkspaceTree,
  WorkspaceFile,
  WorkspaceChanges,
  FileChangedLines,
  type ProjectId,
} from "@/lib/schemas";

import { api } from "./client";

const enc = encodeURIComponent;

/** Which hosts this person can clone from on this project. */
export const listSourceProviders = (projectId: ProjectId) =>
  api(`/dev/${enc(projectId)}/sources`, { schema: SourceProviders });

/** `?provider=` only when a choice was offered — omitted, the backend uses the
 *  project's single configured source and behaves exactly as it did before. */
const withProvider = (provider?: string) =>
  provider ? `?provider=${encodeURIComponent(provider)}` : "";

/**
 * THE NAME SAYS `Ado` AND IT NO LONGER MEANS ONLY AZURE DEVOPS: on GitHub these are
 * owners, repositories and branches. Renaming reaches five routers, their proxies and
 * every caller, so the behaviour moved first and the name is debt.
 */
export const listAdoProjects = (projectId: ProjectId, provider?: string) =>
  api(`/dev/${enc(projectId)}/ado/projects${withProvider(provider)}`, {
    schema: z.array(AdoProject),
  });

export const listAdoRepos = (
  projectId: ProjectId,
  adoProject: string,
  provider?: string,
) =>
  api(
    `/dev/${enc(projectId)}/ado/projects/${enc(adoProject)}/repos${withProvider(provider)}`,
    { schema: z.array(AdoRepo) },
  );

export const listAdoBranches = (
  projectId: ProjectId,
  adoProject: string,
  repo: string,
  provider?: string,
) =>
  api(
    `/dev/${enc(projectId)}/ado/repos/${enc(adoProject)}/${enc(repo)}/branches${withProvider(provider)}`,
    { schema: z.array(AdoBranch) },
  );

export const pullRepo = (
  projectId: ProjectId,
  body: {
    /** Where the code lives — omitted when the project has a single source. */
    provider?: string;
    ado_project: string;
    repo_name: string;
    branch: string;
  },
) =>
  api(`/dev/${enc(projectId)}/workspace/pull`, {
    method: "POST",
    body,
    schema: DevWorkspace,
  });

export const getWorkspace = (projectId: ProjectId) =>
  api(`/dev/${enc(projectId)}/workspace`, { schema: DevWorkspace.nullable() });

export const listDevPrs = (projectId: ProjectId) =>
  api(`/dev/${enc(projectId)}/prs`, { schema: z.array(DevPr) });

export const getWorkspaceTree = (projectId: ProjectId) =>
  api(`/dev/${enc(projectId)}/workspace/tree`, { schema: WorkspaceTree });

export const getWorkspaceFile = (projectId: ProjectId, path: string) =>
  api(`/dev/${enc(projectId)}/workspace/file`, {
    query: { path },
    schema: WorkspaceFile,
  });

export const getWorkspaceChanges = (projectId: ProjectId) =>
  api(`/dev/${enc(projectId)}/workspace/changes`, { schema: WorkspaceChanges });

export const getFileChangedLines = (projectId: ProjectId, path: string) =>
  api(`/dev/${enc(projectId)}/workspace/file/changed-lines`, {
    query: { path },
    schema: FileChangedLines,
  });
