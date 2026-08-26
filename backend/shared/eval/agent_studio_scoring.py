"""Deterministic, offline scoring for Agent Studio prompt/skill drafts
(sub-project 4, evaluation-gated promotion).

Sibling to shared/eval/scoring.py's score_output(), same architecture (no LLM call,
no network — CI must run this offline) but a genuinely different question: score_output
compares an agent RUN's output artifact against a known-good `expected` value; a
prompt/skill DRAFT has no such expected text to diff against (the draft IS the new
content). This scores draft body text directly against a fixed per-agent topic rubric.

KNOWN LIMITATIONS, DISCLOSED (final whole-branch review, sub-project 4, Important #2)
---------------------------------------------------------------------------------------
1. GAMEABLE: a case-insensitive-substring rubric can be satisfied by pasting the
   topic keywords into otherwise-meaningless text (e.g. "acceptance criteria,
   stakeholder, scope, user stories" with nothing else scores 1.0). Accepted as an
   inherent limitation of a first-cut, deterministic, no-LLM rubric — the same
   tradeoff score_output() itself already makes (token-overlap scoring is no more
   robust to gaming). Not treated as blocking; a live-execution "golden task" runner
   is the documented, larger follow-up that would close this (see the sub-project 4
   spec §4/§6), not something this module can fix on its own.
2. NOT COMPOSED WITH ANCESTORS: `evaluate()`/`evaluate_skill()` score ONLY the draft
   row's own content (its own prompt/skill slots), never the org->workspace->project
   cascade `preview()`/`build_preview_layers` already know how to stack. A narrow,
   legitimate one-line delta on top of an already-good ancestor default therefore
   scores against the delta ALONE and can permanently fail the gate — the only way
   through today is padding the draft with topic keywords that add no real content,
   which is the opposite of what an evaluation gate should reward. This is a real
   gap, not merely a first-cut tradeoff like #1: fixing it means composing the same
   ancestor-context (`ancestor_chain`, `workspace_id`/`project_id`) `preview()`
   already receives into `evaluate()`/`evaluate_skill()`'s request shape, which
   neither route currently accepts — a genuine follow-up task, not a one-line patch.
"""
from __future__ import annotations

from shared.eval.scoring import EvalSignals

# Fixed, code-reviewed rubric — one entry per pipeline agent_id (AGENT_REGISTRY's 8
# keys). Each topic is a tuple of alternative keyword fragments; a topic counts as
# "present" if ANY fragment appears (case-insensitive substring) in the draft body.
# Deliberately a code constant, not a DB-editable rubric — matches this codebase's
# existing precedent (FORBIDDEN_PATTERNS, DESIGN_REQUIRED_SECTIONS are both code
# constants too); YAGNI for a first cut.
AGENT_REQUIRED_TOPICS: dict[str, tuple[tuple[str, ...], ...]] = {
    "requirements": (
        ("acceptance criteria",), ("stakeholder",), ("scope",), ("user story", "user stories"),
    ),
    "design": (
        ("architecture",), ("api contract", "api contracts"), ("database schema", "data model"),
        ("scalability", "non-functional"),
    ),
    "development": (
        ("code quality", "clean code"), ("test", "testing"), ("error handling",), ("naming convention",),
    ),
    "code_review": (
        ("readability",), ("maintainability",), ("style guide", "linting"), ("bug", "defect"),
    ),
    "security": (
        ("vulnerability",), ("owasp",), ("threat", "threat model"), ("authentication", "authorization"),
    ),
    "testing": (
        ("test coverage", "coverage"), ("edge case",), ("regression",), ("assertion",),
    ),
    "deployment": (
        ("rollback",), ("environment",), ("ci/cd", "pipeline"), ("monitoring", "observability"),
    ),
    "documentation": (
        ("audience",), ("example",), ("clarity",), ("changelog", "version history"),
    ),
}

PASS_THRESHOLD = 0.5


def evaluate_agent_default(agent_id: str, body: str) -> tuple[bool, EvalSignals]:
    """(is_pass, signals). Deterministic — same inputs always produce the same
    output, no LLM/network call. `is_pass` requires BOTH: score >= PASS_THRESHOLD
    AND zero forbidden-pattern hits (a forbidden-pattern hit disqualifies outright,
    independent of topic coverage — mirrors FORBIDDEN_PATTERNS' own all-or-nothing
    role in the existing write-time lint)."""
    from shared.routers.agent_profiles import FORBIDDEN_PATTERNS  # noqa: PLC0415 - cross-layer import kept local, see module docstring's layering note

    lowered = (body or "").lower()
    topics = AGENT_REQUIRED_TOPICS.get(agent_id, ())
    present = [group[0] for group in topics if any(frag in lowered for frag in group)]
    missing = [group[0] for group in topics if group[0] not in present]
    score = round(len(present) / len(topics), 4) if topics else 0.0

    forbidden_hits = [p for p in FORBIDDEN_PATTERNS if p.search(body or "")]

    signals = EvalSignals(
        score=score,
        signals={
            "topics_present": present,
            "topics_missing": missing,
            "forbidden_hits": [p.pattern for p in forbidden_hits],
        },
    )
    is_pass = score >= PASS_THRESHOLD and not forbidden_hits
    return is_pass, signals
