"""The page view of a suite — the markdown kept beside the workbook, rendered in the app.

Tables are single blocks (consecutive rows); a pipe in a value is replaced so it cannot
split a cell. Functional steps are listed per case beneath the table: they are the part a
reviewer reads most closely, and a table cell cannot hold a numbered list.
"""
from __future__ import annotations

import re
from typing import Any

from agents_orchestrator.testing_agent.suites.models import KIND_LABEL, SuiteMeta


def _cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "").replace("|", "/")).strip() or "—"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def suite_markdown(meta: SuiteMeta, cases: list[Any]) -> str:
    label = KIND_LABEL[meta.kind]
    title = f"{meta.repository or meta.project or 'Project'} — {label} test cases"
    about = [
        f"- **Branch:** {meta.branch or '—'}" + (f" (commit {meta.commit[:7]})" if meta.commit else ""),
        f"- **Derived from:** {meta.sources or '—'}",
        f"- **Date:** {meta.generated_at or '—'}",
        f"- **Cases:** {len(cases)}",
    ]
    if meta.app_notes:
        about.append(f"- **Application:** {meta.app_notes}")
    blocks = [f"# {title}", "## About this suite", "\n".join(about)]

    if meta.kind == "unit":
        blocks += ["## Test cases", _table(
            ["ID", "Title", "Module", "Function", "Scenario", "Input", "Expected result", "Priority"],
            [[c.id, c.title, c.module, c.function, c.scenario, c.input, c.expected, c.priority] for c in cases])]
    elif meta.kind == "api":
        blocks += ["## Test cases", _table(
            ["ID", "Title", "Request", "Body", "Expected status", "Response must contain", "Priority"],
            [[c.id, c.title, f"{c.method} {c.path}", c.body, c.expected_status, "; ".join(c.expected_body_contains),
              c.priority] for c in cases])]
    else:
        blocks += ["## Test cases", _table(
            ["ID", "Title", "Preconditions", "Steps", "Expected result", "Priority"],
            [[c.id, c.title, c.preconditions, len(c.steps), c.expected, c.priority] for c in cases])]
        blocks.append("## Steps")
        for c in cases:
            steps = "\n".join(f"{i}. {s.describe()}" for i, s in enumerate(c.steps, start=1))
            blocks.append(f"### {c.id} · {c.title}\n\n{steps}\n\n**Expected:** {c.expected}")
    return "\n\n".join(blocks) + "\n"
