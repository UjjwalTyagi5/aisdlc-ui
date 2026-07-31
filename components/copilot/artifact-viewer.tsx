"use client";

import * as React from "react";
import { Download, ExternalLink, FileText, ImageOff } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { MarkdownMessage } from "@/components/app/markdown-message";
import { MermaidRenderer } from "@/components/app/mermaid-renderer";
import { DiagramImage } from "@/components/app/diagram-image";
import { OpenApiViewer } from "@/components/app/openapi-viewer";
import { MonacoViewer } from "@/components/app/monaco-viewer";
import { CodeTreeView } from "@/components/copilot/code-tree-view";
import type { Artifact } from "@/lib/copilot/artifacts";

export interface ArtifactViewerProps {
  artifact: Artifact;
  /** The run this artifact belongs to — needed by the live code-tree viewer. */
  runId: string;
  /** True while `artifact.delta` frames are still building `content`. */
  streaming?: boolean;
  className?: string;
}

/**
 * Dispatches an `Artifact` to the right existing viewer by `kind` (the renderer
 * registry, `RENDERER_NOTES`). Thin by design: it delegates rendering to the
 * shared standalone viewers and only owns the empty / streaming affordances.
 *
 *   markdown → MarkdownMessage (renders ```mermaid fences inline)
 *   mermaid  → MermaidRenderer
 *   openapi  → OpenApiViewer
 *   code     → MonacoViewer (language passed through)
 *   image    → <img> + download link
 *   download → download button → url
 *   file-tree → CodeTreeView, scoped to `artifact.source ?? artifact.stage`
 */
export function ArtifactViewer({ artifact, runId, streaming, className }: ArtifactViewerProps) {
  const { kind, content = "", url, language, title } = artifact;

  // While the doc is still streaming in, always render as markdown so the panel
  // builds up live exactly like the chat does — the authoritative kind-specific
  // viewer takes over once `artifact.ready` replaces the streamed artifact.
  // Resource kinds (image/download/link) and the live code-tree own their own
  // rendering and never fall through to the streamed-markdown path.
  const ownsRendering =
    kind === "image" ||
    kind === "download" ||
    kind === "link" ||
    kind === "code-tree" ||
    kind === "file-tree";
  const showAsMarkdown = kind === "markdown" || (streaming && !ownsRendering);

  if (showAsMarkdown) {
    if (!content.trim()) {
      return <StreamingPlaceholder title={title} className={className} />;
    }
    return (
      <div className={cn("min-h-0 overflow-auto px-4 py-4", className)}>
        <MarkdownMessage content={content} />
        {streaming && (
          <span className="bg-brand-bright ml-0.5 inline-block h-3.5 w-1.5 animate-pulse align-middle" />
        )}
      </div>
    );
  }

  switch (kind) {
    case "mermaid":
      return (
        <div className={cn("min-h-0 overflow-auto p-4", className)}>
          <MermaidRenderer source={content} height="100%" showToolbar />
        </div>
      );

    case "openapi":
      return (
        <div className={cn("min-h-0 overflow-auto p-4", className)}>
          <OpenApiViewer source={content} />
        </div>
      );

    case "code":
      return (
        <div className={cn("min-h-0 overflow-auto p-4", className)}>
          <MonacoViewer value={content} language={language} filename={title} height={520} />
        </div>
      );

    case "code-tree":
      return <CodeTreeView runId={runId} className={className} />;

    case "file-tree":
      return <CodeTreeView runId={runId} source={artifact.source ?? artifact.stage} className={className} />;

    case "link":
      return <LinkArtifact url={url} title={title} className={className} />;

    case "image":
      return <ImageArtifact url={url} title={title} className={className} />;

    case "download":
      return <DownloadArtifact url={url} title={title} className={className} />;

    default:
      // Unknown kind — fall back to markdown/plaintext so nothing renders blank.
      return (
        <div className={cn("min-h-0 overflow-auto px-4 py-4", className)}>
          <MarkdownMessage content={content || `_${title}_`} />
        </div>
      );
  }
}

// ── Sub-components ────────────────────────────────────────────────────────

function StreamingPlaceholder({ title, className }: { title: string; className?: string }) {
  return (
    <div className={cn("flex min-h-0 flex-1 items-center justify-center p-8", className)}>
      <div className="text-center">
        <span className="border-brand-bright/30 bg-brand-bright/10 text-brand-bright mx-auto mb-3 flex size-10 items-center justify-center rounded-full border">
          <FileText className="size-4 animate-pulse" aria-hidden />
        </span>
        <p className="text-[13px] font-medium text-foreground">{title}</p>
        <p className="text-muted-foreground mt-1 text-[12px]">Generating…</p>
      </div>
    </div>
  );
}

function ImageArtifact({
  url,
  title,
  className,
}: {
  url?: string;
  title: string;
  className?: string;
}) {
  if (!url) return <MissingResource title={title} className={className} />;
  return (
    <div className={cn("min-h-0 overflow-auto p-4", className)}>
      <DiagramImage src={url} alt={title} height="100%" showToolbar />
    </div>
  );
}

function DownloadArtifact({
  url,
  title,
  className,
}: {
  url?: string;
  title: string;
  className?: string;
}) {
  if (!url) return <MissingResource title={title} className={className} />;
  return (
    <div className={cn("flex min-h-0 flex-1 items-center justify-center p-8", className)}>
      <div className="border-line-soft bg-panel-elevated/40 flex max-w-sm flex-col items-center gap-3 rounded-[var(--radius)] border p-6 text-center">
        <span className="border-brand-bright/30 bg-brand-bright/10 text-brand-bright flex size-10 items-center justify-center rounded-full border">
          <FileText className="size-4" aria-hidden />
        </span>
        <div>
          <p className="text-[13px] font-medium text-foreground">{title}</p>
          <p className="text-muted-foreground mt-1 text-[12px] leading-snug">
            This artifact is available as a downloadable file.
          </p>
        </div>
        <Button
          size="sm"
          asChild
          className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r text-white"
        >
          <a href={url} download>
            <Download className="size-4" aria-hidden />
            Download
          </a>
        </Button>
      </div>
    </div>
  );
}

function LinkArtifact({
  url,
  title,
  className,
}: {
  url?: string;
  title: string;
  className?: string;
}) {
  if (!url) return <MissingResource title={title} className={className} />;
  return (
    <div className={cn("flex min-h-0 flex-1 items-center justify-center p-8", className)}>
      <div className="border-line-soft bg-panel-elevated/40 flex max-w-sm flex-col items-center gap-3 rounded-[var(--radius)] border p-6 text-center">
        <span className="border-brand-bright/30 bg-brand-bright/10 text-brand-bright flex size-10 items-center justify-center rounded-full border">
          <ExternalLink className="size-4" aria-hidden />
        </span>
        <div>
          <p className="text-[13px] font-medium text-foreground">{title}</p>
          <p className="text-muted-foreground mt-1 text-[12px] leading-snug">
            Opens in a new tab.
          </p>
        </div>
        <Button
          size="sm"
          asChild
          className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-r text-white"
        >
          <a href={url} target="_blank" rel="noreferrer noopener">
            <ExternalLink className="size-4" aria-hidden />
            {title.toLowerCase().includes("pull request") ? "Open Pull Request" : "Open link"}
          </a>
        </Button>
      </div>
    </div>
  );
}

function MissingResource({ title, className }: { title: string; className?: string }) {
  return (
    <div className={cn("flex min-h-0 flex-1 items-center justify-center p-8", className)}>
      <div className="text-muted-foreground text-center">
        <ImageOff className="mx-auto mb-2 size-5" aria-hidden />
        <p className="text-[12.5px]">{title} — resource unavailable.</p>
      </div>
    </div>
  );
}
