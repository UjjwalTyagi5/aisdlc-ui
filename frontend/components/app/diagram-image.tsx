"use client";

import * as React from "react";
import { Download, Maximize2, Minus, Plus, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export interface DiagramImageProps {
  src?: string;
  alt?: string;
  className?: string;
  /** Toolbar visibility. Off for tiny inline images (e.g. badges). */
  showToolbar?: boolean;
  height?: number | string;
}

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;

/**
 * Rich container for a rendered image (Kroki C4/PlantUML/ERD PNGs embedded in
 * design docs). Mirrors `MermaidRenderer`'s toolbar — a "Diagram" label, zoom
 * (0.5×–3×), reset, fullscreen, and download — so mermaid + kroki diagrams look
 * and behave identically wherever they appear (chat, artifacts panel, standalone
 * pages). Fails soft: a missing/broken src degrades to nothing; a cross-origin
 * download that can't be fetched falls back to opening the src in a new tab.
 */
export function DiagramImage({
  src,
  alt,
  className,
  showToolbar = true,
  height = 420,
}: DiagramImageProps) {
  const [zoom, setZoom] = React.useState(1);
  const [fullscreen, setFullscreen] = React.useState(false);
  const [broken, setBroken] = React.useState(false);

  const zoomOut = React.useCallback(
    () => setZoom((z) => Math.max(MIN_ZOOM, +(z - ZOOM_STEP).toFixed(2))),
    [],
  );
  const zoomIn = React.useCallback(
    () => setZoom((z) => Math.min(MAX_ZOOM, +(z + ZOOM_STEP).toFixed(2))),
    [],
  );
  const reset = React.useCallback(() => setZoom(1), []);

  const filename = React.useMemo(() => deriveFilename(src, alt), [src, alt]);

  const download = React.useCallback(async () => {
    if (!src) return;
    try {
      const res = await fetch(src, { mode: "cors" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      // Cross-origin / opaque response — can't read the blob. Open it instead so
      // the user can still save it via the browser's own controls.
      try {
        window.open(src, "_blank", "noopener,noreferrer");
      } catch {
        /* popup blocked — nothing more we can safely do */
      }
    }
  }, [src, filename]);

  if (!src) return null;

  return (
    <div
      className={cn(
        "bg-panel-elevated flex flex-col rounded-md border border-line-soft",
        className,
      )}
    >
      {showToolbar && (
        <Toolbar
          zoom={zoom}
          broken={broken}
          onZoomOut={zoomOut}
          onZoomIn={zoomIn}
          onReset={reset}
          onFullscreen={() => setFullscreen(true)}
          onDownload={download}
        />
      )}

      <div
        className="relative flex-1 overflow-auto p-4"
        style={{ height: typeof height === "number" ? `${height}px` : height }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- agent-generated Kroki PNG/SVG at an arbitrary /generated/ URL */}
        <img
          src={src}
          alt={alt ?? "Diagram"}
          onError={() => setBroken(true)}
          // Responsive by default: at 100% the image fits the panel width and never
          // exceeds it (max-w-full h-auto), so a very wide C4/ER diagram scales down
          // to fit rather than overflowing/breaking. Zoom is expressed as layout
          // width (a % of the container) so `overflow-auto` produces real scrollbars
          // when zoomed in — the diagram scrolls, it never clips.
          className={cn(
            "block h-auto origin-top-left",
            zoom <= 1 ? "max-w-full" : "max-w-none",
          )}
          style={zoom === 1 ? undefined : { width: `${zoom * 100}%` }}
        />
      </div>

      {fullscreen && (
        <FullscreenOverlay src={src} alt={alt} onClose={() => setFullscreen(false)} />
      )}
    </div>
  );
}

function Toolbar({
  zoom,
  broken,
  onZoomOut,
  onZoomIn,
  onReset,
  onFullscreen,
  onDownload,
}: {
  zoom: number;
  broken: boolean;
  onZoomOut: () => void;
  onZoomIn: () => void;
  onReset: () => void;
  onFullscreen: () => void;
  onDownload: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line-soft px-3 py-2">
      <span className="font-display text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        Diagram
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0"
          onClick={onZoomOut}
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
          onClick={onZoomIn}
          aria-label="Zoom in"
        >
          <Plus className="size-3.5" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0"
          onClick={onReset}
          aria-label="Reset zoom"
        >
          <Maximize2 className="size-3.5" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0"
          onClick={onFullscreen}
          aria-label="View fullscreen"
        >
          <Maximize2 className="size-3.5" aria-hidden />
        </Button>
        <div className="bg-line-soft mx-1 h-5 w-px" aria-hidden />
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1 px-2 font-mono text-[11px]"
          onClick={onDownload}
          disabled={broken}
        >
          <Download className="size-3.5" aria-hidden />
          PNG
        </Button>
      </div>
    </div>
  );
}

function FullscreenOverlay({
  src,
  alt,
  onClose,
}: {
  src: string;
  alt?: string;
  onClose: () => void;
}) {
  const [zoom, setZoom] = React.useState(1);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // Lock body scroll while the overlay is open.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={alt ?? "Diagram fullscreen view"}
      className="fixed inset-0 z-50 flex flex-col bg-black/85 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* Toolbar (stop propagation so control clicks don't close the overlay) */}
      <div
        className="flex items-center justify-end gap-1 border-b border-white/10 px-4 py-2.5"
        onClick={(e) => e.stopPropagation()}
      >
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0 text-white/70 hover:bg-white/10 hover:text-white"
          onClick={() => setZoom((z) => Math.max(MIN_ZOOM, +(z - ZOOM_STEP).toFixed(2)))}
          aria-label="Zoom out"
        >
          <Minus className="size-4" aria-hidden />
        </Button>
        <span className="w-12 text-center font-mono text-[12px] text-white/70">
          {(zoom * 100).toFixed(0)}%
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0 text-white/70 hover:bg-white/10 hover:text-white"
          onClick={() => setZoom((z) => Math.min(MAX_ZOOM, +(z + ZOOM_STEP).toFixed(2)))}
          aria-label="Zoom in"
        >
          <Plus className="size-4" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0 text-white/70 hover:bg-white/10 hover:text-white"
          onClick={() => setZoom(1)}
          aria-label="Reset zoom"
        >
          <Maximize2 className="size-4" aria-hidden />
        </Button>
        <div className="mx-1 h-5 w-px bg-white/15" aria-hidden />
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0 text-white/70 hover:bg-white/10 hover:text-white"
          onClick={onClose}
          aria-label="Close fullscreen"
        >
          <X className="size-4" aria-hidden />
        </Button>
      </div>

      {/* Zoomable body. At 100% the image is contained to the viewport at its
          natural aspect ratio; zooming scales it up and the container scrolls. */}
      <div className="flex flex-1 items-center justify-center overflow-auto p-6">
        {/* eslint-disable-next-line @next/next/no-img-element -- agent-generated Kroki PNG/SVG at an arbitrary /generated/ URL */}
        <img
          src={src}
          alt={alt ?? "Diagram"}
          onClick={(e) => e.stopPropagation()}
          className="max-h-full max-w-full object-contain"
          style={zoom === 1 ? undefined : { transform: `scale(${zoom})` }}
        />
      </div>
    </div>
  );
}

/** Best-effort download filename from the alt text or the URL path. */
function deriveFilename(src?: string, alt?: string): string {
  const fromAlt = alt?.trim().replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "");
  if (fromAlt) return /\.[a-z0-9]+$/i.test(fromAlt) ? fromAlt : `${fromAlt}.png`;
  if (src) {
    try {
      const path = new URL(src, "http://x").pathname;
      const last = path.split("/").filter(Boolean).pop();
      if (last && /\.[a-z0-9]+$/i.test(last)) return last;
    } catch {
      /* not a parseable URL — fall through */
    }
  }
  return "diagram.png";
}
