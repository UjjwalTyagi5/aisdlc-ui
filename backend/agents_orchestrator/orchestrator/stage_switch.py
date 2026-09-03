"""Natural-language "switch agent" intent detector for the Copilot turn loop.

Pure + fail-soft: `rule_match`/`mentions_stage` are deterministic regex/string
matching (no IO); `detect_switch` additionally accepts an injected async
`llm_classify` callable so the ambiguous-mention path can defer to a cheap LLM
classification without this module ever importing an LLM client itself.

STAGE_IDS is derived from the canonical STAGE_ORDER (agents_orchestrator ->
config.agent_registry) rather than hard-coded, so it can never drift from the
real 8 pipeline stages.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable, Optional

from shared.services.orchestrator.progression import STAGE_ORDER

STAGE_IDS: list[str] = list(STAGE_ORDER)

STAGE_ALIASES: dict[str, list[str]] = {
    "documentation": ["documentation agent", "documentation", "changelog", "docs"],
    "code_review": ["code review agent", "code review", "code-review", "review the code", "pr review"],
    "security": ["security agent", "security scan", "security review", "vulnerability scan", "security"],
    "testing": ["testing agent", "unit tests", "unit test", "functional test", "test cases", "api test", "testing", "qa"],
    "deployment": ["deployment agent", "deployment", "deploy", "release", "ship it"],
    "design": ["design agent", "architecture", "design"],
    "development": ["development agent", "development", "developer", "coding", "implementation", "dev"],
    "requirements": ["requirements agent", "requirement agent", "requirements", "requirement", "user stories", "backlog"],
    # The PM agent. Added to STAGE_ORDER when the stage shipped but missed here, so
    # the orchestrator could route to every stage except this one — "switch to
    # planning" fell through as if the stage did not exist. STAGE_ALIASES and
    # STAGE_IDS are asserted equal by tests/copilot/test_stage_switch.py, which is
    # what caught it.
    "plan": ["pm agent", "project manager", "planning agent", "planning", "plan",
             "schedule", "sprint plan", "resource plan", "timeline"],
}

SWITCH_VERBS: list[str] = [
    "switch to",
    "go to",
    "move to",
    "jump to",
    "run",
    "launch",
    "start",
    "begin",
    "let's do",
    "lets do",
    "now do",
    "hand off to",
    "handoff to",
    "activate",
    "kick off",
]


def _sorted_longest_first(items: list[str]) -> list[str]:
    return sorted(items, key=len, reverse=True)


def _alt(items: list[str]) -> str:
    """Build a regex alternation from literal phrases, longest-first."""
    return "|".join(re.escape(item) for item in _sorted_longest_first(items))


_VERB_ALT = _alt(SWITCH_VERBS)

# Cache per-stage compiled patterns: a switch verb, optionally "the", then an alias.
# Both the verb and the alias are anchored with word-boundary lookarounds so they
# only match whole words/phrases — otherwise substrings inside unrelated words
# ("redo" containing "do", "docstrings" containing "docs") would false-positive.
_RULE_PATTERNS: dict[str, re.Pattern] = {
    stage: re.compile(
        rf"(?<!\w)(?:{_VERB_ALT})(?!\w)\s+(?:the\s+)?(?<!\w)(?:{_alt(aliases)})(?!\w)"
    )
    for stage, aliases in STAGE_ALIASES.items()
}

# Stages ordered so multi-word / more-specific aliases are checked before
# shorter ones that might be substrings (e.g. "code review" before "review").
_STAGES_BY_ALIAS_LENGTH = sorted(
    STAGE_ALIASES.keys(),
    key=lambda stage: max(len(a) for a in STAGE_ALIASES[stage]),
    reverse=True,
)

_MENTION_PATTERNS: dict[str, re.Pattern] = {
    stage: re.compile(rf"(?<!\w)(?:{_alt(aliases)})(?!\w)")
    for stage, aliases in STAGE_ALIASES.items()
}


def rule_match(text: str, current_stage: str) -> Optional[str]:
    """Deterministic "verb + alias" match. Returns the target stage id, or None.

    Never raises — any odd/empty input just yields no match.
    """
    try:
        if not text:
            return None
        lowered = text.lower()
        for stage in _STAGES_BY_ALIAS_LENGTH:
            if stage == current_stage:
                continue
            if _RULE_PATTERNS[stage].search(lowered):
                return stage
        return None
    except Exception:
        return None


def mentions_stage(text: str) -> Optional[str]:
    """Returns a stage whose alias appears anywhere in *text*, else None.

    Used only to decide whether the ambiguity gate (LLM fallback) should run;
    the caller is responsible for excluding the current stage.
    """
    try:
        if not text:
            return None
        lowered = text.lower()
        for stage in _STAGES_BY_ALIAS_LENGTH:
            if _MENTION_PATTERNS[stage].search(lowered):
                return stage
        return None
    except Exception:
        return None


LlmClassify = Callable[[str, str, list[str]], Awaitable[dict]]


async def detect_switch(
    text: str,
    current_stage: str,
    *,
    llm_classify: Optional[LlmClassify] = None,
) -> Optional[str]:
    """Hybrid switch-intent detector: deterministic rules, then an optional
    LLM fallback for ambiguous-but-stage-mentioning turns.

    Fail-soft: any exception (including from `llm_classify`) is swallowed and
    treated as "no switch" so a detector bug can never block a turn.
    """
    try:
        target = rule_match(text, current_stage)
        if target:
            return target

        mentioned = mentions_stage(text)
        if mentioned and mentioned != current_stage and llm_classify is not None:
            try:
                result = await llm_classify(text, current_stage, STAGE_IDS)
            except Exception:
                return None
            if not isinstance(result, dict):
                return None
            if not result.get("switch"):
                return None
            candidate = result.get("target")
            if candidate in STAGE_IDS and candidate != current_stage:
                return candidate
            return None

        return None
    except Exception:
        return None
