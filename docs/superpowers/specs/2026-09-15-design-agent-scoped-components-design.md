# Design agent: produce what is asked, in the brief's visual language

**Date:** 2026-09-15 · **Status:** approved in chat, implementing

## Problem

Asked for "the HLD", the Design agent produced the whole eight-section architecture
document (HLD, LLD, C4, API contract, DB schema, ADRs, tech stack, security checklist)
plus an executive summary, problem statement, deployment, risks and future work. Its
system prompt mandates "ALL 8 sections, EVERY response", and its generation template
(`prompts/architecture_generation.py`) is one 435-line block. The user cannot ask for a
single component, and does not learn what the agent can produce.

The Word file it saves is the generic markdown→docx output: default headings, plain
"Table Grid" tables, no title, no identity. The Track 3 migration-intent brief, by
contrast, is a designed document (`requirements_modernization_agent/brief_document.py`):
white→orange title band, key-facts strip, cards, tinted header rows, one PwC palette.

## Decisions (from the user)

- A generic ask ("create the architecture") → the agent lists what it can produce and
  asks which, with "the full design document" as one choice. A specific ask produces
  only what was named. Later asks add components to the same document.
- Word gets the brief's look now; PDF is converted from that Word file (Word COM via
  `docx2pdf`), falling back to the existing plain PDF when Word is unavailable.

## Design

### A. One catalogue, three readers

`design_architecture_agent/components.py` — the components, in document order:

| id | label | what you get |
|---|---|---|
| `overview` | Overview | executive summary, problem statement |
| `hld` | High-level design | system overview, layered architecture diagram, data flow, integrations, NFRs |
| `lld` | Low-level design | component specs, class diagram, sequence diagrams, error handling |
| `c4` | C4 diagrams | context, container, component |
| `api` | API contract | endpoints with request/response, full OpenAPI 3.0 |
| `db` | Database schema | ER diagram, DDL |
| `adr` | Architecture decision records | one ADR per decision |
| `stack` | Technology stack | layer → technology → justification; deployment |
| `security` | Security design | OWASP Top 10 review of this design |

Each entry carries its `## EXACT HEADER` (the one `shared/models/design.py` and the
Orchestrator's deliverables view already parse), its generation template, and the
diagrams it requires. `build_generation_prompt(components, custom_prompt)` assembles
grounding rules + the selected templates + a diagram checklist limited to those
components + quality rules. Readers: the generation tools, the `list_design_components`
tool, and the roster in the system prompt (rendered, not hand-typed). `overview` is
included automatically only for the full document.

### B. Agent behaviour

- `list_design_components()` returns the catalogue as text; the prompt tells the agent
  to answer a generic ask with it and a question.
- `generate_architecture(document_text, components=[...], custom_prompt)` and
  `generate_architecture_from_context(context, components=[...], user_requirements)`
  generate only those components. Unknown ids are refused by name.
- A second call in the same session with new components MERGES: the session's document
  (`shared.last_architecture`) is split on section headers, the new sections are added,
  and the whole is re-rendered in catalogue order. Regenerating an existing component
  replaces that section only.
- `update_response` preserves the sections that exist, not "all 8".
- Absolute rules become conditional: "when HLD is produced it MUST include…".
- The system prompt's roster of components and the output-format section are rendered
  from the catalogue.

### C. The document

- `shared/docs/pwc_style.py`: the palette (moved from `brief_document.py`, which imports
  it back — no visual change there) and `WordCanvas`, the reusable Word primitives
  (page setup, shading, padding, borders, fixed tables, runs, paragraphs, pills, title
  band, facts strip, tinted header row, numbered section heading).
- `design_architecture_agent/design_document.py`: `render_design_docx(markdown, path,
  *, meta, render_mermaid, fetch_image)`. Walks the markdown (headings, paragraphs,
  bullets, tables, fenced code, mermaid, blockquotes) and paints it with the canvas:
  title band (eyebrow "DESIGN · <components>", title, project, date), key-facts strip
  (project, source, components, generated), a numbered section per component under an
  orange rule, tables with white→orange header rows, code in grey monospace panels,
  diagrams as images with captions, `[ASSUMPTION]` callouts with an amber dot, ADR
  blocks as cards with an orange edge, risk tables with a red edge.
- Palette rules (from the brief): grey = context, orange = the design, red = risk,
  green only "fine as is"; orange text only as `ACCENT`/`ACCENT_DEEP` (≥4.5:1);
  brand orange only as fills and rules.
- `_markdown_to_docx` in `architecture.py` calls the themed renderer with the session's
  meta; `save_architecture_pdf` converts the themed .docx via `docx2pdf` when available.

### D. Tests

- Catalogue: ids, order, headers match the parser's; prompt assembly includes only the
  selected templates and their diagrams; refuses unknown ids.
- Tools: `generate_*` with components → prompt contains only those headers; merge keeps
  existing sections and orders by catalogue; `list_design_components` text; the system
  prompt names every component and no longer says "ALL 8".
- Renderer: title band text, facts, only the requested sections, header-row shading is
  a palette tint, code panel present, diagram falls back to code when rendering fails,
  assumption callout, filename escape. A rendered sample is rasterized and inspected.
- Existing design-agent suites keep passing.

## Out of scope

The on-page tabbed viewer, PPT export, and the Requirements/PM documents' styling.
