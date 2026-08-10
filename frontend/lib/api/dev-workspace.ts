import { z } from "zod";

import {
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

export const listAdoProjects = (projectId: ProjectId) =>
  api(`/dev/${enc(projectId)}/ado/projects`, { schema: z.array(AdoProject) });

export const listAdoRepos = (projectId: ProjectId, adoProject: string) =>
  api(`/dev/${enc(projectId)}/ado/projects/${enc(adoProject)}/repos`, {
    schema: z.array(AdoRepo),
  });

export const listAdoBranches = (
  projectId: ProjectId,
  adoProject: string,
  repo: string,
) =>
  api(`/dev/${enc(projectId)}/ado/repos/${enc(adoProject)}/${enc(repo)}/branches`, {
    schema: z.array(AdoBranch),
  });

export const pullRepo = (
  projectId: ProjectId,
  body: { ado_project: string; repo_name: string; branch: string },
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
