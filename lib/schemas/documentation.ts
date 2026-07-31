import { z } from "zod";

export const DocConnector = z.object({
  kind: z.string(),
  label: z.string(),
  available: z.boolean(),
});
export type DocConnector = z.infer<typeof DocConnector>;

export const PrepareDocResult = z.object({
  status: z.string(),
  mode: z.enum(["branch", "pr"]),
  repo_name: z.string(),
  ado_project: z.string(),
  branch: z.string(),
  pr_id: z.string().nullable().optional(),
  pr_title: z.string().nullable().optional(),
  head_sha: z.string(),
  languages: z.array(z.string()).default([]),
  upstream_summary: z.string().default(""),
});
export type PrepareDocResult = z.infer<typeof PrepareDocResult>;

export const DocType = z.enum([
  "doc_set", "overview", "sdd", "api_reference", "code_summary",
  "changelog", "release_notes", "rtm", "run_summary", "compliance", "custom",
]);
export type DocType = z.infer<typeof DocType>;

export const GeneratedDoc = z.object({
  id: z.string(),
  type: z.string().default("custom"),
  title: z.string().default(""),
  filename: z.string().default(""),
  format: z.string().default("md"),
  path: z.string().default(""),
  contents: z.string().default(""),
  bytes: z.number().default(0),
});
export type GeneratedDoc = z.infer<typeof GeneratedDoc>;

export const DocSetResponse = z.object({
  documents: z.array(GeneratedDoc).default([]),
  doc_list: z
    .array(z.object({
      id: z.string(), type: z.string().default("custom"), title: z.string().default(""),
      filename: z.string().default(""), format: z.string().default("md"), bytes: z.number().default(0),
    }))
    .default([]),
  pr_url: z.string().nullable().optional(),
  context: z.object({
    repo_name: z.string().default(""),
    ado_project: z.string().default(""),
    mode: z.enum(["branch", "pr"]).default("branch"),
    source_branch: z.string().default(""),
    pr_id: z.string().nullable().optional(),
    head_sha: z.string().default(""),
    languages: z.array(z.string()).default([]),
    upstream_summary: z.string().default(""),
  }),
});
export type DocSetResponse = z.infer<typeof DocSetResponse>;
