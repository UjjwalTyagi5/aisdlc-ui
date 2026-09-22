// @vitest-environment jsdom
/**
 * The Requirements agent's BRD as a report, not a download link.
 *
 * The parser mirrors the backend's `requirements_document.py`: the kind is read off
 * the section headers (a risk register is one table, so its columns count), the
 * prompt's numbering is dropped, a `# Title` wins the band, and each `##` becomes a
 * numbered section — the same rules the Word file follows, so page and file agree.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

// The real renderer lazy-loads mermaid, which jsdom cannot lay out; what matters here is
// that a diagram reaches it, with its source.
vi.mock("@/components/app/mermaid-renderer", () => ({
  MermaidRenderer: ({ source }: { source: string }) => <figure data-testid="mermaid">{source}</figure>,
}));

import { MarkdownReport, normaliseHeaders, parseDocument } from "@/components/app/markdown-report";

afterEach(cleanup);

const BRD = `# QuickLink — Business Requirements

## Executive Summary
QuickLink shortens URLs for the marketing team.

## Project Objectives
- Cut link length by 80%

## Requirements
| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Shorten a URL | High |
| FR-02 | Count clicks | Medium |

## Key Stakeholders
Information not found in the source documents for a legal reviewer.
`;

describe("parseDocument", () => {
  it("reads the kind off the headers and keeps the H1 as the title", () => {
    const doc = parseDocument(BRD, "anything.docx");
    expect(doc.kind.id).toBe("brd");
    expect(doc.title).toBe("QuickLink — Business Requirements");
    expect(doc.sections.map((s) => s.title)).toEqual(["Executive Summary", "Project Objectives", "Requirements", "Key Stakeholders"]);
  });

  it("names a risk register by its table columns", () => {
    const doc = parseDocument("| Risk ID | Risk Description | Likelihood | Mitigation Strategy / Action |\n|---|---|---|---|\n| R1 | Vendor delay | Medium | Second vendor |");
    expect(doc.kind.id).toBe("risk");
  });

  it("recognises a PRD by its sections and by its file name", () => {
    expect(parseDocument("## Product Overview\nx\n## Target Users\ny\n## Features\nz", "a.docx").kind.id).toBe("prd");
    expect(parseDocument("## Notes\nhello", "QuickLink_PRD.docx").kind.id).toBe("prd");
    expect(parseDocument("## Notes\nhello", "QuickLink_BRD.docx").kind.id).toBe("brd");
  });

  it("falls back to the file name, then to the generic kind", () => {
    expect(parseDocument("## Notes\nhello", "QuickLink_BRD.docx").kind.id).toBe("brd");
    expect(parseDocument("## Notes\nhello", "output.docx").kind.id).toBe("document");
  });

  it("does not call one 'Requirements' header a BRD", () => {
    expect(parseDocument("## Requirements\n- one", "notes.docx").kind.id).toBe("document");
  });
});

describe("normaliseHeaders", () => {
  it("drops the prompt's numbering and makes a dotted number a sub-heading", () => {
    expect(normaliseHeaders("##1. Executive Summary\n## 1.1 Introduction\n## 3.6 Risks (Table)\n##Glossary").split("\n")).toEqual([
      "## Executive Summary", "### Introduction", "### Risks (Table)", "## Glossary",
    ]);
  });
});

describe("MarkdownReport", () => {
  it("renders the band, numbered sections, pills for priorities and a callout for what was not found", () => {
    render(<MarkdownReport markdown={BRD} filename="QuickLink_BRD.docx" project="QuickLink" status="Draft" />);
    expect(screen.getByText("Business requirements document")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "QuickLink — Business Requirements" })).toBeInTheDocument();
    expect(screen.getByText("01")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Key Stakeholders" })).toBeInTheDocument();
    expect(screen.getByText("High")).toHaveClass("rounded-md");
    expect(screen.getByText(/Information not found in the source documents/)).toBeInTheDocument();
    // the lead sentence is the band's subtitle AND the first section's body
    expect(screen.getAllByText("QuickLink shortens URLs for the marketing team.", { selector: "p" })).toHaveLength(2);
  });

  it("titles an untitled document by project and kind", () => {
    render(<MarkdownReport markdown={"## Executive Summary\nx\n## Project Scope\ny"} filename="brd_v3.docx" project="ClaimTrack" />);
    expect(screen.getByRole("heading", { level: 2, name: "ClaimTrack — Business requirements" })).toBeInTheDocument();
  });

  it("uses the caller's name for the document when it gives one", () => {
    render(<MarkdownReport markdown={"## Summary\nReady."} project="QuickLink" kind={{ eyebrow: "Code review document", label: "Code review document" }} />);
    expect(screen.getByRole("heading", { level: 2, name: "QuickLink — Code review document" })).toBeInTheDocument();
    expect(screen.queryByText("Requirements document")).not.toBeInTheDocument();
  });
});

// LIVE (2026-09-21): a Design document opened on the page showed its C4, sequence and ER
// diagrams as mermaid source, while the Word file showed them as figures.
describe("MarkdownReport — diagrams and figures", () => {
  const DESIGN = [
    "## C4 Architecture Diagrams",
    "Level 1 — System Context",
    "",
    "```mermaid",
    "graph TD",
    "  A[\"CMS\"] --> B[\"API\"]",
    "```",
    "",
    "## Data Model",
    "```sql",
    "CREATE TABLE links (id int);",
    "```",
  ].join("\n");

  it("draws a mermaid block as a diagram, and leaves other code as code", () => {
    render(<MarkdownReport markdown={DESIGN} filename="architecture.docx" />);
    const diagram = screen.getByTestId("mermaid");
    expect(diagram).toHaveTextContent('graph TD A["CMS"] --> B["API"]');
    expect(screen.getByText("CREATE TABLE links (id int);").tagName).toBe("CODE");
  });

  it("shows a figure carried inside the document, and nothing else as a data URL", () => {
    const png = "data:image/png;base64,iVBORw0KGgo=";
    render(<MarkdownReport markdown={`## Context\n![Figure](${png})\n\n[click](data:text/html;base64,PHNjcmlwdD4=)`} />);
    expect(screen.getByRole("img", { name: "Figure" })).toHaveAttribute("src", png);
    expect(screen.getByText("click").getAttribute("href") ?? "").not.toContain("data:");
  });
});
