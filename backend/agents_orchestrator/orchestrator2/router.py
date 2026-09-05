"""Deterministic pre-filter for the Orchestrator's agent router.

`prefilter(text)` answers one narrow question: *is this message an explicit
imperative naming one of the nine agents?* If yes it returns that agent's id and
the turn costs no model call. If there is any doubt at all it returns `None`, and
the Context Agent — a model — decides. `None` is not a failure; it is the design.

Why this is deliberately narrow
-------------------------------
The engine this replaces routed on an agent alias appearing ANYWHERE in the text
(`agents_orchestrator/orchestrator/stage_switch.py`). That produced two failures
which between them define this module's contract:

  - "I need a PRD" contains no alias, so it routed nowhere and the user had to
    name the agent by hand. That limitation is why a Context Agent now exists.
  - "document this function" contains "document", so it DID route — to the
    Documentation agent — when it was plainly work for the agent already running.

So this pre-filter never matches a bare name mid-sentence. It requires the WHOLE
message to be the command: an optional politeness word, a run/switch/use/open-style
verb, the agent's name, and the literal word "agent". Anything else — a question, a
statement, a negation, a hedge, a sentence with the command buried inside it, a
message naming two agents — falls through to the model, which is the component
equipped to read intent.

Requiring the literal word "agent" is what makes the old bug structurally
impossible: "run testing" and "document this function" are plausible requests to
the agent already working, and neither can reach this matcher.

Names come from `AGENT_IDS` plus `DISPLAY_NAMES`, asserted equal at import time —
the same pattern `registry.py` uses for `REGISTRY`. Adding a tenth agent without
giving it a display name breaks the import, not a user's routing.

`plan` is the **Project Manager agent** in every user-facing string. The display
name is what users type; the id this function returns stays `plan`.

Pure: no IO, no model call, no state, no notion of a "next" agent. It must never
raise — odd input (None, a non-string, empty, gibberish) returns `None`.
"""
from __future__ import annotations

import re

from agents_orchestrator.orchestrator2.registry import AGENT_IDS

# The user-facing name of each agent, keyed by id. `plan` is the Project Manager
# agent — never "Plan agent", never "PM agent" — and that is the name users type.
DISPLAY_NAMES: dict[str, str] = {
    "requirements": "Requirements",
    "design": "Design",
    "plan": "Project Manager",
    "development": "Development",
    "code_review": "Code Review",
    "security": "Security",
    "testing": "Testing",
    "deployment": "Deployment",
    "documentation": "Documentation",
}

# An agent that exists but has no display name is unreachable by name — the exact
# class of silent gap `registry.py` was written to close. Fail at import, not at
# request time. `tests/orchestrator2/test_router.py` asserts the same thing.
assert set(DISPLAY_NAMES) == set(AGENT_IDS), (
    f"DISPLAY_NAMES {sorted(DISPLAY_NAMES)} does not cover AGENT_IDS {sorted(AGENT_IDS)}"
)

# The verbs that make a message a command rather than a remark. Every one of these
# is an instruction to hand the turn to a different agent; none of them reads as
# work for the agent currently running.
_VERBS: tuple[str, ...] = (
    "run",
    "open",
    "use",
    "start",
    "launch",
    "activate",
    "switch to",
    "go to",
    "move to",
    "talk to",
    "hand off to",
    "handoff to",
)

# Politeness that carries no intent, so stripping it changes nothing about whether
# the message is a command. Kept tiny on purpose: every addition is a hand-typed
# entry that has to stay true forever.
_LEADING_FILLER = r"(?:(?:please|pls|ok|okay|now)[,\s]+)?"
_TRAILING_FILLER = r"(?:[,\s]+(?:please|now))?"


def _normalise(text: str) -> str:
    """Lowercase, flatten separators, drop a trailing full stop.

    `_` and `-` become spaces so the raw id ("code_review") and the hyphenated
    spelling ("code-review") both reach the same matcher. A trailing "?" is NOT
    stripped: a question mark is the clearest possible signal that the message is
    a question about an agent rather than an order to run one, and leaving it in
    place makes the anchored pattern reject it.
    """
    lowered = re.sub(r"[_\-]+", " ", text.strip().lower())
    return re.sub(r"\s+", " ", lowered).strip().rstrip(".!").strip()


# Every spelling that names an agent: its display name and its raw id. Nothing
# else. The old router's alias lists ("docs", "qa", "ship it", "backlog") are the
# soup that made "document this function" route, and are not reproduced here.
_NAME_TO_ID: dict[str, str] = {}
for _agent_id in AGENT_IDS:
    _NAME_TO_ID[_normalise(DISPLAY_NAMES[_agent_id])] = _agent_id
    _NAME_TO_ID[_normalise(_agent_id)] = _agent_id


def _alternation(phrases: object) -> str:
    """Regex alternation over literal phrases, longest first so a longer name is
    never pre-empted by a shorter one that prefixes it."""
    return "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))


# The whole message must be the command — hence ^ and $. A command buried inside a
# larger sentence ("when the build is green, run the testing agent") is not
# unambiguous enough to skip the model, and a sentence naming two agents cannot be
# answered by a function that returns one id.
_COMMAND = re.compile(
    rf"^{_LEADING_FILLER}"
    rf"(?:{_alternation(_VERBS)})\s+"
    rf"(?:the\s+|a\s+|an\s+)?"
    rf"({_alternation(_NAME_TO_ID)})\s+"
    rf"agent{_TRAILING_FILLER}$"
)


def prefilter(text: str) -> str | None:
    """Return the agent id for an unambiguous imperative naming an agent, else `None`.

    Matches only a whole message of the shape *verb + agent name + "agent"*, e.g.
    "run the testing agent", "switch to the security agent", "use the project
    manager agent". Returns `None` for everything else — questions, statements,
    negations, in-agent work, messages naming two agents — so the Context Agent
    can read the intent properly.

    Never raises. Any odd input yields `None`.
    """
    try:
        if not isinstance(text, str) or not text:
            return None
        match = _COMMAND.match(_normalise(text))
        return _NAME_TO_ID[match.group(1)] if match else None
    except Exception:  # noqa: BLE001 - a pre-filter bug must never break a turn
        return None
