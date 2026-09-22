"""The components a design can be made of — ONE list, three readers.

WHY THIS EXISTS. Asked for "the HLD", the Design agent produced the whole eight-section
document plus an executive summary, deployment plan, risks and future work. Its system
prompt mandated "ALL 8 sections, EVERY response", and its generation template
(`prompts/architecture_generation.py`) was one 435-line block that could not be asked
for in parts. The user could neither ask for one component nor learn which existed.

This module is the catalogue. Three things derive from it and therefore cannot drift:

  · the GENERATION PROMPT — `build_generation_prompt(components)` assembles the
    grounding rules, the selected components' templates (and only theirs), a diagram
    checklist limited to those components, and the quality rules;
  · the MENU the agent shows for a generic ask — `menu_text()`;
  · the roster and output-format section of the agent's system prompt.

THE HEADERS ARE LOAD-BEARING. `shared/models/design.py::_SECTION_MAP` extracts each
section by its exact `## HEADER`, and `shared/services/orchestrator/artifacts_view.py`
splits the Orchestrator's deliverables view on them. A template that emitted a
different header would produce a section the page cannot show. The tests assert every
parsed header is a catalogue header.

`overview` (executive summary + problem statement) is not a component a person asks
for on its own; it is included when the FULL document is requested and omitted
otherwise, so "just the DB schema" is just the DB schema.
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Iterable


class UnknownComponentError(ValueError):
    """A requested component the catalogue does not have. Names what does exist."""


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    #: One line, for the menu: what the reader gets.
    yields: str
    #: The exact `## HEADER` the section opens with — the one the parsers read.
    header: str
    #: The generation template. Opens with `## {header}`.
    template: str
    #: Mermaid diagrams this component must contain, for the checklist.
    diagrams: tuple[str, ...] = ()
    #: Other names people use for it.
    aliases: tuple[str, ...] = field(default_factory=tuple)


# ── the templates ────────────────────────────────────────────────────────────
#
# Split from `prompts/architecture_generation.py`'s single block. The example code in
# each is a STRUCTURE GUIDE, never content — the grounding rules say so — and every
# placeholder must be replaced from the requirements.

_OVERVIEW = """\
## OVERVIEW

### Executive Summary

| Field            | Details |
|------------------|---------|
| Project Name     | |
| Objective        | |
| Scope            | |
| Stakeholders     | |
| Success Criteria | |

### Problem Statement

Describe the core problem the system solves in 3–5 sentences.
"""

_HLD = """\
## HIGH-LEVEL DESIGN

1. **System Overview** (2–3 paragraphs) — what the system does, who uses it, and how
   the major parts fit together.
2. **High-Level Architecture Diagram [MANDATORY MERMAID]** — a `graph`/`flowchart`
   showing the major functional layers (Presentation / API / Business Logic / Data)
   and how they interact. A layered view, distinct from a C4 container diagram.

```mermaid
graph TD
    %% Example — replace with actual system content
    A["Client / Presentation Layer"] --> B["API / Gateway Layer"]
    B --> C["Business Logic Layer"]
    C --> D[("Data Layer")]
```

3. **Data Flow** — a Mermaid `sequenceDiagram` or `flowchart` for the system's main
   use case, end to end.
4. **Integration Points** — table: System | Protocol | Auth | Purpose
5. **NFR Summary** — table: Category | Requirement | Target
"""

_LLD = """\
## LOW-LEVEL DESIGN

1. **Component Specifications** — per component: responsibilities, interfaces,
   dependencies (a table or a bullet list per component).
2. **Component / Class Diagram [MANDATORY MERMAID]** — a `classDiagram` (or a
   detailed `flowchart` if the domain does not fit classes) showing the concrete
   classes/modules, their key methods/fields, and relationships.

```mermaid
classDiagram
    %% Example — replace with actual classes derived from the requirements
    class Controller {
        +handleRequest()
    }
    class Service {
        +validate()
        +process()
    }
    class Repository {
        +save()
        +findById()
    }
    Controller --> Service
    Service --> Repository
```

3. **Sequence Diagram [MANDATORY MERMAID]** — a `sequenceDiagram` for the primary
   flow (client request → business logic → database → response). One more per
   additional critical use case the requirements describe.

```mermaid
sequenceDiagram
    participant U as User
    participant API as REST API
    participant SVC as Service Layer
    participant DB as Database
    U->>API: Request
    API->>SVC: Validate and process
    SVC->>DB: Persist / query
    DB-->>SVC: Result
    SVC-->>API: Processed result
    API-->>U: Response (200/4xx)
```

4. **Error Handling Strategy** — error codes, retry policy, fallback behaviour.
"""

_C4 = """\
## C4 ARCHITECTURE DIAGRAMS

Produce ALL THREE levels as separate Mermaid `graph TB` blocks (never Mermaid's own
C4 syntax — it fails to render). Label every arrow with what is sent.

#### Level 1 — System Context [MANDATORY MERMAID]

The system, its human users, and every external system it talks to.

```mermaid
graph TB
    %% Example — replace with actual system content
    User["👤 End User"]
    SYS["[System]\\nYour System Name\\nBrief description"]
    EXT1["[External System]\\nExternal Service 1"]
    User -->|"action description"| SYS
    SYS -->|"data/call description"| EXT1
```

#### Level 2 — Container Diagram [MANDATORY MERMAID]

Inside the system boundary: every deployable container (web app, API, database,
queue, cache) and how they communicate.

```mermaid
graph TB
    subgraph "System Boundary"
        WEB["[Container: Web App]\\nTech\\nServes UI"]
        API["[Container: Backend API]\\nTech\\nBusiness logic"]
        DB[("(Container: Database)\\nTech\\nStores data")]
    end
    User["👤 User"] -->|"HTTPS"| WEB
    WEB -->|"REST"| API
    API -->|"SQL"| DB
```

#### Level 3 — Component Diagram (for the backend container) [MANDATORY MERMAID]

Inside the API container: controllers, services, repositories and how they interact.

```mermaid
graph TB
    subgraph "Backend API Container"
        CTRL["Controller\\nHandles HTTP routes"]
        SVC["Service\\nBusiness logic"]
        REPO["Repository\\nData access"]
    end
    Client["Web App"] -->|"HTTP request"| CTRL
    CTRL -->|"Calls"| SVC
    SVC -->|"Reads/writes"| REPO
    REPO -->|"SQL"| DB[("Database")]
```
"""

_API = """\
## API CONTRACT

Document every key endpoint: method, path, description, request body (JSON) and
response schema (JSON), each annotated "Implements: <feature / story from the
requirements>". Never define an endpoint for a feature the requirements do not
mention. Then the full OpenAPI 3.0 YAML in ONE fenced `yaml` block covering every
documented endpoint.

#### POST /api/v1/<resource>

**Description:** …  **Implements:** …

**Request Body:**
```json
{ "field": "value" }
```

**Response 201:**
```json
{ "id": 1, "status": "created" }
```

```yaml
openapi: 3.0.3
info:
  title: <Service> API
  version: 1.0.0
paths:
  /api/v1/<resource>:
    post:
      summary: …
      responses:
        '201':
          description: Created
```
"""

_DB = """\
## DATABASE SCHEMA

Include BOTH — the ER diagram is not replaced by the SQL.

1. **ER Diagram [MANDATORY MERMAID]** — a Mermaid `erDiagram` with every major
   entity, its key fields, and relationships with cardinality.

```mermaid
erDiagram
    %% Example — replace with actual entities derived from the requirements
    TABLE_A {
        int id PK
        string name
    }
    TABLE_B {
        int id PK
        int table_a_id FK
    }
    TABLE_A ||--o{ TABLE_B : "has"
```

2. **DDL** — full CREATE TABLE SQL for every entity: primary keys, foreign keys,
   NOT NULL constraints and indexes. SQL only, no bullet points. Tables and columns
   must use the requirements' own domain language.

```sql
-- Example: replace with actual tables derived from the requirements
CREATE TABLE example (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```
"""

_ADR = """\
## ARCHITECTURE DECISION RECORDS

One ADR per significant technology or design choice, in this format:

#### ADR-001: <Decision Title>

| Field        | Details |
|--------------|---------|
| Status       | Accepted |
| Context      | Why this decision was needed |
| Decision     | What was chosen |
| Rationale    | Why this option over the alternatives |
| Consequences | Trade-offs and implications |
| Alternatives | Other options considered, and why not |
"""

_STACK = """\
## TECHNOLOGY STACK

| Layer            | Technology | Version | Justification |
|------------------|------------|---------|---------------|
| Frontend         |            |         |               |
| Backend API      |            |         |               |
| Database         |            |         |               |
| Cache            |            |         |               |
| Message Queue    |            |         |               |
| Auth             |            |         |               |
| Storage          |            |         |               |
| CI/CD            |            |         |               |
| Cloud / Hosting  |            |         |               |
| Monitoring       |            |         |               |

Leave out a layer the system does not have rather than inventing one; tag any layer
the requirements do not specify with ⚠️ [ASSUMPTION].

### Deployment Architecture

A Mermaid `graph LR` from developer to production, then a table:
Environment | Description.
"""

_SECURITY = """\
## SECURITY DESIGN CHECKLIST

Review the designed APIs and data model against the OWASP Top 10 (2021). For EACH
category state the risk in the context of THIS design and the mitigating control:

| OWASP Category | Applicable? | Risk in this design | Mitigation control |
|----------------|-------------|---------------------|--------------------|
| A01 Broken Access Control | | | |
| A02 Cryptographic Failures | | | |
| A03 Injection | | | |
| A04 Insecure Design | | | |
| A05 Security Misconfiguration | | | |
| A06 Vulnerable & Outdated Components | | | |
| A07 Identification & Authentication Failures | | | |
| A08 Software & Data Integrity Failures | | | |
| A09 Security Logging & Monitoring Failures | | | |
| A10 Server-Side Request Forgery (SSRF) | | | |

Then a **Security Architecture** table — Concern | Approach — covering authentication,
authorisation, data in transit, data at rest, API protection, secrets, compliance.
Tag any control you ASSUME (not stated in requirements) with ⚠️ [ASSUMPTION].
"""


COMPONENTS: tuple[Component, ...] = (
    Component(
        id="overview", label="Overview",
        yields="executive summary and problem statement",
        header="OVERVIEW", template=_OVERVIEW,
        aliases=("executive summary", "problem statement", "summary"),
    ),
    Component(
        id="hld", label="High-level design",
        yields="system overview, layered architecture diagram, data flow, integration points, NFR summary",
        header="HIGH-LEVEL DESIGN", template=_HLD,
        diagrams=("high-level architecture (`graph`/`flowchart`)", "data flow (`sequenceDiagram` or `flowchart`)"),
        aliases=("hld", "high level design", "high-level design", "architecture overview", "system design"),
    ),
    Component(
        id="lld", label="Low-level design",
        yields="component specifications, class diagram, sequence diagrams, error handling",
        header="LOW-LEVEL DESIGN", template=_LLD,
        diagrams=("component/class diagram (`classDiagram`)", "sequence diagram (`sequenceDiagram`)"),
        aliases=("lld", "low level design", "low-level design", "detailed design", "class diagram", "sequence diagram"),
    ),
    Component(
        id="c4", label="C4 diagrams",
        yields="system context, container and component diagrams",
        header="C4 ARCHITECTURE DIAGRAMS", template=_C4,
        diagrams=("C4 level 1 context (`graph TB`)", "C4 level 2 container (`graph TB`)", "C4 level 3 component (`graph TB`)"),
        aliases=("c4", "c4 diagrams", "c4 architecture", "context diagram", "container diagram", "component diagram"),
    ),
    Component(
        id="api", label="API contract",
        yields="every endpoint with request and response, and the full OpenAPI 3.0 specification",
        header="API CONTRACT", template=_API,
        aliases=("api", "api contract", "api contracts", "openapi", "api spec", "api specification", "swagger", "endpoints"),
    ),
    Component(
        id="db", label="Database schema",
        yields="ER diagram and the CREATE TABLE DDL",
        header="DATABASE SCHEMA", template=_DB,
        diagrams=("ER diagram (`erDiagram`)",),
        aliases=("db", "database", "database schema", "db schema", "schema", "erd", "er diagram", "data model", "ddl"),
    ),
    Component(
        id="adr", label="Architecture decision records",
        yields="one ADR per significant decision, with context, rationale, consequences and alternatives",
        header="ARCHITECTURE DECISION RECORDS", template=_ADR,
        aliases=("adr", "adrs", "decision records", "architecture decisions", "decisions"),
    ),
    Component(
        id="stack", label="Technology stack",
        yields="technology per layer with justification, and the deployment architecture",
        header="TECHNOLOGY STACK", template=_STACK,
        diagrams=("deployment pipeline (`graph LR`)",),
        aliases=("stack", "tech stack", "technology stack", "recommended tech stack", "technologies", "deployment", "deployment architecture", "infrastructure"),
    ),
    Component(
        id="security", label="Security design",
        yields="OWASP Top 10 review of this design and the security architecture",
        header="SECURITY DESIGN CHECKLIST", template=_SECURITY,
        aliases=("security", "security design", "security checklist", "security design checklist", "owasp", "threat model"),
    ),
)

COMPONENT_IDS: tuple[str, ...] = tuple(c.id for c in COMPONENTS)
BY_ID: dict[str, Component] = {c.id: c for c in COMPONENTS}

#: What "everything" means: every component, overview included.
FULL_ALIASES = ("all", "everything", "full", "full document", "full design document",
                "complete", "complete document", "whole document", "entire document")


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().lower().replace("_", " ").replace("-", " ").split())


_LOOKUP: dict[str, str] = {}
for _c in COMPONENTS:
    _LOOKUP[_norm(_c.id)] = _c.id
    _LOOKUP[_norm(_c.label)] = _c.id
    _LOOKUP[_norm(_c.header)] = _c.id
    for _a in _c.aliases:
        _LOOKUP[_norm(_a)] = _c.id


def resolve(requested: Iterable[str]) -> list[str]:
    """Catalogue ids for what was asked, in DOCUMENT order.

    Accepts ids, labels, headers and aliases, case-insensitively; any of `FULL_ALIASES`
    means every component. Order is the catalogue's, not the request's, so a document
    always reads HLD before DB schema however the question was phrased. Refuses an
    unknown name and an empty selection, naming what exists — the agent relays that
    rather than producing something else.
    """
    wanted: set[str] = set()
    unknown: list[str] = []
    for item in requested or ():
        key = _norm(item)
        if not key:
            continue
        if key in FULL_ALIASES:
            wanted.update(COMPONENT_IDS)
            continue
        found = _LOOKUP.get(key)
        if found is None:
            unknown.append(str(item))
        else:
            wanted.add(found)
    if unknown or not wanted:
        available = ", ".join(f"{c.label} ({c.id})" for c in COMPONENTS if c.id != "overview")
        what = ", ".join(repr(u) for u in unknown) if unknown else "nothing"
        raise UnknownComponentError(
            f"I can't produce {what}. The components I can produce are: {available}; "
            "or 'all' for the full design document."
        )
    return [cid for cid in COMPONENT_IDS if cid in wanted]


def labels_for(ids: Iterable[str]) -> str:
    """"High-level design, Database schema" — for a title band or a reply."""
    return ", ".join(BY_ID[i].label for i in ids if i in BY_ID)


def roster_text() -> str:
    """The catalogue as the system prompt shows it: id, label, what it yields, and
    the diagrams it must contain. Rendered, so the prompt cannot name a component the
    tools cannot produce."""
    lines = []
    for c in COMPONENTS:
        line = f"- `{c.id}` — {c.label}: {c.yields}."
        if c.diagrams:
            line += " Diagrams: " + "; ".join(c.diagrams) + "."
        if c.id == "overview":
            line += " (Included automatically with the full document; not offered alone.)"
        lines.append(line)
    return "\n".join(lines)


def menu_text() -> str:
    """What the agent says when asked what it can produce, or asked generically."""
    lines = ["I can produce any of these design components, on their own or together:"]
    for c in COMPONENTS:
        if c.id == "overview":
            continue
        lines.append(f"- {c.label} — {c.yields}")
    lines.append(
        "- The full design document — all of the above, opening with an executive "
        "summary and problem statement"
    )
    lines.append("Tell me which you want, and I'll produce only those.")
    return "\n".join(lines)


# ── the session's document ───────────────────────────────────────────────────
#
# A design is built up over a conversation: "the HLD" now, "add the DB schema" later.
# The session keeps ONE document (`shared.last_architecture`), and these helpers are
# how a newly generated component joins it — by section, in catalogue order, replacing
# the same section when it is regenerated — so the .docx always holds everything
# produced so far and never two copies of one section.

_SECTION_RE = _re.compile(r"(?m)^##\s+([^\n]+?)\s*$")
_HEADER_TO_ID: dict[str, str] = {c.header: c.id for c in COMPONENTS}
_DECORATION_RE = _re.compile(r"\(.*?\)|\[.*?\]|[:\-–—]+\s*$")


def component_for_header(header: str) -> str | None:
    """The component a `##` header names, tolerating the older decorated forms.

    `## HIGH-LEVEL DESIGN` is the catalogue's; `## High-Level Design (HLD) [REQUIRED]`
    is what the retired single-block template produced and what older documents in
    the record still carry. Both are the HLD. Exact header first, then the header
    stripped of parentheses and brackets against every id, label and alias, then a
    substring search for a label — so "API Contracts" still finds the API contract.
    """
    if not header:
        return None
    exact = _HEADER_TO_ID.get(header.strip().upper())
    if exact:
        return exact
    bare = _norm(_DECORATION_RE.sub("", header))
    found = _LOOKUP.get(bare)
    if found:
        return found
    # Whole-word search, longest term first, so "API Contracts" finds the API contract
    # and "Security Architecture" the security review — while "capitalisation" never
    # matches the alias "api".
    terms = sorted(
        ((term, c.id) for c in COMPONENTS for term in (c.label, c.header, *c.aliases)),
        key=lambda t: -len(t[0]),
    )
    for term, cid in terms:
        if _re.search(rf"\b{_re.escape(_norm(term))}\b", bare):
            return cid
    return None


def split_sections(markdown: str) -> tuple[str, dict[str, str]]:
    """(preamble, {component id: section text incl. its header}).

    The preamble is whatever precedes the first `##` header — the `#` title. A `##`
    header the catalogue does not know is kept under its own text as the key, so an
    unexpected section is carried rather than dropped.
    """
    text = markdown or ""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return text.strip(), {}
    preamble = text[: matches[0].start()].strip()
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        header = m.group(1).strip()
        key = component_for_header(header) or header
        sections[key] = text[m.start():end].strip() + "\n"
    return preamble, sections


def sections_present(markdown: str) -> list[str]:
    """The catalogue ids the document holds, in document order."""
    _, sections = split_sections(markdown)
    return [cid for cid in COMPONENT_IDS if cid in sections]


def merge_sections(existing: str, new: str) -> str:
    """`existing` with `new`'s sections added or replaced, in catalogue order.

    The title comes from whichever document has one, `existing` first. Sections the
    catalogue does not know keep their arrival order after the known ones.
    """
    pre_a, sec_a = split_sections(existing)
    pre_b, sec_b = split_sections(new)
    merged = {**sec_a, **sec_b}
    title = pre_a or pre_b
    ordered = [merged[cid] for cid in COMPONENT_IDS if cid in merged]
    ordered += [merged[k] for k in merged if k not in _HEADER_TO_ID.values()]
    body = "\n".join(s.rstrip() + "\n" for s in ordered)
    return (title + "\n\n" + body) if title else body


# ── the generation prompt ────────────────────────────────────────────────────

_GROUNDING = """\
You are a Senior AI Solutions Architect producing part of an enterprise-grade design
document. Your output is read by engineers and by executive stakeholders.

Custom instruction / focus area: {custom_prompt}

---

## GROUNDING RULES — READ BEFORE WRITING ANYTHING

These rules prevent hallucination. The document must reflect the actual system
described in the requirements text — not a generic example.

- Replace EVERY placeholder (table name, endpoint, entity, user role, system name)
  with content derived directly from the provided requirements.
- If a detail is not specified in the requirements, mark it explicitly:
  > ⚠️ **[ASSUMPTION]** — not specified in requirements. Confirm before development.
- Never copy or adapt the example code blocks in the templates below as real output.
  They are structure guides only. All content must come from the requirements.
- Endpoint paths, table names and column names must use the domain language of the
  requirements (if they say "leave request", use `leave_request`, not `claim`).

---

## WHAT TO PRODUCE — AND NOTHING ELSE

Produce ONLY the sections below, each opening with its exact `##` header, in this
order: {headers}. Do not add any other section — no executive summary, deployment
plan, risks or future work unless one of those is a section below. Do not rename,
merge or drop a header: the platform splits the document on these exact headers.

Every diagram MUST be a valid Mermaid code block (```mermaid … ```).
{checklist}
---
"""

_QUALITY = """\
---

QUALITY RULES:
- Every Mermaid block MUST be syntactically valid — every node label in double quotes,
  one statement per line, no C4-specific syntax (use `graph TB`), under ~25 nodes.
- Tables must be fully populated — no empty cells.
- Everything must reflect the actual system in the requirements, never a generic
  example; tag every assumption with ⚠️ [ASSUMPTION].
- Use the exact `##` headers listed above and no others.
"""


def build_generation_prompt(requested: Iterable[str], custom_prompt: str = "", tech_stack: str = "") -> str:
    """The generation prompt for exactly these components. Raises on an unknown one.

    `tech_stack` is the project's mandatory tech-stack block (`tech_stack.render_for_prompt`),
    placed after the grounding and before the templates; '' leaves the prompt as it was."""
    ids = resolve(requested)
    selected = [BY_ID[i] for i in ids]
    headers = ", ".join(f"`## {c.header}`" for c in selected)
    diagrams = [d for c in selected for d in c.diagrams]
    if diagrams:
        checklist = (
            "\nMANDATORY DIAGRAM CHECKLIST — each of these must appear, as a real Mermaid "
            "block populated from the requirements (never the literal example):\n"
            + "".join(f"  {n}. {d}\n" for n, d in enumerate(diagrams, 1))
        )
    else:
        checklist = "\n"
    head = _GROUNDING.format(
        custom_prompt=custom_prompt or "", headers=headers, checklist=checklist,
    )
    if tech_stack:
        head = head + "\n" + tech_stack + "\n"
    body = "\n---\n\n".join(c.template.rstrip() + "\n" for c in selected)
    return head + "\n" + body + "\n" + _QUALITY


def existing_sections_note(document: str, requested_ids: Iterable[str]) -> str:
    """What the model is told about sections the document ALREADY holds.

    A DB schema written blind to the HLD names different entities than the HLD does.
    The sections already produced go to the model as context — to stay consistent
    with, not to rewrite: the prompt still says to produce only the requested ones.
    """
    _, sections = split_sections(document)
    requested = set(requested_ids)
    kept = [sections[cid] for cid in COMPONENT_IDS if cid in sections and cid not in requested]
    if not kept:
        return ""
    return (
        "\n--- SECTIONS THIS DESIGN ALREADY HAS (for consistency; do NOT reproduce them, "
        "produce only the requested sections) ---\n" + "\n".join(kept)
    )
