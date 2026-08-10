"use client";

import * as React from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy } from "lucide-react";

// Token colors for fenced code. Dark-surfaced blocks (ChatGPT/Claude style) read
// well in both app themes, so the dark hljs theme is applied unconditionally and
// scoped to `.hljs` inside our code blocks.
import "highlight.js/styles/github-dark.css";

import { cn } from "@/lib/utils";
import { MermaidRenderer } from "@/components/app/mermaid-renderer";
import { DiagramImage } from "@/components/app/diagram-image";

/** Flatten arbitrary React children (incl. highlight spans) to a raw string for copy. */
function flattenText(node: React.ReactNode): string {
  if (node === null || node === undefined || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (React.isValidElement(node)) {
    return flattenText((node.props as { children?: React.ReactNode }).children);
  }
  return "";
}

function CodeBlock({
  language,
  raw,
  children,
}: {
  language: string;
  raw: string;
  children: React.ReactNode;
}) {
  const [copied, setCopied] = React.useState(false);
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const copy = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      // clipboard blocked (insecure context / permissions) — leave state untouched
    }
  }, [raw]);

  return (
    <div className="group/code my-3 overflow-hidden rounded-lg border border-white/10 bg-[oklch(0.21_0.02_265)] text-[13px] shadow-sm">
      <div className="flex items-center justify-between border-b border-white/10 bg-white/[0.03] px-3 py-1.5">
        <span className="font-mono text-[10.5px] font-medium uppercase tracking-wider text-white/55">
          {language || "code"}
        </span>
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? "Copied" : "Copy code"}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-white/60 transition-colors hover:bg-white/10 hover:text-white/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/40"
        >
          {copied ? (
            <>
              <Check className="size-3" aria-hidden /> Copied
            </>
          ) : (
            <>
              <Copy className="size-3" aria-hidden /> Copy
            </>
          )}
        </button>
      </div>
      <pre className="max-h-[420px] overflow-auto p-3.5 leading-relaxed">
        <code className={cn("hljs bg-transparent p-0 font-mono", `language-${language}`)}>
          {children}
        </code>
      </pre>
    </div>
  );
}

const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mb-2 mt-4 text-base font-semibold tracking-tight first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-4 text-[15px] font-semibold tracking-tight first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-3 text-sm font-semibold first:mt-0">{children}</h3>
  ),
  // A lone image becomes a block DiagramImage (a <div>); unwrap its paragraph so we
  // don't nest a <div> inside <p> (invalid HTML → hydration error).
  p: ({ children, node }) => {
    const kids = (node as { children?: { type?: string; tagName?: string }[] } | undefined)?.children;
    if (kids?.length === 1 && kids[0]?.type === "element" && kids[0]?.tagName === "img") {
      return <>{children}</>;
    }
    return <p className="mb-2 leading-relaxed last:mb-0">{children}</p>;
  },
  ul: ({ children }) => (
    <ul className="mb-2 ml-4 list-disc space-y-1 last:mb-0 marker:text-muted-foreground">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-2 ml-4 list-decimal space-y-1 last:mb-0 marker:text-muted-foreground">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-primary font-medium underline-offset-2 hover:underline"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  blockquote: ({ children }) => (
    <blockquote className="border-border text-muted-foreground my-2 border-l-2 pl-3 italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-border my-3" />,
  // Images — the Kroki C4/PlantUML/ERD diagrams embedded in design docs — get the
  // rich zoom/fullscreen/download container so they match inline Mermaid diagrams.
  img: ({ src, alt }) => (
    <DiagramImage
      src={typeof src === "string" ? src : undefined}
      alt={typeof alt === "string" ? alt : undefined}
      className="my-3"
      height={360}
    />
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-muted/60">{children}</thead>,
  th: ({ children }) => (
    <th className="border-b px-2.5 py-1.5 text-left font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border-b px-2.5 py-1.5 align-top">{children}</td>,
  // Inline code only — fenced blocks come through `pre`.
  code: ({ className, children }) => (
    <code
      className={cn(
        "bg-muted rounded px-1 py-0.5 font-mono text-[0.85em] before:content-[''] after:content-['']",
        className,
      )}
    >
      {children}
    </code>
  ),
  // Fenced code: extract language + raw text from the wrapped <code>, then render
  // the dark code block (or a Mermaid diagram for ```mermaid fences).
  pre: ({ children }) => {
    const codeEl = React.Children.toArray(children).find(
      (c): c is React.ReactElement<{ className?: string; children?: React.ReactNode }> =>
        React.isValidElement(c),
    );
    const codeClass = (codeEl?.props.className as string) || "";
    const language = /language-(\w+)/.exec(codeClass)?.[1] ?? "";
    const raw = flattenText(codeEl?.props.children).replace(/\n$/, "");

    if (language === "mermaid") {
      return <MermaidRenderer source={raw} className="my-3" height={360} showToolbar />;
    }
    return (
      <CodeBlock language={language} raw={raw}>
        {codeEl?.props.children}
      </CodeBlock>
    );
  },
};

export interface MarkdownMessageProps {
  content: string;
  className?: string;
}

/**
 * Renders agent chat content as rich markdown: GitHub-flavored tables/lists,
 * syntax-highlighted + copyable code blocks, inline Mermaid diagrams, and safe
 * links. Mirrors the design-system token usage of the artifact viewers.
 */
export function MarkdownMessage({ content, className }: MarkdownMessageProps) {
  return (
    <div className={cn("min-w-0 max-w-full text-sm leading-relaxed [word-break:break-word]", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={COMPONENTS}
        // Allow inline data:image diagrams (Kroki can return them); the default
        // transform strips data: URIs. All other URLs keep the default sanitizer.
        urlTransform={(url) =>
          url.startsWith("data:image/") ? url : defaultUrlTransform(url)
        }
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
