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
    scope: str                                       # "workspace" | "project"
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
    source: str                                      # "project_selection" | "bu_default" | "none"
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
        f'2. Where the list does not cover something the design needs (a cache, an identity provider), write '
        f'"{NOT_COVERED}" in place of the technology — never choose one from outside the list.',
        # LIVE (2026-09-22): without this the marker was also written into the Version column.
        "3. Versions are not part of the list: give the version you recommend for each listed technology.",
        "4. Do not recommend alternatives from outside this list.",
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
            # The stack table ends at its first non-table line. The section goes on to hold
            # other tables (Environment | Description) whose cells are not technologies.
            if tech_idx is not None:
                break
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
