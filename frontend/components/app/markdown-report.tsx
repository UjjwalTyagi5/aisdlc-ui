"use client";

import * as React from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Boxes, CalendarDays, FileText, Layers } from "lucide-react";

import { cn } from "@/lib/utils";
import { MermaidRenderer } from "@/components/app/mermaid-renderer";
import {
  Callout,
  type Fact,
  FactStrip,
  Pill,
  ReportHero,
  ReportSection,
  toneForWord,
} from "@/components/app/report-primitives";

/**
 * A markdown document — the Requirements agent's BRD, PDD or risk register — as
 * a designed report: hero band, key facts, each `##` a numbered section, tables
 * with a tinted header and status words as pills, "not found" lines as callouts.
 *
 * The same document goes to Word through `shared/docs/markdown_docx.py`; the two
 * read as one family because they follow the same rules: the title lives in the
 * band, `##` numbers itself, a priority column colours its words.
 */

export interface DocumentKind {
  id: "prd" | "brd" | "pdd" | "risk" | "document";
  eyebrow: string;
  label: string;
}

const KINDS: { kind: DocumentKind; markers: string[] }[] = [
  {
    kind: { id: "prd", eyebrow: "Product requirements document", label: "Product requirements" },
    markers: ["product overview", "problem statement", "target users", "goals and success metrics",
      "features", "functional requirements", "non-functional requirements", "user journeys", "release plan"],
  },
  {
    kind: { id: "brd", eyebrow: "Business requirements document", label: "Business requirements" },
    markers: ["executive summary", "project objectives", "needs statement", "project scope", "requirements",
      "project constraints", "key stakeholders", "schedule", "glossary"],
  },
  {
    kind: { id: "pdd", eyebrow: "Process definition document", label: "Process definition" },
    markers: ["solution design details", "scope of requirement", "process functional description", "data flow",
      "success criteria", "in-scope", "out of scope", "assumptions", "dependencies", "intended audience",
      "process overview", "as-is process", "to-be process"],
  },
  {
    kind: { id: "risk", eyebrow: "Risk register", label: "Risk register" },
    markers: ["risk register", "risk id", "risk description", "likelihood", "risk level", "mitigation", "owner"],
  },
];
const GENERIC: DocumentKind = { id: "document", eyebrow: "Requirements document", label: "Requirements document" };

export interface ParsedDocument {
  title: string | null;
  preamble: string;
  sections: { title: string; body: string }[];
  kind: DocumentKind;
}

const NUMBERED = /^(#{1,6})\s*(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$/;

/** Mirrors `requirements_document.normalise_headers`: drop the prompt's own numbering
 *  ("##1. Executive Summary"), make a dotted sub-number a sub-heading. */
export function normaliseHeaders(markdown: string): string {
  return markdown
    .split(/\r?\n/)
    .map((line) => {
      const m = NUMBERED.exec(line);
      if (!m) return line.replace(/^(#{1,6})(?=[^#\s])/, "$1 ");
      const [, hashes, number, title] = m;
      const h = hashes!.length === 2 && number!.replace(/\.$/, "").includes(".") ? "###" : hashes!;
      return `${h} ${title}`;
    })
    .join("\n");
}

export function parseDocument(raw: string, filename = ""): ParsedDocument {
  const markdown = normaliseHeaders(raw ?? "").trim();
  const lines = markdown.split("\n");
  let title: string | null = null;
  const sections: { title: string; body: string }[] = [];
  const preamble: string[] = [];
  let current: { title: string; body: string[] } | null = null;
  let inFence = false;
  for (const line of lines) {
    if (/^```/.test(line.trim())) inFence = !inFence;
    if (!inFence) {
      const h1 = /^#\s+(.+?)\s*#*\s*$/.exec(line);
      if (h1 && !title && !current) { title = h1[1]!.trim(); continue; }
      const h2 = /^##\s+(.+?)\s*#*\s*$/.exec(line);
      if (h2) {
        if (current) sections.push({ title: current.title, body: current.body.join("\n").trim() });
        current = { title: h2[1]!.trim(), body: [] };
        continue;
      }
    }
    (current ? current.body : preamble).push(line);
  }
  if (current) sections.push({ title: current.title, body: current.body.join("\n").trim() });

  const headers = sections.map((s) => s.title.toLowerCase());
  for (const t of markdown.split("\n")) {
    // a table's column headers name a document that is one table — the risk register
    if (t.trim().startsWith("|")) { headers.push(...t.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim().toLowerCase())); break; }
  }
  let kind = GENERIC;
  let best = 1;
  for (const k of KINDS) {
    const hits = k.markers.filter((m) => headers.some((h) => h.startsWith(m))).length;
    if (hits > best) { kind = k.kind; best = hits; }
  }
  if (kind === GENERIC) {
    // By id, not position (mirrors requirements_document.document_kind).
    const byId = (id: DocumentKind["id"]) => KINDS.find((k) => k.kind.id === id)!.kind;
    const name = filename.toLowerCase();
    if (name.includes("prd")) kind = byId("prd");
    else if (name.includes("brd") || name.includes("requirement")) kind = byId("brd");
    else if (name.includes("pdd") || name.includes("process")) kind = byId("pdd");
    else if (name.includes("risk")) kind = byId("risk");
  }
  return { title, preamble: preamble.join("\n").trim(), sections, kind };
}

/** The first sentence or two of the first section — what the document is about. */
function leadOf(doc: ParsedDocument): string | undefined {
  const src = doc.preamble || doc.sections[0]?.body || "";
  const para = src.split(/\n\s*\n/).map((p) => p.trim()).find((p) => p && !p.startsWith("|") && !p.startsWith("#") && !p.startsWith("-") && !p.startsWith("*"));
  if (!para) return undefined;
  const text = para.replace(/[*_`]/g, "");
  return text.length > 260 ? `${text.slice(0, 257).replace(/\s+\S*$/, "")}…` : text;
}

function titleFromFilename(filename: string): string {
  return filename.replace(/\.[a-z0-9]+$/i, "").replace(/[_-]+/g, " ").trim();
}

/* ── the markdown pieces ────────────────────────────────────────────────── */

const NOT_FOUND = /information (is )?not (found|available)|not (found|specified|provided) in the (source|provided|input)/i;

function text(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (React.isValidElement(node)) return text((node.props as { children?: React.ReactNode }).children);
  return "";
}

const components: Components = {
  h1: ({ children }) => <h3 className="font-display mt-4 text-base font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="font-display mt-4 text-base font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-4 text-[13.5px] font-semibold">{children}</h4>,
  h4: ({ children }) => <h5 className="mt-3 text-[11px] font-semibold tracking-wide text-orange-900 uppercase dark:text-orange-200">{children}</h5>,
  h5: ({ children }) => <h5 className="mt-3 text-[11px] font-semibold tracking-wide uppercase">{children}</h5>,
  h6: ({ children }) => <h6 className="mt-3 text-[11px] font-semibold tracking-wide uppercase">{children}</h6>,
  p: ({ children }) => {
    const t = text(children);
    if (NOT_FOUND.test(t)) return <Callout tone="neutral" className="my-2 text-[13px]">{children}</Callout>;
    return <p className="text-foreground/85 my-2 text-[14px] leading-relaxed">{children}</p>;
  },
  ul: ({ children }) => <ul className="my-2 space-y-1 pl-5 text-[14px] leading-relaxed [&>li]:list-disc [&>li]:marker:text-primary">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 space-y-1 pl-5 text-[14px] leading-relaxed [&>li]:list-decimal [&>li]:marker:font-semibold [&>li]:marker:text-primary">{children}</ol>,
  li: ({ children }) => {
    const t = text(children);
    if (NOT_FOUND.test(t)) return <li className="text-muted-foreground italic marker:text-muted-foreground/50">{children}</li>;
    return <li>{children}</li>;
  },
  blockquote: ({ children }) => <Callout tone="neutral" className="my-3">{children}</Callout>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">{children}</a>
  ),
  hr: () => <hr className="my-4" />,
  code: ({ className, children }) => {
    const block = /language-/.test(className ?? "") || text(children).includes("\n");
    return block ? (
      <code className={cn("block overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-[12px] leading-relaxed", className)}>{children}</code>
    ) : (
      <code className="bg-muted rounded px-1 py-0.5 font-mono text-[12px]">{children}</code>
    );
  },
  // A ```mermaid block IS A DIAGRAM. The Word file draws it as a figure
  // (`markdown_docx.mermaid`), the chat draws it with the same renderer as here, and this
  // viewer showed its source — so a design opened on the page had code where its C4,
  // sequence and ER diagrams belonged. A diagram that will not parse keeps its source.
  pre: ({ children }) => {
    const codeEl = React.Children.toArray(children).find(
      (c): c is React.ReactElement<{ className?: string; children?: React.ReactNode }> => React.isValidElement(c),
    );
    if (codeEl && /language-mermaid\b/.test(codeEl.props.className ?? "")) {
      return <MermaidRenderer source={text(codeEl.props.children).replace(/\n$/, "")} className="my-4" height={460} />;
    }
    return <pre className="my-3">{children}</pre>;
  },
  // A figure carried inside the document itself — a Word file's pictures, read back as
  // data URLs by `docx_preview` when the document has no page copy.
  img: ({ src, alt }) => (
    // eslint-disable-next-line @next/next/no-img-element -- an embedded data: URL; next/image adds nothing here
    <img src={typeof src === "string" ? src : undefined} alt={alt || "Figure"} className="my-4 max-w-full rounded-lg border bg-white" />
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-xl border">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="from-primary/5 to-primary/15 bg-gradient-to-r text-[10.5px] tracking-wide text-orange-900 uppercase dark:text-orange-200">
      {children}
    </thead>
  ),
  tbody: ({ children }) => <tbody className="[&>tr]:border-t [&>tr:hover]:bg-muted/40">{children}</tbody>,
  th: ({ children }) => <th className="px-3 py-2 text-left font-semibold">{children}</th>,
  td: ({ children }) => {
    const t = text(children).trim();
    const tone = t.length <= 14 ? toneForWord(t) : null;
    if (NOT_FOUND.test(t)) return <td className="text-muted-foreground px-3 py-2 align-top italic">{children}</td>;
    return <td className="px-3 py-2 align-top">{tone ? <Pill tone={tone}>{t}</Pill> : children}</td>;
  },
};

/** A picture inside the document. Only raster images as base64 — react-markdown's default
 *  drops every `data:` URL, which is right for links and wrong for a document's figures. */
const FIGURE_DATA_URL = /^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=]+$/i;
const urlTransform = (url: string) => (FIGURE_DATA_URL.test(url) ? url : defaultUrlTransform(url));

export function MarkdownBody({ markdown, className }: { markdown: string; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} urlTransform={urlTransform}>{markdown}</ReactMarkdown>
    </div>
  );
}

/* ── the report ─────────────────────────────────────────────────────────── */

export function MarkdownReport({
  markdown,
  filename = "",
  project,
  generatedAt,
  status,
  actions,
  facts: extraFacts = [],
  kind: kindOverride,
  className,
}: {
  markdown: string;
  /** The Word file's name — the title's last resort, and a hint to the kind. */
  filename?: string;
  project?: string;
  /** ISO timestamp or a preformatted date. */
  generatedAt?: string | null;
  /** "Draft", "Awaiting approval", "Approved". */
  status?: string;
  /** For the band's corner: Download Word, Raise for approval. */
  actions?: React.ReactNode;
  facts?: Fact[];
  /** What the document is, when the caller knows better than its headings — an agent page
   *  names its own documents (a code review report is not a "Requirements document"). Left
   *  out, the kind is read from the headings, as the Requirements pages always have. */
  kind?: Pick<DocumentKind, "eyebrow" | "label">;
  className?: string;
}) {
  const doc = React.useMemo(() => parseDocument(markdown, filename), [markdown, filename]);
  const kind = kindOverride ?? doc.kind;
  const title = doc.title ?? (project ? `${project} — ${kind.label}` : titleFromFilename(filename) || kind.label);
  const when = generatedAt
    ? (Number.isNaN(Date.parse(generatedAt)) ? generatedAt : new Date(generatedAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }))
    : undefined;
  const facts: Fact[] = [
    { label: "Project", value: project ?? "", icon: Boxes },
    { label: "Sections", value: doc.sections.length || "", icon: Layers },
    { label: "Generated", value: when ?? "", icon: CalendarDays },
    { label: "File", value: filename, icon: FileText },
    ...extraFacts,
  ];

  return (
    <article className={cn("mx-auto max-w-5xl space-y-8 p-4 md:p-6", className)}>
      <header className="space-y-3">
        <ReportHero
          eyebrow={kind.eyebrow}
          eyebrowTail={project}
          title={title}
          subtitle={leadOf(doc)}
          meta={[status ?? "Draft", doc.sections.length ? doc.sections.slice(0, 4).map((s) => s.title).join(" · ") + (doc.sections.length > 4 ? " …" : "") : ""].filter(Boolean).join("  ·  ")}
          aside={actions}
        />
        <FactStrip facts={facts} className="lg:grid-cols-4" />
      </header>

      {doc.preamble && <MarkdownBody markdown={doc.preamble} />}

      {doc.sections.map((s, i) => (
        <ReportSection key={`${s.title}-${i}`} n={i + 1} title={s.title} id={`doc-section-${i + 1}`}>
          {s.body ? <MarkdownBody markdown={s.body} /> : <Callout tone="neutral">Nothing recorded under this heading.</Callout>}
        </ReportSection>
      ))}

      {doc.sections.length === 0 && !doc.preamble && (
        <Callout tone="neutral">This document is empty.</Callout>
      )}
    </article>
  );
}
