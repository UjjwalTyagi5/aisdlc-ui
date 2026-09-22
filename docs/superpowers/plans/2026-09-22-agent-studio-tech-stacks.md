# Agent Studio Tech Stacks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- Business Unit admins offer tech stacks, with one as the default.
- Project admins pick exactly one.
- The Design agent is held to the project's stack when it generates, verified by a deterministic check.

**Architecture:**
- **Two new tables:** `tech_stacks` and `project_tech_stack_selections`.
- **Code layers:**
  - a pure rules module (`tech_stack.py`)
  - a DB store with a TTL-cached resolver (`tech_stack_store.py`)
  - a FastAPI router reusing Agent Studio's tier ownership (`resolve_actor_tier_access`)
- **Design agent:**
  - The generation tool puts the stack into its own prompt, checks the Technology Stack table, corrects once and flags leftovers.
  - The chat node adds the stack to the system message on every turn.
- **Frontend:**
  - a "Tech stack · all agents" panel in Agent Studio's Skills tab
  - a chip on the Design page

**Tech Stack:**
- Backend: FastAPI, SQLAlchemy async, Alembic, Postgres RLS, LangChain/LiteLLM.
- Frontend: Next.js App Router, TanStack Query, zod, shadcn/ui, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-22-agent-studio-tech-stacks-design.md`

## Global Constraints

- **Tiers:**
  - Only `workspace` (Business Unit) and `project` tiers; no org tier.
  - Ownership is `resolve_actor_tier_access(...).owns` exactly. **No new permissions or role rows** (RBAC catalogue drift).
- **Categories** (ids, in order): `languages`, `backend_frameworks`, `frontend_frameworks`, `databases`, `cloud_hosting`, `messaging`, `devops`, `testing`, `observability`.
- **Limits:**
  - name 3–80, unique among live stacks of the same tier (case-insensitive)
  - description ≤ 280; notes ≤ 2,000
  - ≤ 20 items per category, each ≤ 60 characters, trimmed and de-duplicated
  - at least one item
- **Effective stack:** the project's selection (live, and the project's own or its current BU's), else the BU default, else none. A skipped selection yields a warning.
- **Violations shape:** `{field, code, message}` in `HTTPException(422, detail={"violations": [...]})`, the same shape Agent Studio renders.
- **The not-covered marker:** `Not covered by the project's tech stack — needs a decision`.
- **Terminology:**
  - API field names are snake_case, matching `agent-skills`.
  - UI copy says "Business Unit", never "workspace".
- **Change discipline:**
  - Lint every changed frontend file.
  - Backend edits reload nothing (the backend runs without `--reload`): restart it once, after the migration.
- **Latency baseline** (unchanged code, grok-3-mini, 6 runs): Technology stack mean 11.9 s / median 11.6 s; HLD mean 11.5 s / median 11.2 s. The prompt is 9.2k / 9.1k chars. Data: `bench_results.json` in the session scratchpad.

---

### Task 0: Branch

- [ ] **Step 1:** Create the branch from the current HEAD, which has migration 0064:

```bash
git checkout -b agent-studio-tech-stacks
git add docs/superpowers/specs/2026-09-22-agent-studio-tech-stacks-design.md docs/superpowers/plans/2026-09-22-agent-studio-tech-stacks.md
git commit -m "Spec and plan: Agent Studio tech stacks"
```

---

### Task 1: Rules — catalogue, validation, effective-stack decision, prompt block, table check

**Files:**
- Create: `backend/shared/services/tech_stack_catalog.py`
- Create: `backend/shared/services/tech_stack.py`
- Test: `backend/tests/test_tech_stack_rules.py`

**Interfaces:**
- Produces:
  - `CATEGORIES`, `CATEGORY_IDS`, `CATEGORY_LABELS`, `catalog() -> dict`
  - `TechStack`, `EffectiveTechStack`, `StackViolation` (dataclasses)
  - `validate_stack(name, description, categories, notes) -> (dict, list[dict])`
  - `decide_effective(*, project_id, workspace_id, selected, selection_id, default) -> EffectiveTechStack`
  - `render_for_prompt(eff) -> str`
  - `check_stack_table(eff, markdown) -> list[StackViolation]`
  - `correction_note(violations) -> str`
  - `annotate_stack_section(markdown, eff, violations) -> str`
  - `SOURCE_LABELS`, `NOT_COVERED`

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_tech_stack_rules.py`)

```python
"""The rules of a tech stack, with no database: what is valid, which stack a project
follows, what the model is told, and how its Technology Stack table is checked."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services.tech_stack import (  # noqa: E402
    NOT_COVERED, EffectiveTechStack, StackViolation, TechStack, annotate_stack_section,
    check_stack_table, correction_note, decide_effective, render_for_prompt, validate_stack,
)
from shared.services.tech_stack_catalog import CATEGORY_IDS, catalog  # noqa: E402

NODE = TechStack(id="s-node", scope="workspace", workspace_id="w1", project_id=None, name="Node + Next.js",
                 categories={"languages": ["TypeScript"], "backend_frameworks": ["Node.js", "Express"],
                             "frontend_frameworks": ["Next.js"], "databases": ["PostgreSQL"],
                             "cloud_hosting": ["Azure"]},
                 notes="Prefer managed services.", is_default=True)
JAVA = TechStack(id="s-java", scope="workspace", workspace_id="w1", project_id=None, name="Java + Spring Boot",
                 categories={"languages": ["Java"], "backend_frameworks": ["Spring Boot"]})
OWN = TechStack(id="s-own", scope="project", workspace_id=None, project_id="p1", name="QuickLink custom",
                categories={"languages": ["Go"]})

TABLE = """## TECHNOLOGY STACK

| Layer | Technology | Version | Justification |
|---|---|---|---|
| Frontend | **Next.js** | 14 | SSR |
| Backend | Node.js (Express) | 20 | team skills |
| Database | PostgreSQL | 16 | relational |
| Caching | Not covered by the project's tech stack — needs a decision | — | — |

## SECURITY DESIGN CHECKLIST
| Layer | Technology |
|---|---|
| Anything | MongoDB |
"""


def test_the_catalogue_names_every_category_in_order():
    assert CATEGORY_IDS[0] == "languages" and CATEGORY_IDS[-1] == "observability"
    cats = catalog()["categories"]
    assert [c["id"] for c in cats] == list(CATEGORY_IDS)
    assert "Spring Boot" in next(c for c in cats if c["id"] == "backend_frameworks")["suggestions"]


def test_a_valid_stack_is_trimmed_deduplicated_and_ordered_by_category():
    fields, violations = validate_stack(
        "  Node + Next.js ", "", {"databases": ["PostgreSQL"], "languages": [" TypeScript", "typescript", ""]}, "")
    assert violations == []
    assert fields["name"] == "Node + Next.js"
    assert list(fields["categories"]) == ["languages", "databases"]
    assert fields["categories"]["languages"] == ["TypeScript"]


def test_every_limit_is_named_on_its_field():
    _, v = validate_stack("ab", "d" * 281, {"nope": ["x"], "languages": ["x" * 61], "testing": [f"t{i}" for i in range(21)]}, "n" * 2001)
    codes = {(x["field"], x["code"]) for x in v}
    assert ("name", "name_length") in codes and ("description", "description_length") in codes
    assert ("notes", "notes_length") in codes and ("categories.nope", "unknown_category") in codes
    assert ("categories.languages", "item_length") in codes and ("categories.testing", "too_many_items") in codes


def test_an_empty_stack_is_refused():
    _, v = validate_stack("Empty stack", "", {"languages": ["  "]}, "")
    assert [x["code"] for x in v] == ["empty_stack"]


def test_the_projects_choice_wins_then_the_default_then_nothing():
    chosen = decide_effective(project_id="p1", workspace_id="w1", selected=JAVA, selection_id="s-java", default=NODE)
    assert chosen.stack is JAVA and chosen.source == "project_selection" and chosen.warning is None
    own = decide_effective(project_id="p1", workspace_id="w1", selected=OWN, selection_id="s-own", default=NODE)
    assert own.stack is OWN
    default = decide_effective(project_id="p1", workspace_id="w1", selected=None, selection_id=None, default=NODE)
    assert default.stack is NODE and default.source == "bu_default"
    none = decide_effective(project_id="p1", workspace_id="w1", selected=None, selection_id=None, default=None)
    assert none.stack is None and none.source == "none" and none.warning is None


def test_a_deleted_or_foreign_selection_falls_back_and_says_so():
    from dataclasses import replace
    gone = decide_effective(project_id="p1", workspace_id="w1", selected=replace(JAVA, deleted=True), selection_id="s-java", default=NODE)
    assert gone.stack is NODE and "was deleted" in gone.warning and "Node + Next.js" in gone.warning
    foreign = decide_effective(project_id="p1", workspace_id="w2", selected=JAVA, selection_id="s-java", default=None)
    assert foreign.stack is None and "another Business Unit" in foreign.warning and "recommend a stack freely" in foreign.warning


def test_the_prompt_block_lists_the_stack_and_the_rules():
    block = render_for_prompt(EffectiveTechStack(NODE, "bu_default"))
    assert block.startswith("PROJECT TECH STACK — MANDATORY")
    assert '"Node + Next.js" (the Business Unit default)' in block
    assert "- Backend frameworks: Node.js, Express" in block and "Prefer managed services." in block
    assert NOT_COVERED in block
    assert render_for_prompt(None) == "" and render_for_prompt(EffectiveTechStack(None, "none")) == ""


def test_the_table_check_reads_only_the_technology_stack_section():
    eff = EffectiveTechStack(NODE, "bu_default")
    assert check_stack_table(eff, TABLE) == []          # MongoDB is in another section; not-covered passes
    bad = TABLE.replace("| PostgreSQL | 16 |", "| MongoDB | 7 |")
    assert check_stack_table(eff, bad) == [StackViolation(layer="Database", technology="MongoDB")]
    assert check_stack_table(EffectiveTechStack(None, "none"), bad) == []


def test_short_names_match_whole_words_only():
    go = EffectiveTechStack(OWN, "project_selection")
    table = "## TECHNOLOGY STACK\n| Layer | Technology |\n|---|---|\n| Backend | Go 1.22 |\n| Database | MongoDB |\n"
    assert check_stack_table(go, table) == [StackViolation(layer="Database", technology="MongoDB")]


def test_the_correction_names_what_was_outside():
    note = correction_note([StackViolation("Database", "MongoDB")])
    assert "MongoDB (Database)" in note and NOT_COVERED in note


def test_the_section_is_stamped_once_and_leftovers_are_flagged():
    eff = EffectiveTechStack(NODE, "bu_default")
    stamped = annotate_stack_section(TABLE, eff, [])
    assert "## TECHNOLOGY STACK\n\nProject tech stack: **Node + Next.js** (the Business Unit default).\n" in stamped
    assert annotate_stack_section(stamped, eff, []) == stamped      # idempotent
    flagged = annotate_stack_section(TABLE, eff, [StackViolation("Database", "MongoDB")])
    assert "> **Outside the project's tech stack:** MongoDB (Database)." in flagged
    assert annotate_stack_section("no table here", eff, []) == "no table here"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_tech_stack_rules.py -q -p no:cacheprovider`
Expected: FAIL. `ModuleNotFoundError: shared.services.tech_stack`.

- [ ] **Step 3: Write the catalogue** (`backend/shared/services/tech_stack_catalog.py`)

```python
"""The categories a tech stack is made of, and what Agent Studio suggests for each.

One source for both ends: the router validates category ids against CATEGORIES and serves
`catalog()` to the editor, whose chip inputs suggest SUGGESTIONS while accepting anything
typed. A stack names what the organisation actually uses, not what this list knows.
"""
from __future__ import annotations

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("languages", "Languages"),
    ("backend_frameworks", "Backend frameworks"),
    ("frontend_frameworks", "Frontend frameworks"),
    ("databases", "Databases"),
    ("cloud_hosting", "Cloud & hosting"),
    ("messaging", "Messaging & integration"),
    ("devops", "CI/CD & DevOps"),
    ("testing", "Testing"),
    ("observability", "Observability"),
)
CATEGORY_IDS: tuple[str, ...] = tuple(cid for cid, _ in CATEGORIES)
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)

SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "languages": ("Java", "Kotlin", "TypeScript", "JavaScript", "Python", "C#", "Go", "Rust", "Scala",
                  "Ruby", "PHP", "SQL"),
    "backend_frameworks": ("Spring Boot", "Quarkus", "Micronaut", "Node.js", "Express", "NestJS", "FastAPI",
                           "Django", "Flask", ".NET", "ASP.NET Core", "Gin", "Ruby on Rails", "Laravel"),
    "frontend_frameworks": ("React", "Next.js", "Angular", "Vue", "Nuxt", "Svelte", "Tailwind CSS", "Material UI"),
    "databases": ("PostgreSQL", "MySQL", "SQL Server", "Oracle", "MongoDB", "Cosmos DB", "DynamoDB", "Redis",
                  "Elasticsearch", "Cassandra", "SQLite"),
    "cloud_hosting": ("Azure", "AWS", "Google Cloud", "Azure App Service", "Azure Kubernetes Service",
                      "Azure Functions", "AWS Lambda", "Amazon EKS", "Kubernetes", "Docker", "On-premises"),
    "messaging": ("Kafka", "RabbitMQ", "Azure Service Bus", "Azure Event Hubs", "Amazon SQS", "Amazon SNS",
                  "Google Pub/Sub", "REST", "GraphQL", "gRPC"),
    "devops": ("GitHub Actions", "Azure DevOps Pipelines", "GitLab CI", "Jenkins", "Terraform", "Bicep", "Helm",
               "Argo CD"),
    "testing": ("JUnit", "Mockito", "Jest", "Vitest", "Playwright", "Cypress", "pytest", "k6", "Postman"),
    "observability": ("OpenTelemetry", "Prometheus", "Grafana", "Azure Monitor", "Application Insights",
                      "Datadog", "ELK", "Sentry"),
}


def catalog() -> dict:
    return {"categories": [
        {"id": cid, "label": label, "suggestions": list(SUGGESTIONS.get(cid, ()))}
        for cid, label in CATEGORIES
    ]}
```

- [ ] **Step 4: Write the rules** (`backend/shared/services/tech_stack.py`)

```python
"""Tech stacks: what one is, which one a project follows, and how an agent is held to it.

A Business Unit offers stacks as alternatives and marks one its default; a project picks one,
or keeps one of its own. The project's choice replaces the BU's. This module holds the rules
only — no database (`tech_stack_store`) and no HTTP (`routers/tech_stacks`) — so every rule
is testable on its own.

HELD TO, NOT SUGGESTED. Skills reach an agent as a list the model may choose to read; a stack
is a constraint, so it goes into the prompt of the model call that writes the design, and the
Technology Stack table that call produces is checked against it (`check_stack_table`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from shared.services.tech_stack_catalog import CATEGORY_IDS, CATEGORY_LABELS

MIN_NAME, MAX_NAME = 3, 80
MAX_DESCRIPTION = 280
MAX_NOTES = 2000
MAX_ITEMS = 20
MAX_ITEM = 60

NOT_COVERED = "Not covered by the project's tech stack — needs a decision"
SOURCE_LABELS = {
    "project_selection": "chosen for this project",
    "bu_default": "the Business Unit default",
}


@dataclass(frozen=True)
class TechStack:
    id: str
    scope: str                                   # "workspace" | "project"
    workspace_id: Optional[str]
    project_id: Optional[str]
    name: str
    description: str = ""
    categories: dict = field(default_factory=dict)   # {category_id: [item, ...]}
    notes: str = ""
    is_default: bool = False
    deleted: bool = False


@dataclass(frozen=True)
class EffectiveTechStack:
    """The stack a project's agents follow, why, and anything its admins should know."""
    stack: Optional[TechStack]
    source: str                                  # "project_selection" | "bu_default" | "none"
    warning: Optional[str] = None


@dataclass(frozen=True)
class StackViolation:
    layer: str
    technology: str


def _violation(field_: str, code: str, message: str) -> dict:
    return {"field": field_, "code": code, "message": message}


def validate_stack(name, description, categories, notes) -> tuple[dict, list[dict]]:
    """(clean fields, violations) — violations in Agent Studio's {field, code, message} shape."""
    violations: list[dict] = []
    name = (name or "").strip()
    description = (description or "").strip()
    notes = (notes or "").strip()
    if not MIN_NAME <= len(name) <= MAX_NAME:
        violations.append(_violation("name", "name_length", f"A name is {MIN_NAME}–{MAX_NAME} characters."))
    if len(description) > MAX_DESCRIPTION:
        violations.append(_violation("description", "description_length",
                                     f"The description is at most {MAX_DESCRIPTION} characters."))
    if len(notes) > MAX_NOTES:
        violations.append(_violation("notes", "notes_length", f"Notes are at most {MAX_NOTES:,} characters."))

    clean: dict[str, list[str]] = {}
    item_problem = False
    for cid, items in (categories or {}).items():
        if cid not in CATEGORY_IDS:
            violations.append(_violation(f"categories.{cid}", "unknown_category",
                                         f"'{cid}' is not a tech-stack category."))
            item_problem = True
            continue
        seen: set[str] = set()
        kept: list[str] = []
        for raw in items or []:
            item = str(raw).strip()
            if not item:
                continue
            if len(item) > MAX_ITEM:
                violations.append(_violation(f"categories.{cid}", "item_length",
                                             f"'{item[:20]}…' is longer than {MAX_ITEM} characters."))
                item_problem = True
                continue
            if item.lower() not in seen:
                seen.add(item.lower())
                kept.append(item)
        if len(kept) > MAX_ITEMS:
            violations.append(_violation(f"categories.{cid}", "too_many_items",
                                         f"{CATEGORY_LABELS[cid]} holds at most {MAX_ITEMS} items."))
            item_problem = True
        if kept:
            clean[cid] = kept
    if not clean and not item_problem:
        violations.append(_violation("categories", "empty_stack", "Add at least one technology."))
    ordered = {cid: clean[cid] for cid in CATEGORY_IDS if cid in clean}
    return {"name": name, "description": description, "categories": ordered, "notes": notes}, violations


def decide_effective(*, project_id, workspace_id, selected: Optional[TechStack],
                     selection_id: Optional[str], default: Optional[TechStack]) -> EffectiveTechStack:
    """The project's selection when it still stands, else the BU default, else none.

    A selection stands when its stack is live and is the project's own or its CURRENT Business
    Unit's. One that no longer stands is not followed silently: the answer carries a warning
    that Agent Studio and the Design page show."""
    project_id = str(project_id) if project_id else None
    workspace_id = str(workspace_id) if workspace_id else None
    warning = None
    if selection_id is not None:
        stands = (selected is not None and not selected.deleted and (
            selected.project_id == project_id
            or (selected.scope == "workspace" and selected.workspace_id == workspace_id)))
        if stands:
            return EffectiveTechStack(selected, "project_selection")
        warning = ("The tech stack chosen for this project was deleted."
                   if selected is None or selected.deleted
                   else "The tech stack chosen for this project belongs to another Business Unit.")
        warning += (f" Following the Business Unit default, {default.name}, instead." if default
                    else " No Business Unit default is set, so agents recommend a stack freely.")
    if default is not None:
        return EffectiveTechStack(default, "bu_default", warning)
    return EffectiveTechStack(None, "none", warning)


def render_for_prompt(eff: Optional[EffectiveTechStack]) -> str:
    """The mandatory block a model call gets; '' when the project follows no stack."""
    if eff is None or eff.stack is None:
        return ""
    s = eff.stack
    lines = [
        "PROJECT TECH STACK — MANDATORY",
        f'This project uses the tech stack "{s.name}" ({SOURCE_LABELS.get(eff.source, eff.source)}). '
        "Design ONLY with these technologies:",
    ]
    for cid in CATEGORY_IDS:
        items = s.categories.get(cid) or []
        if items:
            lines.append(f"- {CATEGORY_LABELS[cid]}: {', '.join(items)}")
    if s.notes:
        lines.append(f"Notes from the project's admins: {s.notes}")
    lines += [
        "Rules:",
        "1. Every technology you name — the technology stack table, C4 containers, low-level design, "
        "database schema, API contracts, ADRs and deployment — must come from this list.",
        f'2. Where the list does not cover a need, write "{NOT_COVERED}" instead of choosing another technology.',
        "3. Do not recommend alternatives from outside this list.",
    ]
    return "\n".join(lines)


_STACK_HEADER = re.compile(r"^##\s+(?:\d+[.)]?\s*)?TECHNOLOGY STACK\b[^\n]*$", re.IGNORECASE | re.MULTILINE)
_EMPTY_CELLS = {"", "-", "—", "–", "n/a", "na", "none", "tbd"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9#+]", "", text.lower())


def _item_matches(item: str, cell: str) -> bool:
    n_item = _norm(item)
    if not n_item:
        return False
    if len(n_item) <= 2:     # "Go", "C#": whole words only, or "Go" would match "MongoDB"
        return re.search(rf"(?<![a-z0-9]){re.escape(item.lower())}(?![a-z0-9])", cell.lower()) is not None
    return n_item in _norm(cell)


def _stack_section(markdown: str) -> Optional[tuple[int, int]]:
    m = _STACK_HEADER.search(markdown or "")
    if not m:
        return None
    nxt = re.search(r"^##\s", markdown[m.end():], re.MULTILINE)
    return m.start(), (m.end() + nxt.start()) if nxt else len(markdown)


def check_stack_table(eff: Optional[EffectiveTechStack], markdown: str) -> list[StackViolation]:
    """Rows of the TECHNOLOGY STACK table whose Technology names nothing in the stack."""
    if eff is None or eff.stack is None:
        return []
    span = _stack_section(markdown)
    if span is None:
        return []
    items = [i for group in eff.stack.categories.values() for i in group]
    tech_idx = layer_idx = None
    out: list[StackViolation] = []
    for line in markdown[span[0]:span[1]].splitlines():
        row = line.strip()
        if not row.startswith("|"):
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        if tech_idx is None:
            lowered = [c.lower() for c in cells]
            if "technology" in lowered:
                tech_idx = lowered.index("technology")
                layer_idx = lowered.index("layer") if "layer" in lowered else 0
            continue
        if all(set(c) <= set("-: ") for c in cells) or tech_idx >= len(cells):
            continue
        cell = re.sub(r"[*_`]", "", cells[tech_idx]).strip()
        if cell.lower() in _EMPTY_CELLS or "not covered" in cell.lower():
            continue
        if any(_item_matches(i, cell) for i in items):
            continue
        layer = cells[layer_idx] if layer_idx is not None and layer_idx < len(cells) else ""
        out.append(StackViolation(layer=re.sub(r"[*_`]", "", layer).strip(), technology=cell))
    return out


def _listed(violations: Iterable[StackViolation]) -> str:
    return ", ".join(f"{v.technology} ({v.layer})" if v.layer else v.technology for v in violations)


def correction_note(violations: list[StackViolation]) -> str:
    return ("CORRECTION — the previous TECHNOLOGY STACK table named technologies outside the project's "
            f"tech stack: {_listed(violations)}. Regenerate the TECHNOLOGY STACK section using ONLY the "
            f'project\'s tech stack; where it does not cover a layer, write "{NOT_COVERED}".')


def annotate_stack_section(markdown: str, eff: Optional[EffectiveTechStack],
                           violations: list[StackViolation]) -> str:
    """Open the TECHNOLOGY STACK section with the stack it was held to, and flag leftovers."""
    if eff is None or eff.stack is None:
        return markdown
    m = _STACK_HEADER.search(markdown or "")
    if not m:
        return markdown
    stamp = (f"Project tech stack: **{eff.stack.name}** "
             f"({SOURCE_LABELS.get(eff.source, eff.source)}).")
    lines = []
    if stamp not in markdown[m.end():m.end() + len(stamp) + 8]:
        lines.append(stamp)
    if violations:
        lines.append(f"> **Outside the project's tech stack:** {_listed(violations)}. "
                     "Review before approving this design.")
    if not lines:
        return markdown
    return markdown[:m.end()] + "\n\n" + "\n\n".join(lines) + markdown[m.end():]
```

Note on the idempotence test: after one stamp the header is followed by `\n\nProject tech stack: …`, so the window `markdown[m.end(): m.end()+len(stamp)+8]` contains the stamp and nothing is added.

- [ ] **Step 5: Run the tests.** `cd backend && .venv/Scripts/python.exe -m pytest tests/test_tech_stack_rules.py -q -p no:cacheprovider`. Expected: all pass.

- [ ] **Step 6: Commit.** `git add backend/shared/services/tech_stack.py backend/shared/services/tech_stack_catalog.py backend/tests/test_tech_stack_rules.py && git commit -m "Tech stack rules: validation, effective stack, prompt block, table check"`

---

### Task 2: Tables — migration 0065 and ORM models

**Files:**
- Create: `backend/migrations/versions/0065_tech_stacks.py`
- Modify: `backend/shared/models/orm.py` (add two models after `AgentSkillToggle`)

**Interfaces:**
- Produces: ORM classes `TechStackRecord` (`tech_stacks`) and `ProjectTechStackSelection` (`project_tech_stack_selections`), with the columns below.

- [ ] **Step 1: Write the migration**

```python
"""Tech stacks: Business Unit alternatives and each project's choice (Agent Studio).

A Business Unit offers stacks and marks at most one its default; a project picks one or keeps
its own. Two tables, tenant RLS like every table added after the baseline. No role or
permission rows: ownership is Agent Studio's existing tier rule, so no RBAC catalogue drift.

Revision ID: 0065_tech_stacks
Revises: 0064_document_approval_kinds
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0065_tech_stacks"
down_revision = "0064_document_approval_kinds"
branch_labels = None
depends_on = None

_TABLES = ("tech_stacks", "project_tech_stack_selections")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} "
               "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)")
    op.execute(f"CREATE POLICY tenant_isolation_insert ON {table} "
               "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sdlc_app;
            END IF;
        END
        $$;
    """)


def upgrade() -> None:
    op.create_table(
        "tech_stacks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("categories", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scope IN ('workspace', 'project')", name="ck_tech_stack_scope"),
        sa.CheckConstraint(
            "(scope = 'workspace' AND workspace_id IS NOT NULL AND project_id IS NULL) OR "
            "(scope = 'project' AND project_id IS NOT NULL AND workspace_id IS NULL)",
            name="ck_tech_stack_scope_ids"),
        sa.CheckConstraint("NOT is_default OR scope = 'workspace'", name="ck_tech_stack_default_is_bu"),
    )
    op.create_index("ix_tech_stacks_tenant_id", "tech_stacks", ["tenant_id"])
    op.create_index("ix_tech_stacks_workspace_id", "tech_stacks", ["workspace_id"])
    op.create_index("ix_tech_stacks_project_id", "tech_stacks", ["project_id"])
    op.execute("CREATE UNIQUE INDEX uq_tech_stack_default ON tech_stacks (workspace_id) "
               "WHERE is_default AND deleted_at IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_tech_stack_name ON tech_stacks "
               "(scope, COALESCE(workspace_id, project_id), lower(name)) WHERE deleted_at IS NULL")

    op.create_table(
        "project_tech_stack_selections",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tech_stack_id", UUID(as_uuid=True), sa.ForeignKey("tech_stacks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("selected_by", sa.String(255), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_project_tech_stack_selections_tenant_id", "project_tech_stack_selections", ["tenant_id"])

    for table in _TABLES:
        _rls(table)


def downgrade() -> None:
    op.drop_table("project_tech_stack_selections")
    op.drop_table("tech_stacks")
```

- [ ] **Step 2: Add the ORM models** to `backend/shared/models/orm.py`, after `class AgentSkillToggle`. Reuse the file's existing imports (`UUID`, `JSONB`, `Mapped`, `mapped_column`, `String`, `Text`, `Boolean`, `DateTime`, `ForeignKey`, `func`, `text`), adding any it lacks to its import block.

```python
class TechStackRecord(Base):
    """A named tech stack a Business Unit offers its projects, or a project keeps (0065).

    `scope` is "workspace" (a Business Unit's; `workspace_id` set) or "project" (`project_id`
    set). At most one live `is_default` per Business Unit. Deleting is soft (`deleted_at`),
    so a project that chose it can be told what happened."""

    __tablename__ = "tech_stacks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    categories: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectTechStackSelection(Base):
    """Which stack a project follows (0065). No row = the Business Unit default."""

    __tablename__ = "project_tech_stack_selections"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tech_stack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tech_stacks.id", ondelete="CASCADE"), nullable=False)
    selected_by: Mapped[str | None] = mapped_column(String(255))
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Do NOT add them to `_RLS_TABLES` (that tuple is the baseline's; the comment above it says so).

- [ ] **Step 3: Check the migration chain and imports:**
  - `cd backend && .venv/Scripts/python.exe -m alembic heads` → prints `0065_tech_stacks (head)` only.
  - `.venv/Scripts/python.exe -c "import shared.models.orm as o; print(o.TechStackRecord.__table__.c.keys())"`
  - The migration is applied to the dev DB in Task 8.

- [ ] **Step 4: Run the model/enum tests**
  - Run: `.venv/Scripts/python.exe -m pytest tests/test_db_enums_match_the_code.py -q -p no:cacheprovider`
  - Expected: pass, or the same pre-existing result as before the change. Record which.

- [ ] **Step 5: Commit.** `git add backend/migrations/versions/0065_tech_stacks.py backend/shared/models/orm.py && git commit -m "Tech stack tables (0065)"`

---

### Task 3: Store and cached resolver

**Files:**
- Create: `backend/shared/services/tech_stack_store.py`
- Test: `backend/tests/test_tech_stack_store.py` (cache and ws-helper wiring, with a patched resolver; the DB queries are verified live in Task 8)

**Interfaces:**
- Consumes: Task 1 dataclasses and `decide_effective`; Task 2 ORM classes.
- Produces (all async unless noted):
  - `project_scope(tenant_id, project_id) -> tuple[bool, Optional[str]]`
  - `workspace_exists(tenant_id, workspace_id) -> bool`
  - `list_workspace_stacks(tenant_id, workspace_id) -> list[TechStack]`
  - `list_project_stacks(tenant_id, project_id) -> list[TechStack]`
  - `get_stack(tenant_id, stack_id, *, include_deleted=False) -> Optional[TechStack]`
  - `create_stack(tenant_id, *, scope, scope_id, fields, actor) -> TechStack`
  - `update_stack(tenant_id, stack_id, fields, actor) -> Optional[TechStack]`
  - `delete_stack(tenant_id, stack_id, actor) -> Optional[TechStack]`
  - `set_default(tenant_id, stack_id, is_default, actor) -> Optional[TechStack]`
  - `get_selection(tenant_id, project_id) -> Optional[str]`
  - `set_selection(tenant_id, project_id, stack_id, actor) -> None`
  - `resolve_project_tech_stack(tenant_id, project_id) -> EffectiveTechStack`
  - `resolve_project_tech_stack_cached(tenant_id, project_id, *, ttl=45.0) -> EffectiveTechStack`
  - `invalidate_tech_stack_cache(tenant_id=None) -> None` (sync)
  - `current_project_tech_stack() -> Optional[EffectiveTechStack]`
  - `class DuplicateStackName(ValueError)`

- [ ] **Step 1: Write the failing test** (`backend/tests/test_tech_stack_store.py`)

```python
"""The resolver's cache and how agents reach it — the SQL itself is exercised live."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services import tech_stack_store as store  # noqa: E402
from shared.services.tech_stack import EffectiveTechStack, TechStack  # noqa: E402

NODE = EffectiveTechStack(TechStack("s1", "workspace", "w1", None, "Node + Next.js", categories={"languages": ["TypeScript"]}), "bu_default")


async def test_a_resolution_is_cached_until_invalidated(monkeypatch):
    store.invalidate_tech_stack_cache()
    fetch = AsyncMock(return_value=NODE)
    monkeypatch.setattr(store, "resolve_project_tech_stack", fetch)
    assert await store.resolve_project_tech_stack_cached("t1", "p1") is NODE
    assert await store.resolve_project_tech_stack_cached("t1", "p1") is NODE
    assert fetch.await_count == 1
    store.invalidate_tech_stack_cache("t1")
    await store.resolve_project_tech_stack_cached("t1", "p1")
    assert fetch.await_count == 2


async def test_a_failed_read_is_not_cached_and_says_so(monkeypatch):
    store.invalidate_tech_stack_cache()
    monkeypatch.setattr(store, "resolve_project_tech_stack", AsyncMock(side_effect=RuntimeError("db down")))
    eff = await store.resolve_project_tech_stack_cached("t1", "p2")
    assert eff.stack is None and "could not be read" in eff.warning
    assert ("t1", "p2") not in store._CACHE


async def test_agents_resolve_the_turns_project(monkeypatch):
    store.invalidate_tech_stack_cache()
    monkeypatch.setattr(store, "resolve_project_tech_stack", AsyncMock(return_value=NODE))
    import config.ws_helper as ws
    ws.set_tenant_id("t1"); ws.set_project_id("p3")
    assert await store.current_project_tech_stack() is NODE
    ws.set_project_id(None)
    assert await store.current_project_tech_stack() is None
```

- [ ] **Step 2: Run to verify it fails.** Run `.venv/Scripts/python.exe -m pytest tests/test_tech_stack_store.py -q -p no:cacheprovider`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the store** (`backend/shared/services/tech_stack_store.py`)

```python
"""Tech stacks in Postgres — the only module that reads or writes the two tables.

Every call is tenant-scoped (`get_db_session_for_tenant` sets the RLS tenant), so another
organisation's rows are invisible rather than filtered. Authorisation is not here: the router
decides who may write; this module does what it is told.

THE RESOLVER IS ON EVERY DESIGN TURN, so it is one connection and three queries, cached for
45 s per (tenant, project) — the same budget skills use — and every write invalidates the
tenant's entries, so an admin's change applies to the next turn.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update

from shared.db import get_db_session_for_tenant
from shared.models.orm import Project, ProjectTechStackSelection, TechStackRecord, Workspace
from shared.services.tech_stack import EffectiveTechStack, TechStack, decide_effective

logger = logging.getLogger(__name__)

TTL_SECONDS = 45.0
_CACHE: dict[tuple[str, str], tuple[float, EffectiveTechStack]] = {}


class DuplicateStackName(ValueError):
    """A live stack of the same tier already has this name."""


def _uuid(value) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _to_stack(row: TechStackRecord) -> TechStack:
    return TechStack(
        id=str(row.id), scope=row.scope,
        workspace_id=str(row.workspace_id) if row.workspace_id else None,
        project_id=str(row.project_id) if row.project_id else None,
        name=row.name, description=row.description or "", categories=dict(row.categories or {}),
        notes=row.notes or "", is_default=bool(row.is_default), deleted=row.deleted_at is not None,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def project_scope(tenant_id, project_id) -> tuple[bool, Optional[str]]:
    """(the project exists in this tenant, its Business Unit id or None)."""
    pid = _uuid(project_id)
    if pid is None:
        return False, None
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = (await db.execute(select(Project.id, Project.workspace_id).where(Project.id == pid))).first()
    if row is None:
        return False, None
    return True, (str(row.workspace_id) if row.workspace_id else None)


async def workspace_exists(tenant_id, workspace_id) -> bool:
    wid = _uuid(workspace_id)
    if wid is None:
        return False
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        return (await db.execute(select(Workspace.id).where(Workspace.id == wid))).first() is not None


def _live_ordered(where) -> "select":
    return (select(TechStackRecord).where(where, TechStackRecord.deleted_at.is_(None))
            .order_by(TechStackRecord.is_default.desc(), func.lower(TechStackRecord.name)))


async def list_workspace_stacks(tenant_id, workspace_id) -> list[TechStack]:
    wid = _uuid(workspace_id)
    if wid is None:
        return []
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        rows = (await db.execute(_live_ordered(TechStackRecord.workspace_id == wid))).scalars().all()
    return [_to_stack(r) for r in rows]


async def list_project_stacks(tenant_id, project_id) -> list[TechStack]:
    pid = _uuid(project_id)
    if pid is None:
        return []
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        rows = (await db.execute(_live_ordered(TechStackRecord.project_id == pid))).scalars().all()
    return [_to_stack(r) for r in rows]


async def get_stack(tenant_id, stack_id, *, include_deleted: bool = False) -> Optional[TechStack]:
    sid = _uuid(stack_id)
    if sid is None:
        return None
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = (await db.execute(select(TechStackRecord).where(TechStackRecord.id == sid))).scalar_one_or_none()
    if row is None or (row.deleted_at is not None and not include_deleted):
        return None
    return _to_stack(row)


async def _name_taken(db, scope: str, scope_uuid, name: str, exclude=None) -> bool:
    column = TechStackRecord.workspace_id if scope == "workspace" else TechStackRecord.project_id
    q = select(TechStackRecord.id).where(column == scope_uuid, TechStackRecord.deleted_at.is_(None),
                                         func.lower(TechStackRecord.name) == name.lower())
    if exclude is not None:
        q = q.where(TechStackRecord.id != exclude)
    return (await db.execute(q.limit(1))).first() is not None


async def create_stack(tenant_id, *, scope: str, scope_id, fields: dict, actor: str) -> TechStack:
    sid = _uuid(scope_id)
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        if await _name_taken(db, scope, sid, fields["name"]):
            raise DuplicateStackName(fields["name"])
        now = _now()
        row = TechStackRecord(
            id=uuid.uuid4(), tenant_id=_uuid(tenant_id), scope=scope,
            workspace_id=sid if scope == "workspace" else None,
            project_id=sid if scope == "project" else None,
            name=fields["name"], description=fields["description"], categories=fields["categories"],
            notes=fields["notes"], is_default=False, created_by=actor, updated_by=actor,
            created_at=now, updated_at=now,
        )
        db.add(row)
        await db.flush()
        return _to_stack(row)


async def _live_row(db, stack_id) -> Optional[TechStackRecord]:
    sid = _uuid(stack_id)
    if sid is None:
        return None
    return (await db.execute(select(TechStackRecord).where(
        TechStackRecord.id == sid, TechStackRecord.deleted_at.is_(None)))).scalar_one_or_none()


async def update_stack(tenant_id, stack_id, fields: dict, actor: str) -> Optional[TechStack]:
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = await _live_row(db, stack_id)
        if row is None:
            return None
        scope_uuid = row.workspace_id if row.scope == "workspace" else row.project_id
        if await _name_taken(db, row.scope, scope_uuid, fields["name"], exclude=row.id):
            raise DuplicateStackName(fields["name"])
        row.name, row.description = fields["name"], fields["description"]
        row.categories, row.notes = fields["categories"], fields["notes"]
        row.updated_by, row.updated_at = actor, _now()
        await db.flush()
        return _to_stack(row)


async def delete_stack(tenant_id, stack_id, actor: str) -> Optional[TechStack]:
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = await _live_row(db, stack_id)
        if row is None:
            return None
        row.deleted_at, row.is_default = _now(), False
        row.updated_by, row.updated_at = actor, _now()
        await db.flush()
        return _to_stack(row)


async def set_default(tenant_id, stack_id, is_default: bool, actor: str) -> Optional[TechStack]:
    """Make this Business Unit stack the BU's default (clearing any other) or clear it."""
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        row = await _live_row(db, stack_id)
        if row is None or row.scope != "workspace":
            return None
        now = _now()
        if is_default:
            # Clear the old default FIRST, so the one-default-per-BU index never sees two.
            await db.execute(update(TechStackRecord).where(
                TechStackRecord.workspace_id == row.workspace_id, TechStackRecord.is_default.is_(True),
                TechStackRecord.id != row.id).values(is_default=False, updated_by=actor, updated_at=now))
            await db.flush()
        row.is_default, row.updated_by, row.updated_at = is_default, actor, now
        await db.flush()
        return _to_stack(row)


async def get_selection(tenant_id, project_id) -> Optional[str]:
    pid = _uuid(project_id)
    if pid is None:
        return None
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        sid = (await db.execute(select(ProjectTechStackSelection.tech_stack_id).where(
            ProjectTechStackSelection.project_id == pid))).scalar_one_or_none()
    return str(sid) if sid else None


async def set_selection(tenant_id, project_id, stack_id: Optional[str], actor: str) -> None:
    """Point the project at a stack, or (None) back at its Business Unit default."""
    pid = _uuid(project_id)
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        existing = await db.get(ProjectTechStackSelection, pid)
        if stack_id is None:
            if existing is not None:
                await db.delete(existing)
            return
        if existing is None:
            db.add(ProjectTechStackSelection(project_id=pid, tenant_id=_uuid(tenant_id),
                                             tech_stack_id=_uuid(stack_id), selected_by=actor, selected_at=_now()))
        else:
            existing.tech_stack_id, existing.selected_by, existing.selected_at = _uuid(stack_id), actor, _now()


async def resolve_project_tech_stack(tenant_id, project_id) -> EffectiveTechStack:
    """The stack this project follows now — one connection, three queries."""
    pid = _uuid(project_id)
    if not tenant_id or pid is None:
        return EffectiveTechStack(None, "none")
    async with get_db_session_for_tenant(str(tenant_id)) as db:
        ws = (await db.execute(select(Project.workspace_id).where(Project.id == pid))).scalar_one_or_none()
        sel_id = (await db.execute(select(ProjectTechStackSelection.tech_stack_id).where(
            ProjectTechStackSelection.project_id == pid))).scalar_one_or_none()
        wanted = [x for x in (sel_id,) if x]
        rows = []
        if wanted or ws:
            q = select(TechStackRecord).where(
                (TechStackRecord.id.in_(wanted)) if not ws else
                (TechStackRecord.id.in_(wanted) | ((TechStackRecord.workspace_id == ws)
                                                   & TechStackRecord.is_default.is_(True)
                                                   & TechStackRecord.deleted_at.is_(None))))
            rows = (await db.execute(q)).scalars().all()
    by_id = {r.id: r for r in rows}
    selected = _to_stack(by_id[sel_id]) if sel_id and sel_id in by_id else None
    default_row = next((r for r in rows if r.workspace_id == ws and r.is_default and r.deleted_at is None), None)
    return decide_effective(project_id=str(pid), workspace_id=str(ws) if ws else None,
                            selected=selected, selection_id=str(sel_id) if sel_id else None,
                            default=_to_stack(default_row) if default_row else None)


async def resolve_project_tech_stack_cached(tenant_id, project_id, *, ttl: float = TTL_SECONDS) -> EffectiveTechStack:
    key = (str(tenant_id), str(project_id))
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    try:
        eff = await resolve_project_tech_stack(tenant_id, project_id)
    except Exception as exc:  # noqa: BLE001 — never cached, never silent: the warning reaches the user
        logger.warning("tech stack resolution failed for project %s: %s", project_id, type(exc).__name__)
        return EffectiveTechStack(None, "none",
                                  "The project's tech stack could not be read, so this answer does not follow one.")
    _CACHE[key] = (now + ttl, eff)
    return eff


def invalidate_tech_stack_cache(tenant_id=None) -> None:
    for key in [k for k in _CACHE if tenant_id is None or k[0] == str(tenant_id)]:
        _CACHE.pop(key, None)


async def current_project_tech_stack() -> Optional[EffectiveTechStack]:
    """The stack for the turn's project (tenant and project from `config.ws_helper`, which every
    Design entry point sets), or None when the turn has no project."""
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    tenant, project = get_tenant_id(), get_project_id()
    if not tenant or not project:
        return None
    return await resolve_project_tech_stack_cached(tenant, project)
```

`Workspace` is the ORM class for `workspaces` (`orm.py:38`). If the class name differs, import the class whose `__tablename__ = "workspaces"`.

- [ ] **Step 4: Run the tests.** `.venv/Scripts/python.exe -m pytest tests/test_tech_stack_store.py tests/test_tech_stack_rules.py -q -p no:cacheprovider`. Expected: all pass.

- [ ] **Step 5: Commit.** `git add backend/shared/services/tech_stack_store.py backend/tests/test_tech_stack_store.py && git commit -m "Tech stack store and cached resolver"`

---

### Task 4: API — `/tech-stacks` and `/projects/{id}/tech-stack`

**Files:**
- Create: `backend/shared/routers/tech_stacks.py`
- Modify: `backend/process_api.py` (include the router right after `agent_skills_router`, line ~1214)
- Test: `backend/tests/test_tech_stacks_router.py`

**Interfaces:**
- Consumes: Task 3 store functions (module `store`); `resolve_actor_tier_access` from `shared.routers.agent_profiles`.
- Produces the routes below. JSON is snake_case:
  - Stack: `{id, scope, workspace_id, project_id, name, description, categories, notes, is_default}`
  - Project view: `{project_id, workspace_id, options: {business_unit: [Stack], project: [Stack]}, selection: {tech_stack_id}, effective: {stack, source, warning}, can_manage, can_manage_business_unit}`
  - BU list: `{items: [Stack], can_manage}`

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_tech_stacks_router.py`)

```python
"""Who may do what with tech stacks, and what the API answers — against an in-memory store."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.routers import tech_stacks as r  # noqa: E402
from shared.services.tech_stack import EffectiveTechStack, TechStack, decide_effective  # noqa: E402

T, W1, W2, P1 = "t-1", "w-1", "w-2", "p-1"


class FakeStore:
    DuplicateStackName = type("DuplicateStackName", (ValueError,), {})

    def __init__(self):
        self.stacks: dict[str, TechStack] = {}
        self.selection: dict[str, str] = {}
        self.n = 0

    async def project_scope(self, tenant, project):
        return (project == P1), (W1 if project == P1 else None)

    async def workspace_exists(self, tenant, ws):
        return ws in (W1, W2)

    async def list_workspace_stacks(self, tenant, ws):
        return [s for s in self.stacks.values() if s.workspace_id == ws and not s.deleted]

    async def list_project_stacks(self, tenant, project):
        return [s for s in self.stacks.values() if s.project_id == project and not s.deleted]

    async def get_stack(self, tenant, sid, include_deleted=False):
        s = self.stacks.get(sid)
        return s if s and (include_deleted or not s.deleted) else None

    async def create_stack(self, tenant, *, scope, scope_id, fields, actor):
        if any(s.name.lower() == fields["name"].lower() and (s.workspace_id or s.project_id) == scope_id
               and not s.deleted for s in self.stacks.values()):
            raise self.DuplicateStackName(fields["name"])
        self.n += 1
        s = TechStack(id=f"s{self.n}", scope=scope, workspace_id=scope_id if scope == "workspace" else None,
                      project_id=scope_id if scope == "project" else None, **fields)
        self.stacks[s.id] = s
        return s

    async def update_stack(self, tenant, sid, fields, actor):
        from dataclasses import replace
        self.stacks[sid] = replace(self.stacks[sid], **fields)
        return self.stacks[sid]

    async def delete_stack(self, tenant, sid, actor):
        from dataclasses import replace
        self.stacks[sid] = replace(self.stacks[sid], deleted=True, is_default=False)
        return self.stacks[sid]

    async def set_default(self, tenant, sid, is_default, actor):
        from dataclasses import replace
        s = self.stacks[sid]
        for k, v in list(self.stacks.items()):
            if v.workspace_id == s.workspace_id and v.is_default:
                self.stacks[k] = replace(v, is_default=False)
        self.stacks[sid] = replace(self.stacks[sid], is_default=is_default)
        return self.stacks[sid]

    async def get_selection(self, tenant, project):
        return self.selection.get(project)

    async def set_selection(self, tenant, project, sid, actor):
        if sid is None:
            self.selection.pop(project, None)
        else:
            self.selection[project] = sid

    async def resolve_project_tech_stack(self, tenant, project):
        sel = self.selection.get(project)
        default = next((s for s in self.stacks.values() if s.workspace_id == W1 and s.is_default and not s.deleted), None)
        return decide_effective(project_id=project, workspace_id=W1, selected=self.stacks.get(sel) if sel else None,
                                selection_id=sel, default=default)

    def invalidate_tech_stack_cache(self, tenant=None):
        self.invalidated = True


@pytest.fixture
def env(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(r, "store", fake)
    owners: set[tuple[str, str]] = set()

    async def tier_access(tenant, user, perms, scope, scope_id):
        return ((scope, scope_id) in owners), False

    monkeypatch.setattr(r, "resolve_actor_tier_access", tier_access)
    monkeypatch.setattr(r, "_assert_project_visible", AsyncMock())
    monkeypatch.setattr(r, "_emit", AsyncMock())
    return SimpleNamespace(store=fake, owners=owners)


def req(user="u-1"):
    return SimpleNamespace(state=SimpleNamespace(tenant_id=T, user_id=user, permissions=[]))


def body(**kw):
    base = {"name": "Node + Next.js", "description": "", "categories": {"languages": ["TypeScript"]}, "notes": ""}
    base.update(kw)
    return base


async def test_a_bu_admin_creates_a_stack_and_a_non_owner_cannot(env):
    env.owners.add(("workspace", W1))
    out = await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body()), req())
    assert out["name"] == "Node + Next.js" and out["workspace_id"] == W1 and out["is_default"] is False
    with pytest.raises(HTTPException) as denied:
        await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W2, **body()), req())
    assert denied.value.status_code == 403 and "Business Unit admin" in denied.value.detail


async def test_invalid_and_duplicate_stacks_answer_violations(env):
    env.owners.add(("workspace", W1))
    with pytest.raises(HTTPException) as bad:
        await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body(name="x", categories={})), req())
    assert bad.value.status_code == 422
    assert {v["code"] for v in bad.value.detail["violations"]} == {"name_length", "empty_stack"}
    await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body()), req())
    with pytest.raises(HTTPException) as dup:
        await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body(name="node + next.js")), req())
    assert dup.value.detail["violations"][0]["code"] == "duplicate_name"


async def test_an_unknown_scope_or_missing_business_unit_is_refused(env):
    env.owners.add(("workspace", "w-nope"))
    with pytest.raises(HTTPException) as scope:
        await r.create_tech_stack(r.CreateTechStackIn(scope="org", scope_id="x", **body()), req())
    assert scope.value.status_code == 422
    with pytest.raises(HTTPException) as missing:
        await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id="w-nope", **body()), req())
    assert missing.value.status_code == 404


async def test_one_default_per_business_unit_and_only_a_bu_stack_can_be_it(env):
    env.owners |= {("workspace", W1), ("project", P1)}
    a = await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body(name="Java")), req())
    b = await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body(name="Node")), req())
    await r.set_tech_stack_default(a["id"], r.DefaultIn(is_default=True), req())
    await r.set_tech_stack_default(b["id"], r.DefaultIn(is_default=True), req())
    listed = await r.list_business_unit_tech_stacks(req(), workspace_id=W1)
    assert [s["name"] for s in listed["items"] if s["is_default"]] == ["Node"] and listed["can_manage"] is True
    own = await r.create_tech_stack(r.CreateTechStackIn(scope="project", scope_id=P1, **body(name="Own")), req())
    with pytest.raises(HTTPException) as not_bu:
        await r.set_tech_stack_default(own["id"], r.DefaultIn(is_default=True), req())
    assert not_bu.value.status_code == 422


async def test_a_project_admin_picks_one_and_the_view_shows_what_agents_follow(env):
    env.owners |= {("workspace", W1), ("project", P1)}
    java = await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body(name="Java")), req())
    node = await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body(name="Node")), req())
    await r.set_tech_stack_default(node["id"], r.DefaultIn(is_default=True), req())
    view = await r.get_project_tech_stack(P1, req())
    assert view["effective"]["source"] == "bu_default" and view["effective"]["stack"]["name"] == "Node"
    assert view["selection"]["tech_stack_id"] is None and view["can_manage"] is True
    view = await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=java["id"]), req())
    assert view["effective"]["stack"]["name"] == "Java" and view["effective"]["source"] == "project_selection"
    view = await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=None), req())
    assert view["effective"]["stack"]["name"] == "Node"


async def test_a_project_cannot_pick_another_business_units_stack_and_only_its_admin_picks(env):
    env.owners |= {("workspace", W2)}
    other = await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W2, **body(name="Other")), req())
    with pytest.raises(HTTPException) as denied:
        await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=other["id"]), req())
    assert denied.value.status_code == 403
    env.owners.add(("project", P1))
    with pytest.raises(HTTPException) as foreign:
        await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=other["id"]), req())
    assert foreign.value.status_code == 409


async def test_deleting_the_selected_stack_falls_back_with_a_warning(env):
    env.owners |= {("workspace", W1), ("project", P1)}
    java = await r.create_tech_stack(r.CreateTechStackIn(scope="workspace", scope_id=W1, **body(name="Java")), req())
    await r.select_project_tech_stack(P1, r.SelectionIn(tech_stack_id=java["id"]), req())
    assert (await r.delete_tech_stack(java["id"], req())) == {"deleted": True, "id": java["id"]}
    view = await r.get_project_tech_stack(P1, req())
    assert view["effective"]["stack"] is None and "was deleted" in view["effective"]["warning"]
    assert env.store.invalidated is True


def test_every_route_sits_behind_the_view_floor():
    from shared.authz.dependency import require_permission  # noqa: F401
    assert r.tech_stacks_router.dependencies, "the router carries the artifact:view floor"
```

- [ ] **Step 2: Run to verify it fails.** Run `.venv/Scripts/python.exe -m pytest tests/test_tech_stacks_router.py -q -p no:cacheprovider`. Expected: `ImportError`.

- [ ] **Step 3: Write the router** (`backend/shared/routers/tech_stacks.py`)

```python
"""Agent Studio tech stacks — Business Unit alternatives and each project's choice.

WHO MAY DO WHAT is Agent Studio's own tier rule, `resolve_actor_tier_access(...).owns`:
a Business Unit's stacks and its default belong to that BU's admin; a project's selection
and its own stacks belong to that project's admin. An organisation admin's `admin:*` owns
every tier, as everywhere in Agent Studio. Reading is open to members, like every shared
Agent Studio tier; a project's view also requires seeing the project.

Every write invalidates the resolver's cache for the tenant, so the next agent turn follows
the change, and is audited.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service
from shared.authz.dependency import require_permission
from shared.routers.agent_profiles import resolve_actor_tier_access
from shared.services import tech_stack_store as store
from shared.services.tech_stack import EffectiveTechStack, TechStack, validate_stack
from shared.services.tech_stack_catalog import catalog

logger = logging.getLogger(__name__)

tech_stacks_router = APIRouter(dependencies=[Depends(require_permission("artifact:view"))])

_OWNER_WORDS = {
    "workspace": "a Business Unit admin of this Business Unit",
    "project": "a project admin of this project",
}


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return str(tid)


def _user_id(request: Request) -> str:
    return str(getattr(request.state, "user_id", "") or "")


def _perms(request: Request) -> list:
    return list(getattr(request.state, "permissions", []) or [])


def _out(s: TechStack) -> dict:
    return {"id": s.id, "scope": s.scope, "workspace_id": s.workspace_id, "project_id": s.project_id,
            "name": s.name, "description": s.description, "categories": s.categories, "notes": s.notes,
            "is_default": s.is_default}


def _effective_out(eff: EffectiveTechStack) -> dict:
    return {"stack": _out(eff.stack) if eff.stack else None, "source": eff.source, "warning": eff.warning}


def _violations(violations: list) -> HTTPException:
    return HTTPException(status_code=422, detail={"violations": violations})


async def _owns(request: Request, scope: str, scope_id) -> bool:
    owns, _ = await resolve_actor_tier_access(_tenant_id(request), _user_id(request), _perms(request),
                                              scope, str(scope_id))
    return bool(owns)


async def _assert_owner(request: Request, scope: str, scope_id) -> None:
    if not await _owns(request, scope, scope_id):
        raise HTTPException(status_code=403, detail=f"Only {_OWNER_WORDS[scope]} can change its tech stacks.")


async def _assert_project_visible(request: Request, project_id) -> None:
    from shared.authz.read_scope import is_org_wide, visible_project_ids  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    if is_org_wide(request):
        return
    async with get_db_session_for_tenant(_tenant_id(request)) as db:
        visible = await visible_project_ids(db, user_id=_user_id(request), tenant_id=_tenant_id(request))
    if visible is not None and str(project_id) not in visible:
        raise HTTPException(status_code=404, detail="not found")


async def _emit(request: Request, event_type: str, resource_id: str, payload: dict) -> None:
    await audit_service.emit(AuditEventPayload(
        tenant_id=_tenant_id(request), event_type=event_type, actor_id=_user_id(request) or None,
        resource_type="tech_stack", resource_id=str(resource_id), payload=payload,
    ))


def _changed(request: Request) -> None:
    store.invalidate_tech_stack_cache(_tenant_id(request))


class TechStackFields(BaseModel):
    name: str = Field(default="", max_length=400)
    description: Optional[str] = Field(default="", max_length=4000)
    categories: dict[str, list[str]] = Field(default_factory=dict)
    notes: Optional[str] = Field(default="", max_length=20000)


class CreateTechStackIn(TechStackFields):
    scope: str
    scope_id: str


class DefaultIn(BaseModel):
    is_default: bool


class SelectionIn(BaseModel):
    tech_stack_id: Optional[str] = None


@tech_stacks_router.get("/tech-stacks/catalog")
async def get_tech_stack_catalog():
    return catalog()


@tech_stacks_router.get("/tech-stacks")
async def list_business_unit_tech_stacks(request: Request, workspace_id: str):
    stacks = await store.list_workspace_stacks(_tenant_id(request), workspace_id)
    return {"items": [_out(s) for s in stacks], "can_manage": await _owns(request, "workspace", workspace_id)}


@tech_stacks_router.get("/projects/{project_id}/tech-stack")
async def get_project_tech_stack(project_id: str, request: Request):
    tenant = _tenant_id(request)
    exists, ws = await store.project_scope(tenant, project_id)
    if not exists:
        raise HTTPException(status_code=404, detail="not found")
    await _assert_project_visible(request, project_id)
    business_unit = await store.list_workspace_stacks(tenant, ws) if ws else []
    own = await store.list_project_stacks(tenant, project_id)
    selection = await store.get_selection(tenant, project_id)
    # Uncached: the admin looking at this page sees the truth now, not 45 s ago.
    effective = await store.resolve_project_tech_stack(tenant, project_id)
    return {
        "project_id": str(project_id),
        "workspace_id": ws,
        "options": {"business_unit": [_out(s) for s in business_unit], "project": [_out(s) for s in own]},
        "selection": {"tech_stack_id": selection},
        "effective": _effective_out(effective),
        "can_manage": await _owns(request, "project", project_id),
        "can_manage_business_unit": bool(ws) and await _owns(request, "workspace", ws),
    }


@tech_stacks_router.post("/tech-stacks", status_code=201)
async def create_tech_stack(body: CreateTechStackIn, request: Request):
    tenant = _tenant_id(request)
    if body.scope not in ("workspace", "project"):
        raise _violations([{"field": "scope", "code": "unknown_scope",
                            "message": "A tech stack belongs to a Business Unit or a project."}])
    if body.scope == "workspace":
        if not await store.workspace_exists(tenant, body.scope_id):
            raise HTTPException(status_code=404, detail="Business Unit not found")
    else:
        exists, _ = await store.project_scope(tenant, body.scope_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Project not found")
        await _assert_project_visible(request, body.scope_id)
    await _assert_owner(request, body.scope, body.scope_id)
    fields, violations = validate_stack(body.name, body.description, body.categories, body.notes)
    if violations:
        raise _violations(violations)
    try:
        stack = await store.create_stack(tenant, scope=body.scope, scope_id=body.scope_id, fields=fields,
                                         actor=_user_id(request) or "system")
    except store.DuplicateStackName:
        raise _violations([{"field": "name", "code": "duplicate_name",
                            "message": f"A tech stack named '{fields['name']}' already exists here."}])
    _changed(request)
    await _emit(request, "tech_stack.created", stack.id,
                {"scope": stack.scope, "scope_id": body.scope_id, "name": stack.name})
    return _out(stack)


async def _existing(request: Request, stack_id: str) -> TechStack:
    stack = await store.get_stack(_tenant_id(request), stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail="Tech stack not found")
    return stack


def _tier_id(stack: TechStack) -> str:
    return stack.workspace_id if stack.scope == "workspace" else stack.project_id


@tech_stacks_router.patch("/tech-stacks/{stack_id}")
async def update_tech_stack(stack_id: str, body: TechStackFields, request: Request):
    stack = await _existing(request, stack_id)
    await _assert_owner(request, stack.scope, _tier_id(stack))
    fields, violations = validate_stack(body.name, body.description, body.categories, body.notes)
    if violations:
        raise _violations(violations)
    try:
        updated = await store.update_stack(_tenant_id(request), stack_id, fields, _user_id(request) or "system")
    except store.DuplicateStackName:
        raise _violations([{"field": "name", "code": "duplicate_name",
                            "message": f"A tech stack named '{fields['name']}' already exists here."}])
    if updated is None:
        raise HTTPException(status_code=404, detail="Tech stack not found")
    _changed(request)
    await _emit(request, "tech_stack.updated", stack_id, {"name": updated.name})
    return _out(updated)


@tech_stacks_router.delete("/tech-stacks/{stack_id}")
async def delete_tech_stack(stack_id: str, request: Request):
    stack = await _existing(request, stack_id)
    await _assert_owner(request, stack.scope, _tier_id(stack))
    await store.delete_stack(_tenant_id(request), stack_id, _user_id(request) or "system")
    _changed(request)
    await _emit(request, "tech_stack.deleted", stack_id, {"name": stack.name, "was_default": stack.is_default})
    return {"deleted": True, "id": stack_id}


@tech_stacks_router.put("/tech-stacks/{stack_id}/default")
async def set_tech_stack_default(stack_id: str, body: DefaultIn, request: Request):
    stack = await _existing(request, stack_id)
    if stack.scope != "workspace":
        raise _violations([{"field": "is_default", "code": "not_business_unit",
                            "message": "Only a Business Unit's tech stacks can be its default."}])
    await _assert_owner(request, "workspace", stack.workspace_id)
    updated = await store.set_default(_tenant_id(request), stack_id, body.is_default, _user_id(request) or "system")
    if updated is None:
        raise HTTPException(status_code=404, detail="Tech stack not found")
    _changed(request)
    await _emit(request, "tech_stack.default_set", stack_id, {"is_default": body.is_default, "name": stack.name})
    return _out(updated)


@tech_stacks_router.put("/projects/{project_id}/tech-stack")
async def select_project_tech_stack(project_id: str, body: SelectionIn, request: Request):
    tenant = _tenant_id(request)
    exists, ws = await store.project_scope(tenant, project_id)
    if not exists:
        raise HTTPException(status_code=404, detail="not found")
    await _assert_project_visible(request, project_id)
    await _assert_owner(request, "project", project_id)
    if body.tech_stack_id:
        stack = await store.get_stack(tenant, body.tech_stack_id)
        if stack is None:
            raise HTTPException(status_code=404, detail="That tech stack no longer exists.")
        allowed = stack.project_id == str(project_id) or (stack.scope == "workspace" and stack.workspace_id == ws)
        if not allowed:
            raise HTTPException(status_code=409,
                                detail="That tech stack belongs to another Business Unit or project.")
    await store.set_selection(tenant, project_id, body.tech_stack_id, _user_id(request) or "system")
    _changed(request)
    await _emit(request, "project.tech_stack_selected", project_id, {"tech_stack_id": body.tech_stack_id})
    return await get_project_tech_stack(project_id, request)
```

- [ ] **Step 4: Register the router** in `backend/process_api.py`, right after line 1214:

```python
from shared.routers.tech_stacks import tech_stacks_router
app.include_router(tech_stacks_router, tags=["tech-stacks"])
```

- [ ] **Step 5: Run the tests.** `.venv/Scripts/python.exe -m pytest tests/test_tech_stacks_router.py tests/test_tech_stack_store.py tests/test_tech_stack_rules.py -q -p no:cacheprovider`. Expected: all pass.

- [ ] **Step 6: Commit.** `git add backend/shared/routers/tech_stacks.py backend/process_api.py backend/tests/test_tech_stacks_router.py && git commit -m "Tech stacks API: BU alternatives, default, project selection"`

---

### Task 5: Design agent — the stack in the generation call, the check, the chat note

**Files:**
- Modify: `backend/agents_orchestrator/design_architecture_agent/components.py` (`build_generation_prompt`, ~line 634)
- Modify: `backend/agents_orchestrator/design_architecture_agent/agents/architecture.py`:
  - add `generate_section_text` and `_tech_stack_receipt`
  - rewrite the body of `_generate_components` to call it
  - add the block to `update_response`
  - add `_with_tech_stack_note` and use it in `agent()` at the `clean_messages` line (~1679)
- Modify: `backend/agents_orchestrator/design_architecture_agent/design_architecture_agent_api.py:571,594` (the side entry point passes `real_tenant_id`)
- Test: `backend/tests/test_design_tech_stack.py`

**Interfaces:**
- Consumes: `render_for_prompt`, `check_stack_table`, `correction_note`, `annotate_stack_section`, `SOURCE_LABELS` (Task 1); `current_project_tech_stack` (Task 3).
- Produces:
  - `build_generation_prompt(requested, custom_prompt="", tech_stack="")`
  - `generate_section_text(source_label, source_text, ids, custom_prompt, existing) -> tuple[str, dict]`
  - `meta` keys: `prompt_chars`, `model_calls`, `tech_stack`, `tech_stack_source`, `tech_stack_warning`, `outside_stack`
  - `_with_tech_stack_note(messages, block) -> list`

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_design_tech_stack.py`)

```python
"""The Design agent is held to the project's tech stack where the stack is actually chosen:
in the generation tool's own model call, and on every chat turn."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from agents_orchestrator.design_architecture_agent import components as dc  # noqa: E402
from agents_orchestrator.design_architecture_agent.agents import architecture as A  # noqa: E402
from shared.services import tech_stack_store  # noqa: E402
from shared.services.tech_stack import EffectiveTechStack, TechStack  # noqa: E402

NODE = TechStack(id="s1", scope="workspace", workspace_id="w1", project_id=None, name="Node + Next.js",
                 categories={"languages": ["TypeScript"], "backend_frameworks": ["Node.js", "Express"],
                             "frontend_frameworks": ["Next.js"], "databases": ["PostgreSQL"]})
EFF = EffectiveTechStack(NODE, "bu_default")
GOOD = ("## TECHNOLOGY STACK\n\n| Layer | Technology | Version | Justification |\n|---|---|---|---|\n"
        "| Frontend | Next.js | 14 | SSR |\n| Backend | Node.js (Express) | 20 | team |\n"
        "| Database | PostgreSQL | 16 | relational |\n")
BAD = GOOD.replace("| PostgreSQL | 16 |", "| MongoDB | 7 |")


def _llm(monkeypatch, *answers):
    prompts: list[str] = []
    replies = list(answers)

    async def fake(prompt, system=""):
        prompts.append(prompt)
        return replies.pop(0)

    monkeypatch.setattr(A, "_llm_generate_async", fake)
    monkeypatch.setattr(A, "broadcast_log", lambda *a, **k: None)   # no sockets in a test
    return prompts


async def test_the_generation_call_carries_the_stack_and_the_section_says_which(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    prompts = _llm(monkeypatch, GOOD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    assert "PROJECT TECH STACK — MANDATORY" in prompts[0] and "- Frontend frameworks: Next.js" in prompts[0]
    assert meta["model_calls"] == 1 and meta["tech_stack"] == "Node + Next.js" and meta["outside_stack"] == []
    assert "Project tech stack: **Node + Next.js** (the Business Unit default)." in out


async def test_a_table_outside_the_stack_is_corrected_once(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    prompts = _llm(monkeypatch, BAD, GOOD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    assert meta["model_calls"] == 2 and "MongoDB (Database)" in prompts[1] and "CORRECTION" in prompts[1]
    assert "MongoDB" not in out and meta["outside_stack"] == []


async def test_what_is_still_outside_after_the_correction_is_flagged_not_dropped(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    _llm(monkeypatch, BAD, BAD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    assert meta["model_calls"] == 2 and meta["outside_stack"] == ["MongoDB (Database)"]
    assert "> **Outside the project's tech stack:** MongoDB (Database)." in out
    assert "MongoDB (Database)" in A._tech_stack_receipt(meta)


async def test_without_a_stack_the_call_is_exactly_as_before(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=None))
    prompts = _llm(monkeypatch, BAD)
    out, meta = await A.generate_section_text("document content", "BRD", ["stack"], "", "")
    expected = (dc.build_generation_prompt(["stack"], custom_prompt="")
                + "\n--- DOCUMENT CONTENT (the source of requirements) ---\nBRD\n"
                + dc.existing_sections_note("", ["stack"]))
    assert prompts == [expected] and out == BAD and meta["model_calls"] == 1 and meta["tech_stack"] is None
    assert A._tech_stack_receipt(meta) == ""


async def test_other_sections_get_the_stack_but_no_table_check(monkeypatch):
    monkeypatch.setattr(tech_stack_store, "current_project_tech_stack", AsyncMock(return_value=EFF))
    prompts = _llm(monkeypatch, "## HIGH-LEVEL DESIGN\nuses MongoDB")
    out, meta = await A.generate_section_text("document content", "BRD", ["hld"], "", "")
    assert "PROJECT TECH STACK — MANDATORY" in prompts[0] and meta["model_calls"] == 1


def test_the_chat_note_joins_the_system_message_without_a_second_one():
    msgs = [SystemMessage(content="You are the Design agent."), HumanMessage(content="hi")]
    out = A._with_tech_stack_note(msgs, "PROJECT TECH STACK — MANDATORY\n- Languages: Go")
    assert len(out) == 2 and isinstance(out[0], SystemMessage)
    assert out[0].content.endswith("- Languages: Go") and msgs[0].content == "You are the Design agent."
    assert A._with_tech_stack_note(msgs, "") is msgs
    bare = A._with_tech_stack_note([HumanMessage(content="hi")], "BLOCK")
    assert isinstance(bare[0], SystemMessage) and bare[0].content == "BLOCK"
```

- [ ] **Step 2: Run to verify it fails.** Run `.venv/Scripts/python.exe -m pytest tests/test_design_tech_stack.py -q -p no:cacheprovider`. Expected: `AttributeError: generate_section_text`.

- [ ] **Step 3: `components.build_generation_prompt` takes the block.** Change its signature and head:

```python
def build_generation_prompt(requested: Iterable[str], custom_prompt: str = "", tech_stack: str = "") -> str:
    """The generation prompt for exactly these components. Raises on an unknown one.

    `tech_stack` is the project's mandatory tech-stack block (`tech_stack.render_for_prompt`),
    placed after the grounding and before the templates; '' leaves the prompt as it was."""
```

and after `head = _GROUNDING.format(...)` add:

```python
    if tech_stack:
        head = head + "\n" + tech_stack + "\n"
```

- [ ] **Step 4: `architecture.py`.** Add `generate_section_text` and `_tech_stack_receipt` above `_generate_components`, and make `_generate_components` use them:

```python
def _tech_stack_receipt(meta: dict) -> str:
    """What the agent tells the user about the stack the sections were held to."""
    from shared.services.tech_stack import SOURCE_LABELS  # noqa: PLC0415

    lines = []
    if meta.get("tech_stack"):
        source = SOURCE_LABELS.get(meta.get("tech_stack_source") or "", meta.get("tech_stack_source") or "")
        lines.append(f"Tech stack applied: {meta['tech_stack']} ({source}).")
    if meta.get("tech_stack_warning"):
        lines.append(f"Tech stack note for the user: {meta['tech_stack_warning']}")
    if meta.get("outside_stack"):
        lines.append("Tell the user: after one correction the technology stack table still names technologies "
                     "outside the project's tech stack — " + ", ".join(meta["outside_stack"])
                     + ". They are flagged in the document.")
    return ("\n".join(lines) + "\n\n") if lines else ""


async def generate_section_text(
    source_label: str, source_text: str, ids: List[str], custom_prompt: str, existing: str,
) -> "tuple[str, dict]":
    """The model call behind every generate tool: these sections, held to the project's tech stack.

    THE STACK IS CHOSEN HERE, in this call's own prompt — not in the chat, whose skills and
    instructions never reached it. When the Technology Stack section is produced, its table is
    checked against the stack; anything outside is corrected once, and what survives the
    correction is flagged in the document and reported, never dropped silently.
    Returns (sections markdown, facts about the call)."""
    from shared.services import tech_stack_store  # noqa: PLC0415
    from shared.services.tech_stack import (  # noqa: PLC0415
        annotate_stack_section, check_stack_table, correction_note, render_for_prompt,
    )

    eff = await tech_stack_store.current_project_tech_stack()
    block = render_for_prompt(eff)

    def _prompt(section_ids: List[str], extra: str, current: str) -> str:
        return (_components.build_generation_prompt(section_ids, custom_prompt=extra, tech_stack=block)
                + f"\n--- {source_label.upper()} (the source of requirements) ---\n{source_text}\n"
                + _components.existing_sections_note(current, section_ids))

    prompt = _prompt(ids, custom_prompt, existing)
    result = await _llm_generate_async(prompt, _GENERATION_SYSTEM)
    calls = 1
    violations: list = []
    if block and "stack" in ids:
        violations = check_stack_table(eff, result)
        if violations:
            broadcast_log(manager, "The technology stack table named technologies outside the project's "
                                   "tech stack — correcting it once...", level="INFO")
            extra = (custom_prompt + "\n\n" if custom_prompt else "") + correction_note(violations)
            so_far = _components.merge_sections(existing, result) if existing.strip() else result
            fixed = await _llm_generate_async(_prompt(["stack"], extra, so_far), _GENERATION_SYSTEM)
            calls += 1
            result = _components.merge_sections(result, fixed)
            violations = check_stack_table(eff, result)
        result = annotate_stack_section(result, eff, violations)
    meta = {
        "prompt_chars": len(prompt) + len(_GENERATION_SYSTEM),
        "model_calls": calls,
        "tech_stack": eff.stack.name if eff and eff.stack else None,
        "tech_stack_source": eff.source if eff else "none",
        "tech_stack_warning": eff.warning if eff else None,
        "outside_stack": [f"{v.technology} ({v.layer})" if v.layer else v.technology for v in violations],
    }
    return result, meta
```

In `_generate_components`, replace the `prompt = (...)` assignment and the `result = await _llm_generate_async(prompt, _GENERATION_SYSTEM)` line with:

```python
    result, meta = await generate_section_text(source_label, source_text, ids, custom_prompt, existing)
```

and its final `return _with_save_receipt(receipt, document)` with:

```python
    return _tech_stack_receipt(meta) + _with_save_receipt(receipt, document)
```

- [ ] **Step 5: `update_response` keeps edits inside the stack.** Before `prompt = f"""Update ...`, add:

```python
    from shared.services import tech_stack_store  # noqa: PLC0415
    from shared.services.tech_stack import render_for_prompt  # noqa: PLC0415

    stack_block = render_for_prompt(await tech_stack_store.current_project_tech_stack())
    stack_rule = f"\n\n{stack_block}\n" if stack_block else ""
```

and change the prompt's tail from `CONTENT TO UPDATE:\n{content}{extra_context}\n\nReturn only the updated content.` to include `{stack_rule}` right before `Return only the updated content.`.

- [ ] **Step 6: The chat note on every turn.** Add near `_sanitize_messages`:

```python
def _with_tech_stack_note(messages: list, block: str) -> list:
    """The project's tech stack joined to the system message sent THIS turn, never stored.

    The chat's SystemMessage is built once, on a session's first turn; a stack chosen or
    changed afterwards would never reach it. Joining (not adding a second system message) keeps
    providers that accept one system message happy."""
    if not block:
        return messages
    if messages and isinstance(messages[0], SystemMessage) and isinstance(messages[0].content, str):
        return [SystemMessage(content=f"{messages[0].content}\n\n{block}")] + list(messages[1:])
    return [SystemMessage(content=block)] + list(messages)
```

In `agent()`, replace `clean_messages = _sanitize_messages(state["messages"])` with:

```python
        from shared.services import tech_stack_store  # noqa: PLC0415
        from shared.services.tech_stack import render_for_prompt  # noqa: PLC0415

        clean_messages = _with_tech_stack_note(
            _sanitize_messages(state["messages"]),
            render_for_prompt(await tech_stack_store.current_project_tech_stack()),
        )
```

Make sure `SystemMessage` is imported in `architecture.py`. Add it to the existing `from langchain_core.messages import ...` line if it's missing.

- [ ] **Step 7: The side entry point passes its tenant.** In `design_architecture_agent_api.py`:
  - line ~571: `resolve_agent_turn("design", sys_content, None, _lf_pid)` becomes `resolve_agent_turn("design", sys_content, real_tenant_id or None, _lf_pid)`.
  - line ~594: `resolve_agent_skills("design", None, _lf_pid)` becomes `resolve_agent_skills("design", real_tenant_id or None, _lf_pid)`.
  - Update the stale comment above line 571: this endpoint does have the request's tenant.

- [ ] **Step 8: Run the tests.** `.venv/Scripts/python.exe -m pytest tests/test_design_tech_stack.py tests/test_design_components.py tests/test_tech_stack_rules.py -q -p no:cacheprovider`. Expected: all pass.

- [ ] **Step 9: Commit.** `git add backend/agents_orchestrator/design_architecture_agent backend/tests/test_design_tech_stack.py && git commit -m "Design agent follows the project's tech stack in generation and chat"`

---

### Task 6: Frontend data layer — schemas, API, query keys, proxy routes

**Files:**
- Create: `frontend/lib/schemas/tech-stacks.ts`, `frontend/lib/api/tech-stacks.ts`
- Modify: `frontend/lib/api/query-keys.ts` (add `techStacks`)
- Create proxy routes:
  - `frontend/app/api/tech-stacks/route.ts`
  - `frontend/app/api/tech-stacks/catalog/route.ts`
  - `frontend/app/api/tech-stacks/[id]/route.ts`
  - `frontend/app/api/tech-stacks/[id]/default/route.ts`
  - `frontend/app/api/projects/[id]/tech-stack/route.ts`
- Test: `frontend/lib/api/__tests__/tech-stacks.test.ts`

**Interfaces:**
- Produces:
  - Types: `TechStack`, `TechStackCatalog`, `BusinessUnitTechStacks`, `ProjectTechStack`, `EffectiveTechStack`, `TechStackFieldsInput`, `TechStackCreateInput`, `SOURCE_LABEL`
  - Calls: `getTechStackCatalog()`, `listBusinessUnitTechStacks(workspaceId)`, `getProjectTechStack(projectId)`, `createTechStack(input)`, `updateTechStack(id, input)`, `deleteTechStack(id)`, `setDefaultTechStack(id, isDefault)`, `selectProjectTechStack(projectId, techStackId|null)`
  - Keys: `qk.techStacks.{all, catalog, businessUnit(ws), project(p)}`

- [ ] **Step 1: Write the failing test** (`frontend/lib/api/__tests__/tech-stacks.test.ts`)

```ts
import { describe, expect, it } from "vitest";

import { ProjectTechStack, TechStack } from "@/lib/schemas/tech-stacks";

const stack = { id: "s1", scope: "workspace", workspace_id: "w1", project_id: null, name: "Node + Next.js",
  description: "", categories: { languages: ["TypeScript"] }, notes: "", is_default: true };

describe("tech stack schemas", () => {
  it("parses the backend's stack and project view", () => {
    expect(TechStack.parse(stack).categories.languages).toEqual(["TypeScript"]);
    const view = ProjectTechStack.parse({
      project_id: "p1", workspace_id: "w1",
      options: { business_unit: [stack], project: [] },
      selection: { tech_stack_id: null },
      effective: { stack, source: "bu_default", warning: null },
      can_manage: true, can_manage_business_unit: false,
    });
    expect(view.effective.source).toBe("bu_default");
  });

  it("refuses a source it does not know", () => {
    expect(() => ProjectTechStack.parse({ project_id: "p", workspace_id: null, options: { business_unit: [], project: [] },
      selection: { tech_stack_id: null }, effective: { stack: null, source: "org", warning: null } })).toThrow();
  });
});
```

- [ ] **Step 2: Run to verify it fails.** Run `cd frontend && npx vitest run lib/api/__tests__/tech-stacks.test.ts`. Expected: FAIL (module not found).

- [ ] **Step 3: Schemas** (`frontend/lib/schemas/tech-stacks.ts`)

```ts
import { z } from "zod";

/** Agent Studio tech stacks — backend `shared/routers/tech_stacks.py` (snake_case, like agent-skills). */

export const TechStackCategory = z.object({
  id: z.string(),
  label: z.string(),
  suggestions: z.array(z.string()).default([]),
});
export const TechStackCatalog = z.object({ categories: z.array(TechStackCategory) });
export type TechStackCatalog = z.infer<typeof TechStackCatalog>;

export const TechStack = z.object({
  id: z.string(),
  scope: z.enum(["workspace", "project"]),
  workspace_id: z.string().nullable(),
  project_id: z.string().nullable(),
  name: z.string(),
  description: z.string().default(""),
  categories: z.record(z.string(), z.array(z.string())).default({}),
  notes: z.string().default(""),
  is_default: z.boolean().default(false),
});
export type TechStack = z.infer<typeof TechStack>;

export const BusinessUnitTechStacks = z.object({
  items: z.array(TechStack),
  can_manage: z.boolean().default(false),
});
export type BusinessUnitTechStacks = z.infer<typeof BusinessUnitTechStacks>;

export const EffectiveTechStack = z.object({
  stack: TechStack.nullable(),
  source: z.enum(["project_selection", "bu_default", "none"]),
  warning: z.string().nullable().default(null),
});
export type EffectiveTechStack = z.infer<typeof EffectiveTechStack>;

export const ProjectTechStack = z.object({
  project_id: z.string(),
  workspace_id: z.string().nullable(),
  options: z.object({
    business_unit: z.array(TechStack).default([]),
    project: z.array(TechStack).default([]),
  }),
  selection: z.object({ tech_stack_id: z.string().nullable() }),
  effective: EffectiveTechStack,
  can_manage: z.boolean().default(false),
  can_manage_business_unit: z.boolean().default(false),
});
export type ProjectTechStack = z.infer<typeof ProjectTechStack>;

export const TechStackDeleted = z.object({ deleted: z.boolean(), id: z.string() });

export interface TechStackFieldsInput {
  name: string;
  description: string;
  categories: Record<string, string[]>;
  notes: string;
}
export interface TechStackCreateInput extends TechStackFieldsInput {
  scope: "workspace" | "project";
  scope_id: string;
}

export const SOURCE_LABEL: Record<EffectiveTechStack["source"], string> = {
  project_selection: "Chosen for this project",
  bu_default: "Business Unit default",
  none: "Not set",
};
```

- [ ] **Step 4: API calls** (`frontend/lib/api/tech-stacks.ts`)

```ts
import {
  BusinessUnitTechStacks,
  ProjectTechStack,
  TechStack,
  TechStackCatalog,
  type TechStackCreateInput,
  TechStackDeleted,
  type TechStackFieldsInput,
} from "@/lib/schemas/tech-stacks";

import { api } from "./client";

export const getTechStackCatalog = () => api("/tech-stacks/catalog", { schema: TechStackCatalog });

export const listBusinessUnitTechStacks = (workspaceId: string) =>
  api("/tech-stacks", { query: { workspace_id: workspaceId }, schema: BusinessUnitTechStacks });

export const getProjectTechStack = (projectId: string) =>
  api(`/projects/${encodeURIComponent(projectId)}/tech-stack`, { schema: ProjectTechStack });

/** 422 carries `violations` — read them with `getLintViolations(err)` (lib/api/agent-profiles). */
export const createTechStack = (input: TechStackCreateInput) =>
  api("/tech-stacks", { method: "POST", body: input, schema: TechStack });

export const updateTechStack = (id: string, input: TechStackFieldsInput) =>
  api(`/tech-stacks/${encodeURIComponent(id)}`, { method: "PATCH", body: input, schema: TechStack });

export const deleteTechStack = (id: string) =>
  api(`/tech-stacks/${encodeURIComponent(id)}`, { method: "DELETE", schema: TechStackDeleted });

export const setDefaultTechStack = (id: string, isDefault: boolean) =>
  api(`/tech-stacks/${encodeURIComponent(id)}/default`, {
    method: "PUT", body: { is_default: isDefault }, schema: TechStack,
  });

/** `null` = follow the Business Unit default. */
export const selectProjectTechStack = (projectId: string, techStackId: string | null) =>
  api(`/projects/${encodeURIComponent(projectId)}/tech-stack`, {
    method: "PUT", body: { tech_stack_id: techStackId }, schema: ProjectTechStack,
  });
```

- [ ] **Step 5: Query keys.** In `frontend/lib/api/query-keys.ts`, add next to `agentSkills`:

```ts
  techStacks: {
    /** Every tech-stack query — invalidated whole after any write. */
    all: ["tech-stacks"] as const,
    catalog: () => ["tech-stacks", "catalog"] as const,
    businessUnit: (workspaceId: string) => ["tech-stacks", "business-unit", workspaceId] as const,
    project: (projectId: string) => ["tech-stacks", "project", projectId] as const,
  },
```

- [ ] **Step 6: Proxy routes.** All mirror `app/api/agent-skills/route.ts`. Writes forward the raw 422 body so `violations` survive.

`frontend/app/api/tech-stacks/route.ts`:

```ts
import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";
import { BusinessUnitTechStacks, TechStack } from "@/lib/schemas/tech-stacks";

/** A Business Unit's tech stacks (query: workspace_id). */
export function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(search ? `/tech-stacks?${search}` : "/tech-stacks", { schema: BusinessUnitTechStacks });
}

/** Create — forwards the raw 422 body so `violations` reach the editor (see agent-skills/route.ts). */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const body: unknown = await req.json();
  try {
    return Response.json(await bffFetch("/tech-stacks", { session, method: "POST", body, schema: TechStack }), { status: 201 });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.rawBody ?? err.details ?? { code: err.code, message: err.message }, { status: err.status });
    }
    throw err;
  }
}
```

`frontend/app/api/tech-stacks/catalog/route.ts`:

```ts
import { bffProxy } from "@/lib/bff/proxy";
import { TechStackCatalog } from "@/lib/schemas/tech-stacks";

export function GET() {
  return bffProxy("/tech-stacks/catalog", { schema: TechStackCatalog });
}
```

`frontend/app/api/tech-stacks/[id]/route.ts`:

```ts
import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";
import { TechStack, TechStackDeleted } from "@/lib/schemas/tech-stacks";

type Ctx = { params: Promise<{ id: string }> };

export async function PATCH(req: NextRequest, { params }: Ctx) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const body: unknown = await req.json();
  try {
    return Response.json(await bffFetch(`/tech-stacks/${encodeURIComponent(id)}`, { session, method: "PATCH", body, schema: TechStack }));
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.rawBody ?? err.details ?? { code: err.code, message: err.message }, { status: err.status });
    }
    throw err;
  }
}

export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return bffProxy(`/tech-stacks/${encodeURIComponent(id)}`, { method: "DELETE", schema: TechStackDeleted });
}
```

`frontend/app/api/tech-stacks/[id]/default/route.ts`:

```ts
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { TechStack } from "@/lib/schemas/tech-stacks";

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/tech-stacks/${encodeURIComponent(id)}/default`, { method: "PUT", body: await req.json(), schema: TechStack });
}
```

`frontend/app/api/projects/[id]/tech-stack/route.ts`:

```ts
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ProjectTechStack } from "@/lib/schemas/tech-stacks";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/tech-stack`, { schema: ProjectTechStack });
}

export async function PUT(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/tech-stack`, { method: "PUT", body: await req.json(), schema: ProjectTechStack });
}
```

- [ ] **Step 7: Run the test and type-check.** `npx vitest run lib/api/__tests__/tech-stacks.test.ts && npx tsc --noEmit -p .`. Expected: pass, clean.

- [ ] **Step 8: Commit.** `git add frontend/lib/schemas/tech-stacks.ts frontend/lib/api/tech-stacks.ts frontend/lib/api/query-keys.ts frontend/app/api/tech-stacks frontend/app/api/projects/[id]/tech-stack frontend/lib/api/__tests__/tech-stacks.test.ts && git commit -m "Tech stacks: client schemas, API calls and proxy routes"`

---

### Task 7: Agent Studio panel, editor, and the Design page chip

**Files:**
- Create: `frontend/components/agent-studio/tech-stack-panel.tsx`, `frontend/components/agent-studio/tech-stack-editor.tsx`
- Modify: `frontend/components/agent-studio/agent-editor.tsx`:
  - render `<TechStackPanel scopeContext={scopeContext} />` in the `skills` tab, above `<SkillsTab>`
  - honour `?tab=skills`
- Create: `frontend/components/app/tech-stack-chip.tsx`
- Modify: `frontend/app/(app)/projects/[id]/design/page.tsx` (the chip before `<ModelSelector>` in the header, ~line 246)
- Test: `frontend/components/agent-studio/__tests__/tech-stack-panel.test.tsx`, `frontend/components/app/__tests__/tech-stack-chip.test.tsx`

**Interfaces:**
- Consumes: Task 6's API calls and keys; `ScopeContext` from `agent-editor.tsx`; `getLintViolations` from `lib/api/agent-profiles.ts`.
- Produces: `TechStackPanel({ scopeContext })`, `TechStackEditor(props)`, `TechStackDetails({ stack, catalog })`, `TechStackChip({ projectId })`.

- [ ] **Step 1: Write the failing panel test** (`frontend/components/agent-studio/__tests__/tech-stack-panel.test.tsx`)

```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const api = vi.hoisted(() => ({
  getTechStackCatalog: vi.fn(),
  listBusinessUnitTechStacks: vi.fn(),
  getProjectTechStack: vi.fn(),
  selectProjectTechStack: vi.fn(),
  setDefaultTechStack: vi.fn(),
  deleteTechStack: vi.fn(),
  createTechStack: vi.fn(),
  updateTechStack: vi.fn(),
}));
vi.mock("@/lib/api/tech-stacks", () => api);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TechStackPanel } from "@/components/agent-studio/tech-stack-panel";

const CATALOG = { categories: [
  { id: "languages", label: "Languages", suggestions: ["Java", "TypeScript"] },
  { id: "backend_frameworks", label: "Backend frameworks", suggestions: [] },
] };
const node = { id: "s-node", scope: "workspace", workspace_id: "w1", project_id: null, name: "Node + Next.js",
  description: "Web apps", categories: { languages: ["TypeScript"] }, notes: "", is_default: true };
const java = { ...node, id: "s-java", name: "Java + Spring Boot", is_default: false, categories: { languages: ["Java"] } };

function ctx(scope: string, scopeId: string | null, over: Record<string, unknown> = {}) {
  return { scope, scopeId, scopeLabel: "X", chain: { workspaceId: "w1", projectId: scope === "project" ? "p1" : null, userId: null },
    isOwner: false, canPropose: false, ownerRoleLabel: null, ...over } as never;
}

function renderPanel(context: never) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><TechStackPanel scopeContext={context} /></QueryClientProvider>);
}

beforeEach(() => { api.getTechStackCatalog.mockResolvedValue(CATALOG); });
afterEach(() => { cleanup(); Object.values(api).forEach((f) => f.mockReset()); });

describe("TechStackPanel", () => {
  it("at the organisation tier, says where stacks are set", () => {
    renderPanel(ctx("org", null));
    expect(screen.getByText(/offered by each Business Unit and chosen per project/i)).toBeInTheDocument();
  });

  it("shows a Business Unit's stacks with the default, and lets its admin manage them", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [node, java], can_manage: true });
    renderPanel(ctx("workspace", "w1"));
    expect(await screen.findByText("Node + Next.js")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New tech stack" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make Java + Spring Boot the default" }));
    await waitFor(() => expect(api.setDefaultTechStack).toHaveBeenCalledWith("s-java", true));
  });

  it("is read-only for someone who does not own the Business Unit", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [node], can_manage: false });
    renderPanel(ctx("workspace", "w1"));
    expect(await screen.findByText("Node + Next.js")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New tech stack" })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it("lets a project admin pick exactly one stack, and says what agents follow", async () => {
    api.getProjectTechStack.mockResolvedValue({ project_id: "p1", workspace_id: "w1",
      options: { business_unit: [node, java], project: [] }, selection: { tech_stack_id: null },
      effective: { stack: node, source: "bu_default", warning: null }, can_manage: true, can_manage_business_unit: false });
    api.selectProjectTechStack.mockResolvedValue({ project_id: "p1", workspace_id: "w1",
      options: { business_unit: [node, java], project: [] }, selection: { tech_stack_id: "s-java" },
      effective: { stack: java, source: "project_selection", warning: null }, can_manage: true, can_manage_business_unit: false });
    renderPanel(ctx("project", "p1"));
    expect(await screen.findByText(/Agents follow/)).toHaveTextContent("Node + Next.js");
    expect(screen.getByRole("radio", { name: /Follow the Business Unit default/ })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /Java \+ Spring Boot/ }));
    await waitFor(() => expect(api.selectProjectTechStack).toHaveBeenCalledWith("p1", "s-java"));
  });

  it("warns when the chosen stack no longer stands", async () => {
    api.getProjectTechStack.mockResolvedValue({ project_id: "p1", workspace_id: "w1",
      options: { business_unit: [node], project: [] }, selection: { tech_stack_id: "gone" },
      effective: { stack: node, source: "bu_default", warning: "The tech stack chosen for this project was deleted." },
      can_manage: false, can_manage_business_unit: false });
    renderPanel(ctx("project", "p1"));
    expect(await screen.findByText(/was deleted/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Follow the Business Unit default/ })).toBeDisabled();
  });

  it("opens a stack to read everything in it", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [node], can_manage: false });
    renderPanel(ctx("workspace", "w1"));
    fireEvent.click(await screen.findByRole("button", { name: "View Node + Next.js" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("TypeScript");
  });

  it("creates a stack from the editor and shows the server's reasons on their fields", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [], can_manage: true });
    const { ApiRequestError } = await import("@/lib/api/client");
    api.createTechStack.mockRejectedValueOnce(new ApiRequestError(422, {
      detail: { violations: [{ field: "name", code: "name_length", message: "A name is 3–80 characters." }] },
    }));
    renderPanel(ctx("workspace", "w1"));
    fireEvent.click(await screen.findByRole("button", { name: "New tech stack" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "x" } });
    const languages = screen.getByLabelText("Languages");
    fireEvent.change(languages, { target: { value: "Java" } });
    fireEvent.keyDown(languages, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("A name is 3–80 characters.")).toBeInTheDocument();
    expect(api.createTechStack).toHaveBeenCalledWith({
      name: "x", description: "", notes: "", categories: { languages: ["Java"] }, scope: "workspace", scope_id: "w1",
    });
  });
});
```

- [ ] **Step 2: Run to verify it fails.** Run `npx vitest run components/agent-studio/__tests__/tech-stack-panel.test.tsx`. Expected: FAIL (module not found).

- [ ] **Step 3: Write the editor** (`frontend/components/agent-studio/tech-stack-editor.tsx`)

```tsx
"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { getLintViolations } from "@/lib/api/agent-profiles";
import { createTechStack, updateTechStack } from "@/lib/api/tech-stacks";
import { qk } from "@/lib/api/query-keys";
import type { TechStack, TechStackCatalog } from "@/lib/schemas/tech-stacks";

export type TechStackEditorMode =
  | { kind: "create"; scope: "workspace" | "project"; scopeId: string; where: string }
  | { kind: "edit"; stack: TechStack };

/** One category's items: chips, type to add (Enter or comma), suggestions from the catalogue. */
function ChipInput({ id, label, items, suggestions, onChange, error }: {
  id: string; label: string; items: string[]; suggestions: string[];
  onChange: (next: string[]) => void; error?: string;
}) {
  const [draft, setDraft] = React.useState("");
  const add = (raw: string) => {
    const value = raw.replace(/,$/, "").trim();
    setDraft("");
    if (!value || items.some((i) => i.toLowerCase() === value.toLowerCase())) return;
    onChange([...items, value]);
  };
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex flex-wrap items-center gap-1.5 rounded-md border px-2 py-1.5">
        {items.map((item) => (
          <span key={item} className="bg-muted inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs">
            {item}
            <button type="button" aria-label={`Remove ${item}`} onClick={() => onChange(items.filter((i) => i !== item))}
              className="text-muted-foreground hover:text-foreground">
              <X className="size-3" aria-hidden />
            </button>
          </span>
        ))}
        <input
          id={id}
          list={`${id}-suggestions`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(draft); }
            else if (e.key === "Backspace" && !draft && items.length) onChange(items.slice(0, -1));
          }}
          onBlur={() => add(draft)}
          placeholder={items.length ? "" : "Type and press Enter"}
          className="min-w-[9rem] flex-1 bg-transparent py-0.5 text-sm outline-none"
        />
        <datalist id={`${id}-suggestions`}>
          {suggestions.filter((s) => !items.includes(s)).map((s) => <option key={s} value={s} />)}
        </datalist>
      </div>
      {error && <p className="text-destructive text-xs">{error}</p>}
    </div>
  );
}

export function TechStackEditor({ open, onOpenChange, mode, catalog }: {
  open: boolean; onOpenChange: (open: boolean) => void; mode: TechStackEditorMode; catalog: TechStackCatalog | undefined;
}) {
  const queryClient = useQueryClient();
  const initial = mode.kind === "edit" ? mode.stack : null;
  const [name, setName] = React.useState(initial?.name ?? "");
  const [description, setDescription] = React.useState(initial?.description ?? "");
  const [notes, setNotes] = React.useState(initial?.notes ?? "");
  const [categories, setCategories] = React.useState<Record<string, string[]>>(initial?.categories ?? {});
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: () => {
      const fields = { name, description, notes, categories };
      return mode.kind === "edit"
        ? updateTechStack(mode.stack.id, fields)
        : createTechStack({ ...fields, scope: mode.scope, scope_id: mode.scopeId });
    },
    onSuccess: async (stack) => {
      toast.success(mode.kind === "edit" ? `Saved ${stack.name}` : `Created ${stack.name}`);
      await queryClient.invalidateQueries({ queryKey: qk.techStacks.all });
      onOpenChange(false);
    },
    onError: (err) => {
      const violations = getLintViolations(err);
      if (violations) {
        setErrors(Object.fromEntries(violations.map((v) => [v.field, v.message])));
        return;
      }
      toast.error(err instanceof Error ? err.message : "The tech stack could not be saved.");
    },
  });

  const categoryError = errors["categories"];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode.kind === "edit" ? `Edit ${mode.stack.name}` : "New tech stack"}</DialogTitle>
          <DialogDescription>
            {mode.kind === "edit"
              ? "Changes apply to the next thing an agent generates."
              : `For ${mode.where}. Agents follow it on projects that use it.`}
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setErrors({}); save.mutate(); }}>
          <div className="space-y-1.5">
            <Label htmlFor="ts-name">Name</Label>
            <Input id="ts-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Java + Spring Boot" />
            {errors["name"] && <p className="text-destructive text-xs">{errors["name"]}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ts-description">Description</Label>
            <Input id="ts-description" value={description} onChange={(e) => setDescription(e.target.value)}
              placeholder="When to use this stack" />
            {errors["description"] && <p className="text-destructive text-xs">{errors["description"]}</p>}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {(catalog?.categories ?? []).map((c) => (
              <ChipInput key={c.id} id={`ts-${c.id}`} label={c.label} items={categories[c.id] ?? []}
                suggestions={c.suggestions} error={errors[`categories.${c.id}`]}
                onChange={(next) => setCategories((cur) => ({ ...cur, [c.id]: next }))} />
            ))}
          </div>
          {categoryError && <p className="text-destructive text-xs">{categoryError}</p>}
          <div className="space-y-1.5">
            <Label htmlFor="ts-notes">Notes</Label>
            <Textarea id="ts-notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={3}
              placeholder="Conventions, versions, what to avoid" />
            {errors["notes"] && <p className="text-destructive text-xs">{errors["notes"]}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              {mode.kind === "edit" ? "Save" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 4: Write the panel** (`frontend/components/agent-studio/tech-stack-panel.tsx`)

```tsx
"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Eye, Layers, Loader2, Lock, Pencil, Plus, Star, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteTechStack, getProjectTechStack, getTechStackCatalog, listBusinessUnitTechStacks,
  selectProjectTechStack, setDefaultTechStack,
} from "@/lib/api/tech-stacks";
import { qk } from "@/lib/api/query-keys";
import { SOURCE_LABEL, type TechStack, type TechStackCatalog } from "@/lib/schemas/tech-stacks";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

import type { ScopeContext } from "./agent-editor";
import { TechStackEditor, type TechStackEditorMode } from "./tech-stack-editor";

/**
 * TECH STACK · ALL AGENTS — the one choice every agent on a project designs and builds with.
 *
 * A Business Unit offers stacks as alternatives and marks one its default; a project follows
 * that default or picks another, or keeps one of its own. Exactly one applies. The panel is
 * the same on every agent's Skills tab because the choice is the project's, not an agent's.
 * Who may change what is the backend's answer (`can_manage`), Agent Studio's tier ownership.
 */
export function TechStackPanel({ scopeContext }: { scopeContext: ScopeContext }) {
  const catalogQ = useQuery({ queryKey: qk.techStacks.catalog(), queryFn: getTechStackCatalog, staleTime: 10 * 60_000 });
  const { scope, scopeId, chain } = scopeContext;

  let body: React.ReactNode;
  if (scope === "workspace" && scopeId) {
    body = <BusinessUnitStacks workspaceId={scopeId} where={scopeContext.scopeLabel} catalog={catalogQ.data} />;
  } else if ((scope === "project" && scopeId) || (scope === "user" && chain.projectId)) {
    body = <ProjectStacks projectId={(scope === "project" ? scopeId : chain.projectId) as string}
      where={scopeContext.projectName ?? scopeContext.scopeLabel} catalog={catalogQ.data} />;
  } else {
    body = (
      <p className="text-muted-foreground text-sm">
        Tech stacks are offered by each {BUSINESS_UNIT_LABEL} and chosen per project. Open a {BUSINESS_UNIT_LABEL} or a
        project to see them.
      </p>
    );
  }

  return (
    <section aria-labelledby="tech-stack-heading" className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <Layers className="text-primary size-4" aria-hidden />
        <h3 id="tech-stack-heading" className="text-sm font-semibold">Tech stack · all agents</h3>
      </div>
      {body}
    </section>
  );
}

function StackChips({ stack, limit }: { stack: TechStack; limit?: number }) {
  const items = Object.values(stack.categories).flat();
  const shown = limit ? items.slice(0, limit) : items;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((item) => <Badge key={item} variant="secondary" className="font-normal">{item}</Badge>)}
      {limit && items.length > limit && <span className="text-muted-foreground text-xs">+{items.length - limit} more</span>}
    </div>
  );
}

export function TechStackDetails({ stack, catalog }: { stack: TechStack; catalog: TechStackCatalog | undefined }) {
  const labels = new Map((catalog?.categories ?? []).map((c) => [c.id, c.label]));
  return (
    <div className="space-y-3">
      {stack.description && <p className="text-muted-foreground text-sm">{stack.description}</p>}
      <dl className="space-y-2">
        {Object.entries(stack.categories).map(([cid, items]) => (
          <div key={cid} className="grid gap-1 sm:grid-cols-[11rem_1fr]">
            <dt className="text-muted-foreground text-xs font-medium uppercase tracking-wide">{labels.get(cid) ?? cid}</dt>
            <dd className="flex flex-wrap gap-1">{items.map((i) => <Badge key={i} variant="secondary" className="font-normal">{i}</Badge>)}</dd>
          </div>
        ))}
      </dl>
      {stack.notes && (
        <div>
          <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">Notes</p>
          <p className="text-sm whitespace-pre-wrap">{stack.notes}</p>
        </div>
      )}
    </div>
  );
}

function useTechStackWrites() {
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: qk.techStacks.all });
  const fail = (err: unknown) => toast.error(err instanceof Error ? err.message : "That change could not be saved.");
  const setDefault = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) => setDefaultTechStack(id, on),
    onSuccess: (s) => { toast.success(s.is_default ? `${s.name} is now the default` : `${s.name} is no longer the default`); void refresh(); },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (s: TechStack) => deleteTechStack(s.id),
    onSuccess: (_r, s) => { toast.success(`Deleted ${s.name}`); void refresh(); },
    onError: fail,
  });
  return { setDefault, remove };
}

function StackRowActions({ stack, canManage, onView, onEdit, onDelete, extra }: {
  stack: TechStack; canManage: boolean; onView: () => void; onEdit: () => void; onDelete: () => void; extra?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button size="sm" variant="ghost" className="h-8" onClick={onView} aria-label={`View ${stack.name}`}>
        <Eye className="size-4" aria-hidden />
      </Button>
      {canManage && (
        <>
          {extra}
          <Button size="sm" variant="ghost" className="h-8" onClick={onEdit} aria-label={`Edit ${stack.name}`}>
            <Pencil className="size-4" aria-hidden />
          </Button>
          <Button size="sm" variant="ghost" className="h-8" onClick={onDelete} aria-label={`Delete ${stack.name}`}>
            <Trash2 className="size-4" aria-hidden />
          </Button>
        </>
      )}
    </div>
  );
}

function useDialogs(catalog: TechStackCatalog | undefined) {
  const [viewing, setViewing] = React.useState<TechStack | null>(null);
  const [editing, setEditing] = React.useState<TechStackEditorMode | null>(null);
  const [deleting, setDeleting] = React.useState<TechStack | null>(null);
  const { remove } = useTechStackWrites();
  const dialogs = (
    <>
      <Dialog open={!!viewing} onOpenChange={(o) => !o && setViewing(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>{viewing?.name}</DialogTitle></DialogHeader>
          {viewing && <TechStackDetails stack={viewing} catalog={catalog} />}
        </DialogContent>
      </Dialog>
      {editing && (
        <TechStackEditor key={editing.kind === "edit" ? editing.stack.id : "new"} open mode={editing} catalog={catalog}
          onOpenChange={(o) => !o && setEditing(null)} />
      )}
      <Dialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {deleting?.name}?</DialogTitle>
            <DialogDescription>
              Projects that use it will follow their {BUSINESS_UNIT_LABEL} default instead, and will be told so.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>Cancel</Button>
            <Button variant="destructive" disabled={remove.isPending}
              onClick={() => deleting && remove.mutate(deleting, { onSettled: () => setDeleting(null) })}>
              {remove.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
  return { dialogs, setViewing, setEditing, setDeleting };
}

function ReadOnlyNote() {
  return (
    <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
      <Lock className="size-3" aria-hidden />You have read-only access to these tech stacks.
    </p>
  );
}

function BusinessUnitStacks({ workspaceId, where, catalog }: { workspaceId: string; where: string; catalog: TechStackCatalog | undefined }) {
  const q = useQuery({ queryKey: qk.techStacks.businessUnit(workspaceId), queryFn: () => listBusinessUnitTechStacks(workspaceId) });
  const { setDefault } = useTechStackWrites();
  const { dialogs, setViewing, setEditing, setDeleting } = useDialogs(catalog);
  if (q.isLoading) return <Skeleton className="h-20 w-full" />;
  if (q.isError) return <p className="text-destructive text-sm">Couldn&apos;t load this {BUSINESS_UNIT_LABEL}&apos;s tech stacks. {q.error instanceof Error ? q.error.message : ""}</p>;
  const { items, can_manage: canManage } = q.data!;
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-sm">
        The stacks {where} offers its projects. The default is what a project follows until its admin picks another.
      </p>
      {!canManage && <ReadOnlyNote />}
      {items.length === 0 ? (
        <p className="text-muted-foreground text-sm">No tech stacks yet — agents recommend one freely.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((s) => (
            <li key={s.id} className="flex items-start justify-between gap-3 rounded-md border p-3">
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.name}</span>
                  {s.is_default && <Badge>Default</Badge>}
                </div>
                {s.description && <p className="text-muted-foreground text-xs">{s.description}</p>}
                <StackChips stack={s} limit={8} />
              </div>
              <StackRowActions stack={s} canManage={canManage}
                onView={() => setViewing(s)} onEdit={() => setEditing({ kind: "edit", stack: s })} onDelete={() => setDeleting(s)}
                extra={
                  <Button size="sm" variant="ghost" className="h-8" disabled={setDefault.isPending}
                    aria-label={s.is_default ? `Remove ${s.name} as the default` : `Make ${s.name} the default`}
                    onClick={() => setDefault.mutate({ id: s.id, on: !s.is_default })}>
                    <Star className={s.is_default ? "size-4 fill-current" : "size-4"} aria-hidden />
                  </Button>
                } />
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <Button size="sm" variant="outline" onClick={() => setEditing({ kind: "create", scope: "workspace", scopeId: workspaceId, where })}>
          <Plus className="size-4" aria-hidden />New tech stack
        </Button>
      )}
      {dialogs}
    </div>
  );
}

const DEFAULT_VALUE = "__bu_default__";

function ProjectStacks({ projectId, where, catalog }: { projectId: string; where: string; catalog: TechStackCatalog | undefined }) {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: qk.techStacks.project(projectId), queryFn: () => getProjectTechStack(projectId) });
  const { dialogs, setViewing, setEditing, setDeleting } = useDialogs(catalog);
  const select = useMutation({
    mutationFn: (id: string | null) => selectProjectTechStack(projectId, id),
    onSuccess: (view) => {
      queryClient.setQueryData(qk.techStacks.project(projectId), view);
      void queryClient.invalidateQueries({ queryKey: qk.techStacks.all });
      toast.success(view.effective.stack ? `Agents now follow ${view.effective.stack.name}` : "Agents recommend a stack freely");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "The tech stack could not be changed."),
  });
  if (q.isLoading) return <Skeleton className="h-24 w-full" />;
  if (q.isError) return <p className="text-destructive text-sm">Couldn&apos;t load this project&apos;s tech stack. {q.error instanceof Error ? q.error.message : ""}</p>;
  const view = q.data!;
  const canManage = view.can_manage;
  const buDefault = view.options.business_unit.find((s) => s.is_default) ?? null;
  const value = view.selection.tech_stack_id ?? DEFAULT_VALUE;
  const option = (s: TechStack, own: boolean) => (
    <li key={s.id} className="flex items-start justify-between gap-3 rounded-md border p-3">
      <div className="flex min-w-0 items-start gap-3">
        <RadioGroupItem value={s.id} id={`ts-opt-${s.id}`} disabled={!canManage || select.isPending} className="mt-1" />
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor={`ts-opt-${s.id}`} className="flex items-center gap-2 font-medium">
            {s.name}
            {s.is_default && <Badge variant="outline">{BUSINESS_UNIT_LABEL} default</Badge>}
            {own && <Badge variant="outline">This project</Badge>}
          </Label>
          <StackChips stack={s} limit={8} />
        </div>
      </div>
      <StackRowActions stack={s} canManage={canManage && own}
        onView={() => setViewing(s)} onEdit={() => setEditing({ kind: "edit", stack: s })} onDelete={() => setDeleting(s)} />
    </li>
  );
  return (
    <div className="space-y-3">
      <p className="text-sm">
        Agents follow: <span className="font-semibold">{view.effective.stack?.name ?? "no tech stack"}</span>
        <span className="text-muted-foreground"> · {SOURCE_LABEL[view.effective.source]}</span>
      </p>
      {view.effective.warning && (
        <p role="alert" className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm text-amber-900 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />{view.effective.warning}
        </p>
      )}
      {!canManage && <ReadOnlyNote />}
      <RadioGroup value={value} onValueChange={(v) => select.mutate(v === DEFAULT_VALUE ? null : v)} aria-label="Tech stack for this project">
        <ul className="space-y-2">
          <li className="flex items-start gap-3 rounded-md border p-3">
            <RadioGroupItem value={DEFAULT_VALUE} id="ts-opt-default" disabled={!canManage || select.isPending} className="mt-1" />
            <Label htmlFor="ts-opt-default" className="font-medium">
              Follow the {BUSINESS_UNIT_LABEL} default
              <span className="text-muted-foreground block text-xs font-normal">
                {buDefault ? buDefault.name : "None set — agents recommend a stack freely"}
              </span>
            </Label>
          </li>
          {view.options.business_unit.map((s) => option(s, false))}
          {view.options.project.map((s) => option(s, true))}
        </ul>
      </RadioGroup>
      {canManage && (
        <Button size="sm" variant="outline" onClick={() => setEditing({ kind: "create", scope: "project", scopeId: projectId, where })}>
          <Plus className="size-4" aria-hidden />New project tech stack
        </Button>
      )}
      {dialogs}
    </div>
  );
}
```

- [ ] **Step 5: Put the panel in the Skills tab and honour `?tab=skills`.** In `agent-editor.tsx`:
  - add `import { useSearchParams } from "next/navigation";` and `import { TechStackPanel } from "./tech-stack-panel";`
  - in `AgentEditor`, add `const initialTab = useSearchParams().get("tab") === "skills" ? "skills" : "behavior";` and change `<Tabs defaultValue="behavior">` to `<Tabs defaultValue={initialTab}>`
  - in the `skills` `TabsContent`, before `<SkillsTab ...>`, add:

```tsx
          <div className="mb-6">
            <TechStackPanel scopeContext={scopeContext} />
          </div>
```

- [ ] **Step 6: The Design page chip.** Write the failing test `frontend/components/app/__tests__/tech-stack-chip.test.tsx`:

```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const getProjectTechStack = vi.fn();
vi.mock("@/lib/api/tech-stacks", () => ({ getProjectTechStack: (...a: unknown[]) => getProjectTechStack(...a) }));

import { TechStackChip } from "@/components/app/tech-stack-chip";

afterEach(() => { cleanup(); getProjectTechStack.mockReset(); });

function view(stack: unknown, source: string, warning: string | null = null) {
  return { project_id: "p1", workspace_id: "w1", options: { business_unit: [], project: [] }, selection: { tech_stack_id: null },
    effective: { stack, source, warning }, can_manage: false, can_manage_business_unit: false };
}
function show() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TechStackChip projectId="p1" /></QueryClientProvider>);
}
const node = { id: "s1", scope: "workspace", workspace_id: "w1", project_id: null, name: "Node + Next.js", description: "", categories: {}, notes: "", is_default: true };

describe("TechStackChip", () => {
  it("names the stack the Design agent follows and links to where it is chosen", async () => {
    getProjectTechStack.mockResolvedValue(view(node, "bu_default"));
    show();
    const link = await screen.findByRole("link", { name: /Tech stack: Node \+ Next\.js/ });
    expect(link).toHaveAttribute("href", "/agent-studio?project=p1&tab=skills");
  });

  it("says when there is none, and shows a warning", async () => {
    getProjectTechStack.mockResolvedValue(view(null, "none", "The tech stack chosen for this project was deleted."));
    show();
    const link = await screen.findByRole("link", { name: /No tech stack set/ });
    expect(link).toHaveAttribute("title", "The tech stack chosen for this project was deleted.");
  });

  it("says so when it cannot be read", async () => {
    getProjectTechStack.mockRejectedValue(new Error("boom"));
    show();
    expect(await screen.findByText("Tech stack unavailable")).toBeInTheDocument();
  });
});
```

Write `frontend/components/app/tech-stack-chip.tsx`:

```tsx
"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Layers } from "lucide-react";

import { getProjectTechStack } from "@/lib/api/tech-stacks";
import { qk } from "@/lib/api/query-keys";
import { SOURCE_LABEL } from "@/lib/schemas/tech-stacks";
import { cn } from "@/lib/utils";

/** The tech stack this project's agents follow, beside the agent that follows it. Links to
 *  where it is chosen (Agent Studio, the project's Skills tab). */
export function TechStackChip({ projectId }: { projectId: string }) {
  const q = useQuery({ queryKey: qk.techStacks.project(projectId), queryFn: () => getProjectTechStack(projectId), staleTime: 60_000 });
  const base = "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors";
  if (q.isLoading) return null;
  if (q.isError || !q.data) {
    return <span className={cn(base, "text-muted-foreground")} title={q.error instanceof Error ? q.error.message : undefined}>Tech stack unavailable</span>;
  }
  const eff = q.data.effective;
  const label = eff.stack ? `Tech stack: ${eff.stack.name}` : "No tech stack set";
  const title = eff.warning ?? (eff.stack ? SOURCE_LABEL[eff.source] : "Agents recommend a stack. Choose one in Agent Studio.");
  return (
    <Link href={`/agent-studio?project=${encodeURIComponent(projectId)}&tab=skills`} title={title}
      className={cn(base, "hover:bg-accent", eff.warning && "border-amber-500/50 text-amber-800 dark:text-amber-300")}>
      {eff.warning ? <AlertTriangle className="size-3.5" aria-hidden /> : <Layers className="size-3.5" aria-hidden />}
      {label}
    </Link>
  );
}
```

In `frontend/app/(app)/projects/[id]/design/page.tsx`:
- import `TechStackChip`
- add `<TechStackChip projectId={projectId} />` as the first child of the header's right-hand `div` (before `<ModelSelector`)

- [ ] **Step 7: Run the tests.** `npx vitest run components/agent-studio/__tests__ components/app/__tests__/tech-stack-chip.test.tsx`. Expected: the new tests pass, and the existing agent-studio tests are unchanged (the panel sits in AgentEditor, not in SkillsTab). If an agent-editor test renders the Skills tab, mock `@/lib/api/tech-stacks` there too.

- [ ] **Step 8: Type-check and lint every changed file.** `npx tsc --noEmit -p . && npx eslint components/agent-studio/tech-stack-panel.tsx components/agent-studio/tech-stack-editor.tsx components/agent-studio/agent-editor.tsx components/app/tech-stack-chip.tsx "app/(app)/projects/[id]/design/page.tsx" lib/api/tech-stacks.ts lib/schemas/tech-stacks.ts lib/api/query-keys.ts app/api/tech-stacks "app/api/projects/[id]/tech-stack" components/agent-studio/__tests__/tech-stack-panel.test.tsx components/app/__tests__/tech-stack-chip.test.tsx`. Expected: clean.

- [ ] **Step 9: Commit.** `git add frontend/components/agent-studio frontend/components/app/tech-stack-chip.tsx frontend/components/app/__tests__/tech-stack-chip.test.tsx "frontend/app/(app)/projects/[id]/design/page.tsx" && git commit -m "Agent Studio tech stack panel and the Design page chip"`

---

### Task 8: Migrate, verify live, measure latency after

**Files:**
- Scratch scripts only (session scratchpad): `bench_design_latency.py` (exists; its `generate_section_text` branch is used after Task 5), `live_tech_stacks.py` (below), `bench_resolver.py` (below).

- [ ] **Step 1: Apply 0065 to the dev DB** (as for 0064):
  1. Run `.venv/Scripts/python.exe -m alembic current`. Expected: `0064_document_approval_kinds`.
  2. Run `.venv/Scripts/python.exe -m alembic upgrade head`, then `alembic current`. Expected: `0065_tech_stacks (head)`.
  3. Verify both tables exist with `relrowsecurity` and `relforcerowsecurity` true (query `pg_class`).
  4. Stop the backend's three `process_api` processes and start it again on 8004. Wait for "Application startup complete", then check `/health` returns 200 and `/openapi.json` lists `/tech-stacks`.

- [ ] **Step 2: Latency with the feature and no stack.** Before any stack exists, run `RUNS=3 python bench_design_latency.py after-none`. Expected: close to the baseline; the only added work is one cached resolver read.

- [ ] **Step 3: The live walk-through.** Write `live_tech_stacks.py`. It drives the real router functions with a real BU admin and project admin (from `role_bindings`), or `admin:*` if there is none; the real DB; and the real model:
  1. Create "Java + Spring Boot (demo)", "Node + Next.js (demo)" and "Python + FastAPI (demo)" on QuickLink's BU, with full categories.
  2. Make Node the default.
  3. For each stack in turn: select it for QuickLink, then call `generate_section_text("document content", <approved BRD>, ["stack"], "", "")`. Save the Technology Stack table to `stack_<name>.md`, and record `meta`.
  4. Clear the selection: the effective stack is Node + Next.js (the BU default).
  5. Create "QuickLink custom (demo)" at project scope, select it, then delete it: the effective stack is Node (BU default), with the "was deleted" warning. Leave the project following the BU default.
  6. Print a side-by-side of the three tables and each run's `outside_stack` and `model_calls`.

  Expected: each table uses only its own stack's technologies (or the not-covered marker), and the three differ.

- [ ] **Step 4: Latency with a stack.** With Node selected as the default, run `RUNS=3 python bench_design_latency.py after-stack`. Then `bench_resolver.py` times `resolve_project_tech_stack` (uncached, ×20) and `resolve_project_tech_stack_cached` (warm, ×200), and reports the mean and p95.

- [ ] **Step 5: Summarise latency.** For each label and component, report the mean and median time, the prompt size, and the model calls. Also report the resolver's cold and warm cost, which is the chat turn's added work.

---

### Task 9: Full verification and wrap-up

- [ ] **Step 1: Backend suites.** Run: `.venv/Scripts/python.exe -m pytest tests/test_tech_stack_rules.py tests/test_tech_stack_store.py tests/test_tech_stacks_router.py tests/test_design_tech_stack.py tests/test_design_components.py tests/test_artifact_preview.py tests/test_docx_preview.py -q -p no:cacheprovider`. Expected: all pass.
- [ ] **Step 2: Frontend.** Run `npx vitest run components/app/__tests__ components/agent-studio/__tests__ __tests__ lib/api/__tests__`, `npx tsc --noEmit -p .`, and eslint on every changed file. Expected: all pass, clean.
- [ ] **Step 3: Commit any fixes, and report to the user:**
  - what was built
  - the live side-by-side of the three stacks
  - the latency before and after
  - what is still manual (a signed-in UI check)
  - the follow-ups: other agents; the 28 leftover test skills
  - Ask before pushing or opening a PR.
