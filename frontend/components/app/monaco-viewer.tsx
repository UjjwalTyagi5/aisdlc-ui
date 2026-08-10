"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";

import { cn } from "@/lib/utils";
import { LoadingState } from "@/components/ui/loading-state";

/**
 * Read-only single-file Monaco viewer. Lazy-loaded so it stays off the
 * initial bundle. Paired with `<DiffViewer>` — same engine, same theme
 * switching, same language detection helpers.
 */
const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.default),
  { ssr: false, loading: () => <LoadingState variant="card" /> },
);

const EXT_LANGUAGE: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  go: "go",
  rs: "rust",
  java: "java",
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

export interface MonacoViewerProps {
  value: string;
  filename?: string;
  language?: string;
  className?: string;
  height?: number | string;
  /** Hide the Monaco minimap for small viewers. Default: hidden. */
  minimap?: boolean;
}

export function MonacoViewer({
  value,
  filename,
  language,
  className,
  height = 420,
  minimap = false,
}: MonacoViewerProps) {
  const { resolvedTheme } = useTheme();
  const lang = inferLanguage(filename, language);

  return (
    <div className={cn("bg-panel-elevated rounded-md border border-line-soft", className)}>
      {filename && (
        <div
          className="flex items-baseline gap-2 border-b border-line-soft px-3 py-2"
          title={filename}
        >
          <span className="font-display shrink-0 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            Code
          </span>
          <span className="truncate font-mono text-[11px] text-muted-foreground">{filename}</span>
        </div>
      )}
      <div style={{ height }}>
        <MonacoEditor
          value={value}
          language={lang}
          theme={resolvedTheme === "dark" ? "vs-dark" : "vs"}
          options={{
            readOnly: true,
            minimap: { enabled: minimap },
            scrollbar: { vertical: "auto", horizontal: "auto" },
            fontSize: 13,
            lineNumbersMinChars: 3,
            smoothScrolling: true,
            renderWhitespace: "selection",
            accessibilitySupport: "auto",
          }}
        />
      </div>
    </div>
  );
}
