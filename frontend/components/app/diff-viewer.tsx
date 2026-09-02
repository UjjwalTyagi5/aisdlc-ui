"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { Columns2, Rows2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";

/**
 * Heavy dependency — Monaco + the React wrapper weigh ~1MB. Dynamic-import
 * keeps it off the initial bundle. Suspense fallback uses our table skeleton.
 */
const MonacoDiffEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.DiffEditor),
  { ssr: false, loading: () => <LoadingState variant="table" rows={8} /> },
);

export type DiffMode = "split" | "unified";

export interface DiffViewerProps {
  /** Previous version of the file. */
  original: string;
  /** Current version. */
  modified: string;
  /** File path — used for filename label + Monaco language detection. */
  filename?: string;
  /** Monaco language id — overrides detection from `filename`. */
  language?: string;
  /** Initial layout; users can toggle via the top-right buttons. */
  defaultMode?: DiffMode;
  /** Rendered read-only by default. */
  readOnly?: boolean;
  /** Show the mode toggle + filename bar. */
  showToolbar?: boolean;
  className?: string;
  height?: number | string;
}

const EXT_LANGUAGE: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  go: "go",
  rs: "rust",
  java: "java",
  kt: "kotlin",
  rb: "ruby",
  cs: "csharp",
  sql: "sql",
  md: "markdown",
  yml: "yaml",
  yaml: "yaml",
  json: "json",
  css: "css",
  html: "html",
};

function inferLanguage(filename?: string, explicit?: string): string {
  if (explicit) return explicit;
  if (!filename) return "plaintext";
  const ext = filename.split(".").pop()?.toLowerCase();
  return ext ? (EXT_LANGUAGE[ext] ?? "plaintext") : "plaintext";
}

export function DiffViewer({
  original,
  modified,
  filename,
  language,
  defaultMode = "split",
  readOnly = true,
  showToolbar = true,
  className,
  height = 480,
}: DiffViewerProps) {
  const { resolvedTheme } = useTheme();
  const [mode, setMode] = React.useState<DiffMode>(defaultMode);
  const lang = inferLanguage(filename, language);

  return (
    <div className={cn("bg-panel-elevated rounded-md border border-line-soft", className)}>
      {showToolbar && (
        <div className="flex items-center justify-between gap-2 border-b border-line-soft px-3 py-2">
          {/* Panel label: font-display eyebrow + mono filename */}
          <div className="flex min-w-0 items-baseline gap-2">
            <span className="font-display shrink-0 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
              Diff
            </span>
            <span className="truncate font-mono text-[11px] text-muted-foreground" title={filename}>
              {filename ?? "(untitled)"}
            </span>
          </div>
          <div className="flex items-center gap-1" role="tablist" aria-label="Diff layout">
            <Button
              variant={mode === "split" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setMode("split")}
              role="tab"
              aria-selected={mode === "split"}
              className="h-7 gap-1 px-2 text-xs"
            >
              <Columns2 className="size-3.5" aria-hidden />
              Split
            </Button>
            <Button
              variant={mode === "unified" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setMode("unified")}
              role="tab"
              aria-selected={mode === "unified"}
              className="h-7 gap-1 px-2 text-xs"
            >
              <Rows2 className="size-3.5" aria-hidden />
              Unified
            </Button>
          </div>
        </div>
      )}
      <div style={{ height }}>
        <MonacoDiffEditor
          original={original}
          modified={modified}
          language={lang}
          theme={resolvedTheme === "dark" ? "vs-dark" : "vs"}
          options={{
            readOnly,
            renderSideBySide: mode === "split",
            minimap: { enabled: false },
            scrollbar: { vertical: "auto", horizontal: "auto" },
            fontSize: 13,
            lineNumbersMinChars: 3,
            smoothScrolling: true,
            renderWhitespace: "selection",
            renderOverviewRuler: false,
            // Accessibility: screen readers read the *modified* content by default
            accessibilitySupport: "auto",
            // Defense in depth: makes Monaco observe its container's size (via
            // ResizeObserver) instead of only measuring once at mount. Safe,
            // zero-behavior-change for existing always-visible callers; protects
            // any future caller that mounts this inside a container whose size
            // isn't stable at mount time (e.g. revealed from display:none).
            automaticLayout: true,
          }}
        />
      </div>
    </div>
  );
}
