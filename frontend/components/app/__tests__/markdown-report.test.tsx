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
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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
});
