"use client";

import * as React from "react";
import { useTheme } from "next-themes";
import { Download, ImageDown, Maximize2, Minus, Plus } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";

export interface MermaidRendererProps {
  /** Mermaid source — `graph LR\n  A-->B` etc. */
  source: string;
  className?: string;
  /** Pan/zoom toolbar visibility. */
  showToolbar?: boolean;
  height?: number | string;
}

type State =
  | { kind: "idle" }
  | { kind: "rendering" }
  | { kind: "ready"; svg: string }
  | { kind: "error"; message: string };

/**
 * Lazy-loads mermaid on mount. Re-renders on theme change so nodes pick up
 * the correct palette. Ships with a simple zoom (0.5× – 3×) + SVG/PNG export.
 */
export function MermaidRenderer({
  source,
  className,
  showToolbar = true,
  height = 420,
}: MermaidRendererProps) {
  const { resolvedTheme } = useTheme();
  const [state, setState] = React.useState<State>({ kind: "idle" });
  const [zoom, setZoom] = React.useState(1);
  const idRef = React.useRef(`m-${Math.random().toString(36).slice(2)}`);

  const render = React.useCallback(async () => {
    setState({ kind: "rendering" });
    try {
      const mermaid = (await import("mermaid")).default;
      mermaid.initialize({
        startOnLoad: false,
        theme: resolvedTheme === "dark" ? "dark" : "default",
        securityLevel: "strict",
        fontFamily: "var(--font-sans)",
        // Without this, mermaid injects its own "bomb / Syntax error in text" SVG
        // into document.body on a parse failure (an orphan that overlaps the page).
        // We render our own clean fallback from the caught error instead.
        suppressErrorRendering: true,
      });
      // Validate first so a malformed diagram (LLM-generated ones sometimes are)
      // fails cleanly here rather than part-way through render.
      await mermaid.parse(source);
      const { svg } = await mermaid.render(idRef.current, source);
      setState({ kind: "ready", svg });
    } catch (err) {
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Render failed",
      });
    }
  }, [source, resolvedTheme]);

  React.useEffect(() => {
    void render();
  }, [render]);

  const exportSvg = () => {
    if (state.kind !== "ready") return;
    const blob = new Blob([state.svg], { type: "image/svg+xml" });
    triggerDownload(blob, "diagram.svg");
  };

  const exportPng = async () => {
    if (state.kind !== "ready") return;
    const img = new Image();
    img.src = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(state.svg)))}`;
    await new Promise((r) => (img.onload = r));
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth * 2;
    canvas.height = img.naturalHeight * 2;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(2, 2);
    ctx.drawImage(img, 0, 0);
    canvas.toBlob((blob) => {
      if (blob) triggerDownload(blob, "diagram.png");
    }, "image/png");
  };

  return (
    <div className={cn("bg-panel-elevated flex flex-col rounded-md border border-line-soft", className)}>
      {showToolbar && (
        <div className="flex items-center justify-between gap-2 border-b border-line-soft px-3 py-2">
          {/* Elevated header: font-display label for the panel */}
          <span className="font-display text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            Diagram
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
              aria-label="Zoom out"
            >
              <Minus className="size-3.5" aria-hidden />
            </Button>
            <span className="text-muted-foreground w-10 text-center font-mono text-[11px]">
              {(zoom * 100).toFixed(0)}%
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
              aria-label="Zoom in"
            >
              <Plus className="size-3.5" aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => setZoom(1)}
              aria-label="Reset zoom"
            >
              <Maximize2 className="size-3.5" aria-hidden />
            </Button>
            <div className="bg-line-soft mx-1 h-5 w-px" aria-hidden />
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 font-mono text-[11px]"
              onClick={exportSvg}
              disabled={state.kind !== "ready"}
            >
              <Download className="size-3.5" aria-hidden />
              SVG
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 font-mono text-[11px]"
              onClick={exportPng}
              disabled={state.kind !== "ready"}
            >
              <ImageDown className="size-3.5" aria-hidden />
              PNG
            </Button>
          </div>
        </div>
      )}

      <div
        className="relative flex-1 overflow-auto p-4"
        style={{ height: typeof height === "number" ? `${height}px` : height }}
      >
        {(state.kind === "idle" || state.kind === "rendering") && (
          <LoadingState variant="card" />
        )}
        {state.kind === "error" && (
          <div className="space-y-3">
            <ErrorState
              title="Diagram couldn't be rendered"
              description="The diagram source has a syntax error. Its text is preserved below."
              detail={state.message}
              onRetry={() => void render()}
            />
            <details className="border-line-soft rounded-md border">
              <summary className="text-muted-foreground hover:text-foreground cursor-pointer px-3 py-2 text-[11px] font-medium">
                Show diagram source
              </summary>
              <pre className="border-line-soft overflow-auto border-t px-3 py-2 font-mono text-[11px] leading-relaxed">
                {source}
              </pre>
            </details>
          </div>
        )}
        {state.kind === "ready" && (
          <div
            className="origin-top-left"
            style={{ transform: `scale(${zoom})` }}

            dangerouslySetInnerHTML={{ __html: state.svg }}
          />
        )}
      </div>
    </div>
  );
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
