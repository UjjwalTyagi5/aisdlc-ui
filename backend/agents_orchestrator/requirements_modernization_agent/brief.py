"""The migration-intent brief: what is required, and how it reads as a document.

Track 3's Requirements agent does not write user stories. A modernization starts from
a system that already exists, so what has to be pinned down before anyone plans is
different: WHY it is being modernized, what the system is TODAY, the TARGET — which the
agent recommends from the code and the user's reasons, and the user confirms — what
changes in each module and at what cost, what is in and out of SCOPE, the CONSTRAINTS the
migration must live within, and how SUCCESS will be measured. `REQUIRED_SECTIONS` is the
minimum; the brief is not recorded until each is answered, because Discovery, Design and
Strategy each plan against a part of it.

The display helpers here (labels, the fallbacks for a version-1 brief, the key facts) are
shared by the Markdown below and by the Word/PDF renderer (`brief_document.py`), so the
chat, the Orchestrator's Deliverables and the downloads all say the same thing.
"""
from __future__ import annotations

import re
from datetime import date

from shared.models.artifacts import (
    DRIVER_CATEGORIES,
    BusinessDriver,
    LayerChange,
    MigrationIntentArtifact,
    Milestone,
    _pick,
)

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

CHANGE_LABEL = {
    "upgrade": "Upgrade", "rewrite": "Rewrite", "replatform": "Re-platform",
    "replace": "Replace", "retire": "Retire", "keep": "Keep as is", "new": "New",
}
STATUS_LABEL = {
    "eol": "End of life", "approaching": "Support ending", "legacy": "Legacy",
    "supported": "Supported", "unknown": "Unknown",
}
DRIVER_LABEL = {
    "end_of_support": "End of support", "security": "Security", "cost": "Cost",
    "skills": "Skills", "compliance": "Compliance", "performance": "Performance",
    "other": "Other",
}
EFFORT_LABEL = {"low": "Low", "medium": "Medium", "high": "High"}
MILESTONE_LABEL = {
    "start": "Start", "freeze": "Freeze", "compliance": "Compliance", "deadline": "Deadline",
    "cutover": "Cutover", "decommission": "Decommission", "other": "Milestone",
}
#: Worst first — a layer made of several modules shows its worst module's status.
_STATUS_RANK = ("eol", "approaching", "legacy", "unknown", "supported")


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


# ── display helpers (shared with the Word/PDF renderer) ─────────────────────


def worst_status(statuses: list[str]) -> str:
    known = [s for s in statuses if s in _STATUS_RANK]
    return min(known, key=_STATUS_RANK.index) if known else ""


def display_drivers(brief: MigrationIntentArtifact) -> list[BusinessDriver]:
    """The tagged drivers; for a brief that only has plain ones, tagged by their words."""
    if brief.drivers:
        return [d for d in brief.drivers if d.title or d.detail]
    return [
        BusinessDriver(category=_pick(text, DRIVER_CATEGORIES, "other"), title=text)
        for text in (str(t).strip() for t in brief.business_drivers) if text
    ]


def display_layers(brief: MigrationIntentArtifact) -> list[LayerChange]:
    """The change per layer; for a brief without them, the whole system as one row."""
    if brief.layers:
        return list(brief.layers)
    if brief.current_state.stack or brief.target_state.stack:
        return [LayerChange(layer="Whole system", current=brief.current_state.stack,
                            target=brief.target_state.stack)]
    return []


def end_of_life_count(brief: MigrationIntentArtifact) -> int:
    """How many parts of today's system run on something past its end of support."""
    if brief.module_changes:
        return sum(1 for m in brief.module_changes if m.current_status == "eol")
    return sum(1 for layer in brief.layers if layer.current_status == "eol")


def parse_date(text: str) -> date | None:
    text = (text or "").strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,})\w*\s+(\d{4})", text)  # 30 June 2027
    if m:
        from datetime import datetime  # noqa: PLC0415

        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).date()
            except ValueError:
                continue
    return None


def pretty_date(text: str) -> str:
    parsed = parse_date(text)
    return parsed.strftime("%d %b %Y").lstrip("0") if parsed else (text or "")


def sorted_milestones(brief: MigrationIntentArtifact) -> list[Milestone]:
    return sorted(brief.milestones, key=lambda m: (parse_date(m.date) or date.max, m.label))


def key_facts(brief: MigrationIntentArtifact) -> list[tuple[str, str]]:
    """The strip under the title: only facts the brief actually holds."""
    facts: list[tuple[str, str]] = []
    if brief.deadline:
        facts.append(("Deadline", pretty_date(brief.deadline)))
    if brief.budget:
        facts.append(("Budget", brief.budget))
    in_scope = [s for s in brief.in_scope if str(s).strip()]
    if in_scope:
        facts.append(("In scope", f"{len(in_scope)} item{'s' if len(in_scope) != 1 else ''}"))
    eol = end_of_life_count(brief)
    if eol:
        facts.append(("End of life today", f"{eol} part{'s' if eol != 1 else ''}"))
    if brief.recommendation and (brief.recommendation.summary or brief.layers):
        facts.append(("Target stack", "Recommended" if brief.recommendation.recommended_by == "agent"
                      else "Set by business"))
    return facts


# ── Markdown (chat, Orchestrator Deliverables, .md export) ──────────────────


def _bullets(items: list[str], empty: str = "None recorded.") -> list[str]:
    cleaned = [str(i).strip() for i in items if str(i).strip()]
    return [f"- {i}" for i in cleaned] if cleaned else [f"- {empty}"]


def _cell(text: str) -> str:
    return (text or "—").replace("|", "\\|").replace("\n", " ")


def _today(item) -> str:
    status = STATUS_LABEL.get(item.current_status, "")
    flag = f" · **{status.lower()}**" if item.current_status in {"eol", "approaching"} else (
        f" · {status.lower()}" if status and item.current_status != "unknown" else "")
    return _cell(item.current) + flag


def brief_markdown(brief: MigrationIntentArtifact) -> str:
    """The brief as the document the BA baselines and every later agent reads."""
    n = 0

    def section(title: str) -> list[str]:
        nonlocal n
        n += 1
        return [f"## {n}. {title}", ""]

    lines = [f"# Migration Intent Brief — {brief.system_name or 'unnamed system'}", ""]
    meta = ["Track 3 · Code Modernization"]
    if brief.recorded_at:
        meta.append(f"recorded {brief.recorded_at[:10]}")
    lines += ["_" + " · ".join(meta) + "_", ""]
    if brief.goal:
        lines += [f"> **Goal:** {brief.goal.strip()}", ""]
    facts = key_facts(brief)
    if facts:
        lines += ["| " + " | ".join(label for label, _ in facts) + " |",
                  "|" + "---|" * len(facts),
                  "| " + " | ".join(_cell(value) for _, value in facts) + " |", ""]

    layers = display_layers(brief)
    lines += section("The change at a glance")
    if layers:
        lines += ["| Part of the system | Today | Target | Change |", "|---|---|---|---|"]
        lines += [f"| **{_cell(layer.layer)}** | {_today(layer)} | {_cell(layer.target)} | "
                  f"{CHANGE_LABEL.get(layer.change_type, '—')} |" for layer in layers]
    else:
        lines.append("- Not recorded yet.")
    lines.append("")

    lines += section("Why we are modernizing")
    drivers = display_drivers(brief)
    if drivers:
        lines += ["| Driver | What it means for us |", "|---|---|"]
        lines += [f"| **{DRIVER_LABEL.get(d.category, 'Other')}** — {_cell(d.title)} | {_cell(d.detail)} |"
                  if d.detail else f"| **{DRIVER_LABEL.get(d.category, 'Other')}** | {_cell(d.title)} |"
                  for d in drivers]
    else:
        lines.append("- None recorded.")
    lines.append("")

    rec = brief.recommendation
    if rec and (rec.summary or rec.rationale):
        lines += section("Recommended target stack")
        by = ("Recommended by the Requirements agent from the legacy code and the reasons above — "
              "accepted when this brief is signed off." if rec.recommended_by == "agent"
              else "Set by the business.")
        lines += [f"_{by}_", ""]
        if rec.summary:
            lines += [rec.summary.strip(), ""]
        if rec.rationale:
            lines += ["**Why this stack**", "", *_bullets(rec.rationale), ""]
        if rec.alternatives:
            lines += ["**Alternatives considered**", "", "| Option | Why not |", "|---|---|",
                      *[f"| {_cell(a.option)} | {_cell(a.why_not)} |" for a in rec.alternatives], ""]
    elif brief.target_state.description:
        lines += section("Target state")
        lines += [brief.target_state.description.strip(), ""]

    lines += section("Scope")
    lines += ["**In scope**", "", *_bullets(brief.in_scope), "",
              "**Out of scope**", "", *_bullets(brief.out_of_scope, "Nothing excluded yet."), ""]

    if brief.module_changes:
        lines += section("What changes in each module")
        lines += ["| Module | Today | Target | Change | Effort |", "|---|---|---|---|---|"]
        lines += [f"| **{_cell(m.module)}** | {_today(m)} | {_cell(m.target)} | "
                  f"{CHANGE_LABEL.get(m.change_type, '—')} | {EFFORT_LABEL.get(m.effort, '—')} |"
                  for m in brief.module_changes]
        lines.append("")
        for m in brief.module_changes:
            if m.changes:
                label = CHANGE_LABEL.get(m.change_type, "")
                lines += [f"**{m.module}**" + (f" — {label.lower()}" if label else ""), "",
                          *_bullets(m.changes), ""]

    if brief.trade_offs:
        lines += section("Trade-offs")
        lines += ["| Decision | What we gain | What it costs |", "|---|---|---|",
                  *[f"| **{_cell(t.decision)}** | {_cell(t.gain)} | {_cell(t.cost)} |"
                    for t in brief.trade_offs], ""]

    milestones = sorted_milestones(brief)
    if milestones:
        lines += section("Timeline")
        lines += ["| Date | Milestone |", "|---|---|",
                  *[f"| {_cell(pretty_date(m.date))} | {_cell(m.label)} |" for m in milestones], ""]

    lines += section("Constraints")
    lines += [*_bullets(brief.constraints), ""]

    lines += section("How we will measure success")
    if brief.success_measures:
        lines += ["| Measure | Today | Target |", "|---|---|---|",
                  *[f"| {_cell(s.metric)} | {_cell(s.current)} | **{_cell(s.target)}** |"
                    for s in brief.success_measures], ""]
    lines += [*_bullets(brief.success_criteria), ""]

    if brief.stakeholders:
        lines += section("Stakeholders")
        lines += ["| Name | Role |", "|---|---|",
                  *[f"| {_cell(s.name)} | {_cell(s.role)} |" for s in brief.stakeholders], ""]

    lines += section("Assumptions, risks and open questions")
    lines += ["**Assumptions**", "", *_bullets(brief.assumptions), "",
              "**Risks**", "", *_bullets(brief.risks), "",
              "**Open questions**", "", *_bullets(brief.open_questions, "None."), ""]

    repo = brief.legacy_repository
    lines += ["## Legacy repository", ""]
    if repo and (repo.url or repo.name):
        where = " / ".join(p for p in (repo.provider, repo.project, repo.name) if p)
        lines.append(f"- {where or repo.url}" + (f" — `{repo.url}`" if repo.url else ""))
    else:
        lines.append("- Not named yet — the Dependency and Risk agent will ask which repository to read.")
    lines.append("")

    lines += ["## Next step", "",
              "- The Dependency and Risk agent reads the legacy repository read-only, maps its "
              "dependency graph, flags end-of-life and vulnerable dependencies, and scores "
              "every module for migration risk against this target.", ""]
    return "\n".join(lines)
