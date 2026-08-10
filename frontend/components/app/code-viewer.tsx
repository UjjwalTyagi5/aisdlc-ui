"use client";

import * as React from "react";
import hljs from "highlight.js";

// Theme-aware token palette (light = VS Code Light+, dark = Dark+), scoped to
// `.code-viewer` so it follows the app's light/dark mode.
import "./code-viewer.css";

import { cn } from "@/lib/utils";

const EXT_LANG: Record<string, string> = {
  ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  mjs: "javascript", cjs: "javascript", py: "python", rb: "ruby", go: "go",
  rs: "rust", java: "java", kt: "kotlin", cs: "csharp", c: "c", h: "c",
  cpp: "cpp", cc: "cpp", hpp: "cpp", php: "php", swift: "swift", scala: "scala",
  sh: "bash", bash: "bash", zsh: "bash", ps1: "powershell", sql: "sql",
  css: "css", scss: "scss", less: "less", html: "xml", htm: "xml", xml: "xml",
  svg: "xml", vue: "xml", json: "json", yml: "yaml", yaml: "yaml", toml: "ini",
  ini: "ini", md: "markdown", markdown: "markdown", dockerfile: "dockerfile",
  makefile: "makefile", proto: "protobuf", graphql: "graphql", gql: "graphql",
  tf: "hcl", hcl: "hcl", lua: "lua", r: "r", dart: "dart", pl: "perl",
};

function langFor(filename?: string): string | undefined {
  if (!filename) return undefined;
  const base = filename.split("/").pop()!.toLowerCase();
  if (base === "dockerfile") return "dockerfile";
  if (base === "makefile") return "makefile";
  if (base.startsWith(".") && !base.slice(1).includes(".")) return undefined; // .gitignore, .env → plain
  const ext = base.includes(".") ? base.split(".").pop()! : base;
  return EXT_LANG[ext];
}

/**
 * Split highlight.js HTML into per-line HTML strings, keeping `<span>` tokens
 * balanced across line breaks (a multi-line comment/string is one span that
 * spans lines, so each line must close + reopen the open spans).
 */
function splitHighlightedLines(html: string): string[] {
  const lines: string[] = [];
  const stack: string[] = []; // open <span ...> tags
  let cur = "";
  const re = /(<[^>]+>)|([^<]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    if (m[1]) {
      const tag = m[1];
      cur += tag;
      if (tag.startsWith("</")) stack.pop();
      else if (!tag.endsWith("/>")) stack.push(tag);
    } else {
      const parts = (m[2] ?? "").split("\n");
      for (let i = 0; i < parts.length; i++) {
        cur += parts[i];
        if (i < parts.length - 1) {
          cur += "</span>".repeat(stack.length);
          lines.push(cur);
          cur = stack.join("");
        }
      }
    }
  }
  lines.push(cur);
  return lines;
}

export interface CodeViewerProps {
  content: string;
  filename?: string;
  /** New-file line numbers (1-based) to highlight as changed (diff additions). */
  highlightLines?: readonly number[];
  className?: string;
}

/**
 * Read-only, offline syntax-highlighted file viewer (highlight.js + a theme-aware
 * VS Code palette) with a line-number gutter and optional changed-line highlight.
 */
export function CodeViewer({ content, filename, highlightLines, className }: CodeViewerProps) {
  const lines = React.useMemo(() => {
    const lang = langFor(filename);
    let value: string;
    try {
      value =
        lang && hljs.getLanguage(lang)
          ? hljs.highlight(content, { language: lang, ignoreIllegals: true }).value
          : hljs.highlightAuto(content).value;
    } catch {
      value = content.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
    return splitHighlightedLines(value);
  }, [content, filename]);

  const changed = React.useMemo(() => new Set(highlightLines ?? []), [highlightLines]);

  return (
    <div
      className={cn(
        "code-viewer bg-card text-foreground h-full overflow-auto font-mono text-[12.5px] leading-[1.55]",
        className,
      )}
    >
      <div className="min-w-max">
        {lines.map((lineHtml, i) => {
          const n = i + 1;
          const isChanged = changed.has(n);
          return (
            <div key={i} className={cn("flex", isChanged && "code-line-changed")}>
              <span
                aria-hidden
                className={cn(
                  "text-muted-foreground/50 border-line-soft bg-card sticky left-0 z-10 shrink-0 select-none border-r px-3 text-right tabular-nums",
                  isChanged && "code-gutter-changed",
                )}
                style={{ minWidth: "3.25rem" }}
              >
                {n}
              </span>
              <code
                className="hljs !bg-transparent !py-0 whitespace-pre pl-3 pr-6"
                dangerouslySetInnerHTML={{ __html: lineHtml || "&nbsp;" }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
