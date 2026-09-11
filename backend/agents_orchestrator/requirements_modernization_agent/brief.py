"""The migration-intent brief: what is required, and how it reads as a document.

Track 3's Requirements agent does not write user stories. A modernization starts from
a system that already exists, so what has to be pinned down before anyone plans is
different: WHY it is being modernized, FROM what TO what, what is in and out of
scope, the CONSTRAINTS the migration must live within, and how SUCCESS will be
measured. `REQUIRED_SECTIONS` is that list; the brief is not recorded until each is
answered, because Discovery, Design and Strategy each plan against a part of it.
"""
from __future__ import annotations

from shared.models.artifacts import MigrationIntentArtifact

#: (attribute path, the label a person reads). Order is the intake order.
REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("system_name", "System being modernized"),
    ("business_drivers", "Why this modernization is happening"),
    ("current_state.stack", "Current stack (from)"),
    ("target_state.stack", "Target stack (to)"),
    ("in_scope", "In scope"),
    ("constraints", "Constraints"),
    ("success_criteria", "Success criteria"),
)


def _value(brief: MigrationIntentArtifact, path: str):
    value = brief
    for part in path.split("."):
        value = getattr(value, part, None)
    return value


def missing_sections(brief: MigrationIntentArtifact) -> list[str]:
    """The labels of every required section still unanswered."""
    missing: list[str] = []
    for path, label in REQUIRED_SECTIONS:
        value = _value(brief, path)
        if isinstance(value, list):
            if not [v for v in value if str(v).strip()]:
                missing.append(label)
        elif not str(value or "").strip():
            missing.append(label)
    return missing


def _bullets(items: list[str], empty: str = "None recorded.") -> list[str]:
    cleaned = [str(i).strip() for i in items if str(i).strip()]
    return [f"- {i}" for i in cleaned] if cleaned else [f"- {empty}"]


def _cell(text: str) -> str:
    return (text or "—").replace("|", "\\|").replace("\n", " ")


def brief_markdown(brief: MigrationIntentArtifact) -> str:
    """The brief as the document the BA baselines and every later agent reads."""
    lines = [f"# Migration Intent Brief — {brief.system_name or 'unnamed system'}", ""]
    meta = ["Track 3 · Code Modernization"]
    if brief.recorded_at:
        meta.append(f"recorded {brief.recorded_at[:10]}")
    lines += ["_" + " · ".join(meta) + "_", ""]

    lines += ["## Why this modernization is happening", "", *_bullets(brief.business_drivers), ""]

    lines += [
        "## From → to", "",
        "| | Stack | Notes |", "|---|---|---|",
        f"| Today | {_cell(brief.current_state.stack)} | {_cell(brief.current_state.description)} |",
        f"| Target | {_cell(brief.target_state.stack)} | {_cell(brief.target_state.description)} |",
        "",
    ]

    lines += ["## Scope", "", "### In scope", "", *_bullets(brief.in_scope), "",
              "### Out of scope", "", *_bullets(brief.out_of_scope, "Nothing excluded yet."), ""]
    lines += ["## Constraints", "", *_bullets(brief.constraints), ""]
    lines += ["## Success criteria", "", *_bullets(brief.success_criteria), ""]

    repo = brief.legacy_repository
    lines += ["## Legacy repository", ""]
    if repo and (repo.url or repo.name):
        where = " / ".join(p for p in (repo.provider, repo.project, repo.name) if p)
        lines.append(f"- {where or repo.url}" + (f" — `{repo.url}`" if repo.url else ""))
    else:
        lines.append("- Not named yet — the Dependency and Risk agent will ask which repository to clone.")
    lines.append("")

    if brief.stakeholders:
        lines += ["## Stakeholders", "", "| Name | Role |", "|---|---|",
                  *[f"| {_cell(s.name)} | {_cell(s.role)} |" for s in brief.stakeholders], ""]

    lines += ["## Assumptions, risks and open questions", "",
              "### Assumptions", "", *_bullets(brief.assumptions), "",
              "### Risks", "", *_bullets(brief.risks), "",
              "### Open questions", "", *_bullets(brief.open_questions, "None."), ""]

    lines += ["## Next step", "",
              "- The Dependency and Risk agent clones the legacy repository read-only, maps its "
              "dependency graph, flags end-of-life and vulnerable dependencies, and scores "
              "every module for migration risk.", ""]
    return "\n".join(lines)
