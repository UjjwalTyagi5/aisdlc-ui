import { z } from "zod";

export const AdoProject = z.object({
  id: z.string(),
  name: z.string(),
});
export type AdoProject = z.infer<typeof AdoProject>;

export const AdoRepo = z.object({
  id: z.string(),
  name: z.string(),
  default_branch: z.string().nullable().optional(),
  remote_url: z.string(),
});
export type AdoRepo = z.infer<typeof AdoRepo>;

export const AdoBranch = z.object({
  name: z.string(),
  is_default: z.boolean(),
});
export type AdoBranch = z.infer<typeof AdoBranch>;

export const DevWorkspace = z.object({
  ado_project: z.string(),
  repo_name: z.string(),
  branch: z.string(),
  remote_url: z.string(),
  work_dir: z.string(),
  commit_sha: z.string().nullable(),
  status: z.enum(["pulling", "ready", "error"]),
  pulled_by: z.string().nullable(),
  error: z.string().optional(),
});
export type DevWorkspace = z.infer<typeof DevWorkspace>;

export const DevPr = z.object({
  id: z.string(),
  title: z.string(),
  branch: z.string(),
  status: z.enum(["open", "review", "merged"]),
  url: z.string(),
  created_at: z.string(),
});
export type DevPr = z.infer<typeof DevPr>;

export const WorkspaceTree = z.object({
  ready: z.boolean(),
  repo_name: z.string().nullable().optional(),
  branch: z.string().nullable().optional(),
  paths: z.array(z.string()),
  truncated: z.boolean(),
});
export type WorkspaceTree = z.infer<typeof WorkspaceTree>;

export const WorkspaceFile = z.object({
  path: z.string(),
  content: z.string(),
  size: z.number(),
  binary: z.boolean(),
  truncated: z.boolean(),
});
export type WorkspaceFile = z.infer<typeof WorkspaceFile>;

export const ChangedFile = z.object({
  path: z.string(),
  status: z.enum(["modified", "added", "deleted", "renamed", "copied"]),
  additions: z.number(),
  deletions: z.number(),
});
export type ChangedFile = z.infer<typeof ChangedFile>;

export const WorkspaceChanges = z.object({
  base: z.string().nullable(),
  files: z.array(ChangedFile),
});
export type WorkspaceChanges = z.infer<typeof WorkspaceChanges>;

export const FileChangedLines = z.object({
  added_lines: z.array(z.number()),
});
export type FileChangedLines = z.infer<typeof FileChangedLines>;
