"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

export interface AdrViewerProps {
  markdown: string;
  className?: string;
}

/**
 * Read-only Markdown viewer tuned for ADRs. Uses Tailwind typography-style
 * classes against our tokens so headings / code / lists look native without
 * pulling in `@tailwindcss/typography`.
 */
export function AdrViewer({ markdown, className }: AdrViewerProps) {
  return (
    <div className={cn("bg-panel-elevated flex flex-col rounded-md border border-line-soft", className)}>
      {/* Elevated panel header */}
      <header className="flex items-center gap-2 border-b border-line-soft px-4 py-2.5">
        <span className="font-display text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          ADR
        </span>
        <span className="text-line-soft select-none">·</span>
        <span className="font-mono text-[10.5px] text-muted-foreground">
          Architecture Decision Record
        </span>
      </header>

      {/* Article body */}
      <article className="overflow-auto p-6">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <h1 className="font-display mb-3 text-2xl font-semibold tracking-tight">
                {children}
              </h1>
            ),
            h2: ({ children }) => (
              <h2 className="font-display mb-2 mt-6 text-lg font-semibold tracking-tight">
                {children}
              </h2>
            ),
            h3: ({ children }) => (
              <h3 className="font-display mb-2 mt-4 text-base font-semibold">{children}</h3>
            ),
            p: ({ children }) => <p className="mb-3 text-sm leading-relaxed">{children}</p>,
            ul: ({ children }) => (
              <ul className="mb-3 ml-5 list-disc space-y-1 text-sm">{children}</ul>
            ),
            ol: ({ children }) => (
              <ol className="mb-3 ml-5 list-decimal space-y-1 text-sm">{children}</ol>
            ),
            li: ({ children }) => <li className="leading-relaxed">{children}</li>,
            a: ({ children, href }) => (
              <a
                href={href}
                target={href?.startsWith("http") ? "_blank" : undefined}
                rel={href?.startsWith("http") ? "noreferrer noopener" : undefined}
                className="text-primary underline-offset-2 hover:underline"
              >
                {children}
              </a>
            ),
            code: ({ children, className: cls }) => {
              const inline = !cls;
              if (inline) {
                return (
                  <code className="bg-surface-2 rounded px-1 py-0.5 font-mono text-[12px]">
                    {children}
                  </code>
                );
              }
              return <code className={cls}>{children}</code>;
            },
            pre: ({ children }) => (
              <pre className="bg-surface-2 mb-3 overflow-auto rounded-md p-3 font-mono text-xs">
                {children}
              </pre>
            ),
            blockquote: ({ children }) => (
              <blockquote className="text-muted-foreground border-primary/40 mb-3 border-l-2 pl-3 text-sm italic">
                {children}
              </blockquote>
            ),
            hr: () => <hr className="border-line-soft my-4" />,
            strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
            em: ({ children }) => <em className="italic">{children}</em>,
            table: ({ children }) => (
              <div className="mb-3 overflow-x-auto">
                <table className="w-full text-sm">{children}</table>
              </div>
            ),
            th: ({ children }) => (
              <th className="border-b border-line-soft px-2 py-1 text-left font-semibold">
                {children}
              </th>
            ),
            td: ({ children }) => (
              <td className="border-b border-line-soft px-2 py-1">{children}</td>
            ),
          }}
        >
          {markdown}
        </ReactMarkdown>
      </article>
    </div>
  );
}
