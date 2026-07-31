import { z } from "zod";

/**
 * Copilot Artifacts protocol — the Zod mirror of the backend artifact events and
 * the `Artifact` shape the pipeline Artifacts panel renders.
 *
 * These events are additive to the base `CopilotEvent` union (see `types.ts`),
 * and — like every Copilot frame — are consumed RAW off the WS and validated
 * with `safeParse` before touching component state; malformed frames are
 * dropped (defense-in-depth).
 *
 * Contract (locked; backend built to this exactly):
 *   artifact.open   { run_id?, stage, artifact_id, kind, title } → open a streaming placeholder
 *   artifact.delta  { run_id?, artifact_id, content }            → append markdown to the open artifact
 *   artifact.end    { run_id?, artifact_id }                     → finalize the streaming artifact
 *   artifact.ready  { run_id?, stage, artifacts: Artifact[] }    → authoritative parsed section list for the stage
 */

// ── Artifact ────────────────────────────────────────────────────────────────

/**
 * Renderable artifact kinds. Each maps to an existing viewer via the registry
 * in `artifact-viewer.tsx`; see `RENDERER_NOTES` for the dispatch table.
 */
export const ArtifactKind = z.enum([
  "markdown",
  "mermaid",
  "openapi",
  "code",
  "image",
  "download",
  "code-tree",
  "file-tree",
  "link",
]);
export type ArtifactKind = z.infer<typeof ArtifactKind>;

export const Artifact = z.object({
  id: z.string(),
  stage: z.string(),
  kind: ArtifactKind,
  title: z.string(),
  /** Inline body for markdown / mermaid / openapi / code kinds. */
  content: z.string().optional(),
  /** External resource for image / download kinds (also markdown-embedded Kroki PNGs). */
  url: z.string().optional(),
  /** Monaco/code language hint (e.g. "sql", "yaml"). */
  language: z.string().optional(),
  /**
   * Stage whose generated output dir a `file-tree` artifact should browse
   * (e.g. "design", "testing"). Falls back to `stage` when omitted — the
   * backend currently emits both set to the same value.
   */
  source: z.string().optional(),
});
export type Artifact = z.infer<typeof Artifact>;

// ── WS events (server → client) ───────────────────────────────────────────────

export const ArtifactOpenEvent = z.object({
  type: z.literal("artifact.open"),
  run_id: z.string().optional(),
  stage: z.string(),
  artifact_id: z.string(),
  kind: z.string(),
  title: z.string(),
});
export type ArtifactOpenEvent = z.infer<typeof ArtifactOpenEvent>;

export const ArtifactDeltaEvent = z.object({
  type: z.literal("artifact.delta"),
  run_id: z.string().optional(),
  artifact_id: z.string(),
  content: z.string().default(""),
});
export type ArtifactDeltaEvent = z.infer<typeof ArtifactDeltaEvent>;

export const ArtifactEndEvent = z.object({
  type: z.literal("artifact.end"),
  run_id: z.string().optional(),
  artifact_id: z.string(),
});
export type ArtifactEndEvent = z.infer<typeof ArtifactEndEvent>;

export const ArtifactReadyEvent = z.object({
  type: z.literal("artifact.ready"),
  run_id: z.string().optional(),
  stage: z.string(),
  artifacts: z.array(Artifact).default([]),
});
export type ArtifactReadyEvent = z.infer<typeof ArtifactReadyEvent>;

/** The four artifact events, ready to fold into the base discriminated union. */
export const ARTIFACT_EVENTS = [
  ArtifactOpenEvent,
  ArtifactDeltaEvent,
  ArtifactEndEvent,
  ArtifactReadyEvent,
] as const;

// ── REST reads (BFF proxies) ──────────────────────────────────────────────────

/** `GET /api/runs/[id]/artifacts` → { artifacts: Artifact[] }. */
export const ArtifactsRead = z.object({
  artifacts: z.array(Artifact).default([]),
});
export type ArtifactsRead = z.infer<typeof ArtifactsRead>;

export const TranscriptMessage = z.object({
  role: z.enum(["user", "agent"]),
  content: z.string(),
  stage: z.string().nullish(),
});
export type TranscriptMessage = z.infer<typeof TranscriptMessage>;

/** `GET /api/runs/[id]/transcript` → { messages: TranscriptMessage[] }. */
export const TranscriptRead = z.object({
  messages: z.array(TranscriptMessage).default([]),
});
export type TranscriptRead = z.infer<typeof TranscriptRead>;

// ── Renderer registry notes ───────────────────────────────────────────────────

/**
 * Documented kind → viewer mapping. `artifact-viewer.tsx` is the executable
 * dispatcher; this table is the reference the spec's renderer registry describes
 * and the source for the panel's per-kind label/icon affordances.
 *
 * A `markdown` artifact renders ```mermaid fences inline automatically (via
 * `MarkdownMessage` → `MermaidRenderer`), so the standalone `mermaid` kind is
 * only for a diagram delivered as bare mermaid source.
 */
export interface RendererNote {
  viewer: string;
  /** Which field the viewer reads. */
  source: "content" | "url";
  label: string;
}

export const RENDERER_NOTES: Record<ArtifactKind, RendererNote> = {
  markdown: { viewer: "MarkdownMessage", source: "content", label: "Document" },
  mermaid: { viewer: "MermaidRenderer", source: "content", label: "Diagram" },
  openapi: { viewer: "OpenApiViewer", source: "content", label: "API spec" },
  code: { viewer: "MonacoViewer", source: "content", label: "Code" },
  image: { viewer: "img", source: "url", label: "Image" },
  download: { viewer: "download", source: "url", label: "File" },
  "code-tree": { viewer: "CodeTreeView", source: "content", label: "Repository" },
  "file-tree": { viewer: "CodeTreeView", source: "content", label: "Generated files" },
  link: { viewer: "link", source: "url", label: "Link" },
};

/** Narrow a raw event `kind` string to a known `ArtifactKind`, defaulting to markdown. */
export function rendererFor(kind: string): ArtifactKind {
  const parsed = ArtifactKind.safeParse(kind);
  return parsed.success ? parsed.data : "markdown";
}
