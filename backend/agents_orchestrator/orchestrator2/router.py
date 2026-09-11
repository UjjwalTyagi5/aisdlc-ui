"""The Orchestrator's agent router: a deterministic pre-filter, then a model.

`route(text, ...)` decides which one of `track`'s delivery agents handles a turn, or
that none should and the user gets a direct answer. `track` defaults to
`"greenfield"` — the nine-agent portfolio Greenfield and Enhancement share (see
`config.agent_registry.TRACK_PORTFOLIOS`) — so everything in this module describing
"the nine agents" is describing that default, not a platform-wide constant: a
track-scoped caller passes `registry_for_track(track)`/`agent_ids_for_track(track)`
in and sees only that track's roster. It is two stages:

  1. `prefilter(text)` — a pure function answering one narrow question: *is this
     message an explicit imperative naming one of the nine agents?* If yes the turn
     costs no model call. If there is any doubt at all it returns `None`. It is
     track-agnostic BY DESIGN (see `route`'s docstring for why) — `route` is what
     discards a match outside the scoped track.
  2. `_ask_model(...)` — the Context Agent. It reads the message for MEANING, with
     one tool per agent generated from `REGISTRY` (or a track-scoped subset of it),
     and either calls exactly one or answers in plain text.

ALL NINE AGENTS ARE CANDIDATES ON EVERY TURN — on the default, `"greenfield"`,
track; a track-scoped caller sees only its own portfolio as candidates. There is no
ORDERING within a portfolio in this engine: no `STAGE_ORDER.index(active) + 1`, no
"next agent", no notion of what ran before. `stage_switch.py` (retired in Phase 5)
advanced positionally, which made "the next
 agent" mean "the next
item in a list" rather than "what the conversation needs".

The pre-filter returning `None` is not a failure; it is the design. It is why the
Context Agent exists, and stage 2 is the half the old engine did not have at all.

Why this is deliberately narrow
-------------------------------
The engine this replaces routed on an agent alias appearing ANYWHERE in the text
(`agents_orchestrator/orchestrator/stage_switch.py`, deleted in Phase 5). That
produced two failures
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

`prefilter` is pure: no IO, no model call, no state, no notion of a "next" agent. Odd
input (None, a non-string, empty, gibberish) returns `None` — by an explicit type
guard, not by a blanket `except` that would also hide a real routing bug (see
`prefilter`).

WHAT `route` GUARANTEES, AND WHAT IT DOES NOT
--------------------------------------------
Guaranteed, whatever the model returns (enforced in `_validated`): `agent_id` is
`None` or one of `AGENT_IDS`, so a hallucinated id can never reach dispatch; and when
`agent_id` is `None`, `direct_reply` is a non-empty string, so a turn never ends in
silence.

NOT guaranteed: that the agent chosen is the RIGHT one. That is a model's judgement.
It is why `reason` is shown to the user and why `agent.selected` is emitted before any
of the agent's text — a wrong pick has to be visible immediately, which is exactly
what the old engine's silent switching was not.

Also not guaranteed: that `route` returns at all. Model resolution failing raises,
deliberately — see `_ask_model`. There is no env-key fallback anywhere in this module.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from agents_orchestrator.orchestrator2.registry import (
    AGENT_IDS,
    REGISTRY,
    agent_ids_for_track,
    registry_for_track,
)
from shared.services.model_call_wrapper import guarded_completion
from shared.services.model_resolver import (
    litellm_key_kwargs,
    resolve_model_for_run,
    temperature_kwargs,
)

#: The track a caller that names none gets — the portfolio Greenfield and Enhancement
#: share, which is what every call site meant before tracks existed.
DEFAULT_TRACK = "greenfield"

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
    # Track 3 — Code Modernization. Offered only on that track (see `route`).
    "requirements_modernization": "Migration Intent",
    "discovery": "Dependency and Risk",
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


# Every spelling that names an agent: its display name and its raw id — plus, for an
# agent whose display name is not what people call it, the few names they do use.
# The old router's alias lists ("docs", "qa", "ship it", "backlog") are the soup that
# made "document this function" route, and are not reproduced here: an extra name is
# only ever a NAME for the agent, never a word describing its work.
#
# ONE NAME CAN MEAN TWO AGENTS. "Requirements" is Portfolio 1's Requirements agent on a
# Greenfield project and Track 3's migration-intent Requirements agent on a Code
# Modernization project — each track owns its own agent (design doc §1.4). So a name
# maps to every id it could mean, and `prefilter` picks the one inside the turn's own
# track; outside any track it means nothing.
_EXTRA_NAMES: dict[str, tuple[str, ...]] = {
    "requirements_modernization": ("requirements", "migration intent", "requirements (migration intent)"),
    "discovery": ("discovery and assessment", "assessment", "dependency and risk", "dependancy and risk"),
}

_NAME_TO_IDS: dict[str, tuple[str, ...]] = {}
for _agent_id in AGENT_IDS:
    for _spelling in (DISPLAY_NAMES[_agent_id], _agent_id, *_EXTRA_NAMES.get(_agent_id, ())):
        _key = _normalise(_spelling)
        if _agent_id not in _NAME_TO_IDS.get(_key, ()):
            _NAME_TO_IDS[_key] = (*_NAME_TO_IDS.get(_key, ()), _agent_id)


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
    rf"({_alternation(_NAME_TO_IDS)})\s+"
    rf"agent{_TRAILING_FILLER}$"
)


def prefilter(text: str, valid_ids: Any = None) -> str | None:
    """Return the agent id for an unambiguous imperative naming an agent, else `None`.

    `valid_ids` is the turn's own track portfolio (default: DEFAULT_TRACK's). A name
    resolves only to an agent inside it — "run the requirements agent" is the
    migration-intent Requirements agent on a Code Modernization project and Portfolio
    1's on a Greenfield one — and a name that means no agent inside it, or more than
    one, is not a command this function can answer.

    Matches only a whole message of the shape *verb + agent name + "agent"*, e.g.
    "run the testing agent", "switch to the security agent", "use the project
    manager agent". Returns `None` for everything else — questions, statements,
    negations, in-agent work, messages naming two agents — so the Context Agent
    can read the intent properly.

    Odd input yields `None` because of the `isinstance` guard below — NOT because
    of a blanket `except`. There used to be one here ("a pre-filter bug must never
    break a turn"), and it was worse than useless: every odd value the tests list is
    already handled by the guard, so the catch caught nothing real, while making
    `test_odd_input_returns_none_and_never_raises` unfalsifiable — delete the guard
    and the test still passed, because the catch covered for it. It would equally
    have laundered a genuine regex or `_NAME_TO_IDS` bug into "not a command", which
    routes the turn to the model and looks exactly like the intended behaviour. A
    pre-filter that silently mis-routes is the failure this module was written to
    end, so the fault surfaces instead. `ws.py`'s turn loop already renders an
    exception raised while serving a turn as a typed `error` the user can see, which
    is where this will land once `route` is called from inside it — nothing calls
    `route` or `prefilter` yet, so that is a property of the call site still to be
    written, not one this function can guarantee alone.

    Everything below is total for a `str`: `_normalise` calls only `str` methods and
    two anchored `re.sub`s, `_COMMAND` is compiled at import over escaped literals
    with no nested quantifier to backtrack on, and `group(1)` can only ever be one of
    the `_NAME_TO_IDS` keys the alternation was built from.
    """
    if not isinstance(text, str) or not text:
        return None
    match = _COMMAND.match(_normalise(text))
    if not match:
        return None
    pool = agent_ids_for_track(DEFAULT_TRACK) if valid_ids is None else tuple(valid_ids)
    hits = [aid for aid in _NAME_TO_IDS[match.group(1)] if aid in pool]
    return hits[0] if len(hits) == 1 else None


# ═══════════════════════════════════════════════════════════════════════════════
# The Context Agent's routing decision
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RoutingDecision:
    """What one turn's routing came to.

    `agent_id` is one of `AGENT_IDS` or `None`. `None` means no delivery agent runs
    and `direct_reply` is what the user sees instead — never silence: `route`
    guarantees that when `agent_id` is `None`, `direct_reply` is a non-empty string
    (see `_validated`).

    `reason` is one line shown to the user saying why, and is set on both paths.

    Frozen because a decision is a fact about a turn: the agent named here is the one
    announced in `agent.selected` and the one dispatch runs, and a decision that can
    be edited afterwards is how those three stop being the same agent.
    """

    agent_id: str | None
    reason: str
    direct_reply: str | None


# What each agent actually does, one line, for the model to choose between. Written
# from each agent's own system prompt (`registry.py`'s `load_prompt` targets) and its
# graph nodes — not from `AGENT_REGISTRY.required_capabilities`, which is declared
# metadata nobody checks against behaviour.
#
# Hand-typed, and therefore asserted at import for COVERAGE: `{"security": ""}` covers
# `AGENT_IDS` perfectly and still leaves the Security agent advertised as nothing but
# its name, which the coverage assert alone cannot see.
#
# What each of the three checks on this table actually buys, stated honestly because a
# comment crediting a check with more than it does is the recurring defect in this
# rebuild:
#
#   · the coverage assert catches a MISSING entry, and nothing else.
#   · the `_MIN_CAPABILITY_CHARS` floor catches the DEGENERATE entry — "", "does
#     stuff" — and nothing else. It is a character count; it cannot tell a real
#     description from plausible filler, and content-free boilerplate over the floor
#     ("handles whatever needs handling in this area of the project") passes it. Treat
#     it as a smoke check, not as enforcement of what this table is for.
#   · what actually enforces what this table is for is REACH: the text must arrive
#     where the model reads it, which is both the tool description built in
#     `_tool_specs` and the prompt roster built in `_system_prompt`. Neither is
#     provable from this table alone, so both are pinned in
#     `tests/orchestrator2/test_router.py` — drop the interpolation from either and
#     those tests go red.
_CAPABILITIES: dict[str, str] = {
    "requirements": (
        "gathers and normalises what is to be built — PRDs and BRDs, epics, features, "
        "INVEST user stories, Gherkin acceptance criteria, and gap/NFR analysis; and "
        "CREATES AND READS WORK ITEMS on the project's connected board (Azure DevOps "
        "or Jira)"
    ),
    "design": (
        "turns requirements into architecture — high- and low-level design, API "
        "contracts, data schemas, ADRs and diagrams"
    ),
    "plan": (
        "turns a design into a plan — work breakdown, estimates, schedule, and the "
        "risks that threaten them"
    ),
    "development": (
        "reads the codebase and writes code — new files, targeted edits, refactors, "
        "and fixing what is broken; CLONES FROM AND PUSHES TO the project's connected "
        "repository (Azure Repos or GitHub), including branches and pull requests"
    ),
    "code_review": (
        "reviews a branch diff or an open pull request, read-only, and returns a "
        "structured, actionable review"
    ),
    "security": (
        "scans a branch or pull request, read-only, for vulnerabilities — dependency "
        "(SCA), static analysis (SAST), secrets — and returns a risk-scored report"
    ),
    "testing": (
        "plans, generates and executes tests against the code, then reports what "
        "passed, what failed and why"
    ),
    "deployment": (
        "produces the deployment package — Dockerfile, CI/CD pipeline, Kubernetes "
        "manifests, deploy and rollback runbooks — and assesses release readiness"
    ),
    "documentation": (
        "writes documentation from the repository and the project's artifacts — "
        "overviews and READMEs, API docs, runbook updates, knowledge articles"
    ),
    "requirements_modernization": (
        "captures the MIGRATION INTENT for modernizing an existing system — why it is "
        "happening, the current and the target stack, what is in and out of scope, the "
        "constraints and measurable success criteria — records the migration-intent "
        "brief, and can write the migration Epic and items to the connected board"
    ),
    "discovery": (
        "clones and reads the LEGACY repository read-only, maps its modules and "
        "dependency graph, flags end-of-life, deprecated and vulnerable dependencies, "
        "and scores every module for migration risk (mechanical, LLM-assisted or "
        "manual-only) — the assessment later planning works from"
    ),
}

assert set(_CAPABILITIES) == set(AGENT_IDS), (
    f"_CAPABILITIES {sorted(_CAPABILITIES)} does not cover AGENT_IDS {sorted(AGENT_IDS)}"
)

# The shortest description above is 98 characters. A floor rather than "non-empty"
# because "does stuff" is as useless to the model as "", and a rule that only rejects
# the empty string invites exactly that. It rejects the degenerate case and no more —
# see the note above for what does the real work.
_MIN_CAPABILITY_CHARS = 40


def _undescribed(capabilities: Mapping[str, Any]) -> list[str]:
    """The ids in `capabilities` whose text is too short to say what the agent does.

    A function rather than an inline comprehension so the RULE can be tested against a
    deliberately broken table without breaking this module's import. Inline, the only
    way to prove the check works was to weaken `_CAPABILITIES` itself, which fires the
    assert below during collection — and a collection error reads as a broken test file
    rather than as a broken invariant, so nobody trusts it.
    """
    return sorted(
        agent_id
        for agent_id, text in capabilities.items()
        if not isinstance(text, str) or len(text.strip()) < _MIN_CAPABILITY_CHARS
    )


_UNDESCRIBED = _undescribed(_CAPABILITIES)
assert not _UNDESCRIBED, (
    f"_CAPABILITIES entries too short to describe an agent (under "
    f"{_MIN_CAPABILITY_CHARS} characters): {_UNDESCRIBED}. The model routes on this "
    f"text; an agent described by nothing is an agent that is never chosen."
)

def _default_capabilities() -> Mapping[str, Any]:
    """What an untracked caller is offered: the DEFAULT_TRACK portfolio.

    NOT the whole `REGISTRY`. `REGISTRY` now also holds Track 3's agents, and a caller
    that names no track has always meant the nine; defaulting to the whole table would
    quietly start offering Discovery & Assessment to a Greenfield conversation.

    Read off this module's `REGISTRY` (filtered to the portfolio's ids) rather than
    through `registry_for_track`, so the list stays DERIVED from the one table that
    says an agent can run — remove an entry there and it disappears here too.
    """
    return {aid: REGISTRY[aid] for aid in agent_ids_for_track(DEFAULT_TRACK) if aid in REGISTRY}


# One tool per agent, named `route_to_<agent id>`. The prefix is what makes the id
# recoverable exactly: `code_review` contains an underscore, so a name is un-prefixed,
# never split.
_TOOL_PREFIX = "route_to_"

# How many earlier messages the routing call sees. Routing depends on recent context
# ("and now?" after a design discussion), so some history is required; an unbounded
# transcript would make a cheap classification call grow without limit in cost and
# latency, and push the message actually being routed out of the model's attention.
# The most recent 20 are kept, and the message being routed is always appended last.
_HISTORY_LIMIT = 20

# A routing turn, not a chat turn: enough for a tool call plus a short direct answer.
_MAX_TOKENS = 1024

_ANSWERED_DIRECTLY = "No delivery agent was needed for this."

_COULD_NOT_CHOOSE_REASON = "I could not match that to one of the delivery agents."

_COULD_NOT_CHOOSE_REPLY = (
    "I could not work out which agent should handle that. Tell me what you want done "
    'and I will pick one — or name it yourself, e.g. "run the testing agent".'
)


def _tool_specs(capabilities: Mapping[str, Any] | None = None) -> list[dict]:
    """One tool per agent, GENERATED FROM `REGISTRY` — or from a track-scoped
    subset of it, when `capabilities` is given.

    Derived, never hand-typed, because the two lists drifting apart is precisely the
    failure `registry.py` exists to prevent: the old engine's dispatch table silently
    omitted `plan`, so routing could select an agent nothing could run. A tool list
    built from `REGISTRY` cannot offer an agent this engine cannot dispatch — remove
    an entry from `REGISTRY` and the model stops being offered it in the same breath.

    `capabilities=None` reproduces today's behaviour EXACTLY — every agent in
    `REGISTRY`, unscoped — which is what every call site not yet naming a track
    still gets. A track-scoped caller passes `registry_for_track(track)`, so the
    model is never even offered a tool for an agent outside this run's track.

    `REGISTRY` rather than `AGENT_IDS` on purpose. They are the same set (asserted at
    import in `registry.py`), but `REGISTRY` is the one that says the agent can
    actually be RUN, and that is the property this list has to have.
    """
    source = _default_capabilities() if capabilities is None else capabilities
    return [
        {
            "type": "function",
            "function": {
                "name": f"{_TOOL_PREFIX}{agent_id}",
                "description": (
                    f"The {DISPLAY_NAMES[agent_id]} agent: {_CAPABILITIES[agent_id]}. "
                    f"Call this when the user's message asks for that work."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": (
                                "One short line, addressed to the user, saying why "
                                "this agent is the right one for what they just "
                                "asked. Shown to them verbatim."
                            ),
                        }
                    },
                    "required": ["reason"],
                },
            },
        }
        for agent_id in source
    ]


# The routing prompt. Three things it deliberately does NOT say, each of them a
# behaviour of the engine being replaced:
#
#   · nothing about order. The retired `stage_switch.py` advanced by
#     `STAGE_ORDER.index(active) + 1`
#     — "the next agent" meant "the next item in a list", not "what the conversation
#     needs". Every turn here considers all nine, and the model is told so explicitly
#     because a model shown a list will otherwise infer a pipeline from it.
#   · nothing about approvals or hand-off conditions. Those are not this engine's, and
#     a model told to consider them would invent state it cannot see.
#   · no keyword rules. Alias-anywhere matching is the exact bug being removed, so the
#     prompt names the two canonical cases in both directions: "I need a PRD" (routes,
#     names nothing) and "document this function" (does not route, names something).
#
# The ROSTER is generated from `REGISTRY`, the same source as the tools, so the roster
# cannot name an agent the tool list does not offer. The template below is hand-written
# prose, so it can: a `route_to_*` typed into it here would be advertised to the model
# and derived from nothing. That is a test's job, not generation's — see
# `_system_prompt`.
_PROMPT_TEMPLATE = """\
You are the Context Agent for a software delivery platform. You read the conversation
and decide one thing: which delivery agent should handle the user's latest message —
or that none should, and you answer it yourself.

The agents you may choose from are exactly the tools you have been given, one per
agent:

{roster}

How to decide:

- Route on what the message ASKS FOR, not on words it happens to contain. "I need a
  PRD" is Requirements work even though it names no agent at all. "Document this
  function" is a request to whoever is already working, not a hand-off to
  Documentation.
- Call exactly one tool, or call none. Never call two: one agent runs per turn.
- WHEN TWO AGENTS COULD BOTH FIT, PICK THE LIKELIER ONE AND SAY WHY. Do not stall to
  ask which. Work that needs two agents needs one of them FIRST, and starting it is
  recoverable in a way a question is not: the user reads your `reason` and redirects in
  one turn if you chose wrong. "Ship it" is Deployment; "make it faster" is
  Development; "review this" is Code Review. Answer directly only if you cannot tell
  what KIND of work is being asked for at all.
- Never invent an agent. The tools above are the complete list of what this platform
  can run. A name that is not one of them cannot be started, and choosing one wastes
  the user's turn.
- There is no fixed order. Any agent may run at any time, as often as the work needs,
  whatever ran before it. Do not reason about which agent ought to follow another,
  about how far along the project is, or about whether earlier work was accepted —
  none of that is your decision, and none of it is in front of you.
- MISSING DETAIL IS NOT A REASON TO WITHHOLD ROUTING. If the message asks for work an
  agent does, start that agent even when it does not say which service, which repo or
  which feature. Gathering those specifics is the agent's own first job, and it can ask
  far better than you can. Answering "I'd be happy to help, but I need more context"
  is the one failure this router must not have: the user asked for work and got a
  question back, and no agent ran.
- BEING PHRASED AS A QUESTION DOES NOT MAKE IT YOURS. "Are there any injection risks
  here?" is Security's work product, not a chat answer; "who is working on what, and
  when does this land?" is the Project Manager's. Ask yourself what would ANSWER the
  message — if the answer is something one of the agents above produces, route to it.
- THE AGENTS REACH THE PROJECT'S CONNECTED TOOLS. Whatever this project has wired up
  — Azure DevOps boards and repositories, Jira, GitHub — the agents above work with it
  directly. Requirements creates and reads work items on the board. Development clones
  the repository, branches, commits, pushes and opens pull requests. This is not a
  documents-only platform, and saying it is, is false.
- NEVER DECLINE ON AN AGENT'S BEHALF. You do not know what the agents cannot do; you
  know what they are for. "I can't create items in Azure Boards", "this platform
  doesn't integrate with external trackers", "I can't pull code directly" — every one
  of those was said here about work an agent went on to do minutes later. If a message
  asks for something one of the nine does, start it. If you are unsure whether an agent
  can do a thing, start the agent that would: it will say so far better than you can,
  and it is the one that would know.
- "CAN YOU …" IS A REQUEST, NOT A SURVEY. "Can you create a story on Azure Boards" and
  "can you pull code from Azure Repos" are asking for the work, not for an inventory of
  your abilities. Route them.
- NEVER ASK PERMISSION TO ROUTE. "Would you like me to route you to the Development
  agent?" costs the user a turn to say yes to what they already asked for. Start the
  agent and say which one in `reason`; if that was wrong they redirect in one turn,
  which is cheaper than the question.
- Answer directly, with no tool call, only when NO agent could make progress on the
  message: a greeting, small talk, a question about this platform or about what you
  can do that is NOT a request for work any agent performs, or a follow-up about
  something already produced in this conversation. Starting an agent for one of those
  interrupts work instead of doing any. When a message is both — a question about your
  abilities AND a request for work — it is a request for work, and it routes.

When you call a tool, `reason` is one short line shown to the user, addressed to them,
saying why that agent — for example "You asked for a PRD, so I've started
Requirements." When you answer directly, just answer: your reply is what they see.
"""


#: Track 3 — Code Modernization. ITS OWN TEMPLATE rather than a conditional section in
#: the Greenfield one (help/track3-implementation-plan.md §7's open question): a
#: modernization is a different conversation — a legacy system, a target stack, a
#: migration — and Greenfield's examples ("I need a PRD", "Development clones the
#: repository") would tell the model about agents this track does not have. The
#: routing RULES are the same ones, restated in this track's vocabulary.
#:
#: Like the Greenfield template, it names no `route_to_*` tool in its prose — the
#: roster is generated from the same map as the tools, and
#: `test_the_prompt_names_no_routing_tool_outside_the_registry` checks the whole text.
_MODERNIZATION_PROMPT_TEMPLATE = """\
You are the Context Agent for a software delivery platform. This project is on TRACK 3 —
CODE MODERNIZATION: it migrates an existing, legacy codebase to a new language, framework
or version. It does not build something new from a blank slate. You read the conversation
and decide one thing: which of this project's agents should handle the user's latest
message — or that none should, and you answer it yourself.

The agents you may choose from are exactly the tools you have been given, one per agent:

{roster}

Track 3's full roster, in hand-off order, is Migration Intent → Dependency and
Risk → Design → Strategy → Development → Code Review → Security → Testing →
Deployment → Documentation. Not built for this track yet: {unbuilt}. If the user asks for
one of those, answer directly: say plainly that that agent is not available for Code
Modernization yet, and offer what the agents above can do instead. Never send that work to
an agent above that does not do it.

How a modernization starts:

- A greeting ("hi", "hello"), "where do I start", "what can you do" or "what is this
  project" is yours to answer directly, in a few sentences: this is a Code Modernization
  project; the work starts with the Migration Intent agent capturing the MIGRATION INTENT —
  why the modernization is happening, what the system runs on today and what it should
  run on afterwards, what is in and out of scope, the constraints, and how success will
  be measured; then the Dependency and Risk agent clones and reads the legacy repository
  (read-only) to map its dependency graph, flag end-of-life and vulnerable dependencies,
  and score every module for migration risk. End by asking them to describe the
  modernization: the system, why it is being modernized, and from what to what.
- Any message that DESCRIBES the modernization is Migration Intent agent work:
  the system, the reasons, the current or target stack, scope, constraints, deadlines,
  budget, success measures, stakeholders, a pasted or attached brief — and answers to the
  Requirements agent's own questions.
- Messages about the LEGACY CODE ITSELF are Dependency and Risk agent work: "proceed to
  discovery", "assess the repository", "scan the codebase", "clone the legacy repo",
  "which modules are riskiest", "which dependencies are end-of-life or vulnerable",
  "map the dependencies".

How to decide:

- Route on what the message ASKS FOR, not on words it happens to contain.
- Call exactly one tool, or call none. Never call two: one agent runs per turn.
- WHEN BOTH AGENTS COULD FIT, PICK THE LIKELIER ONE AND SAY WHY. Do not stall to ask
  which: the user reads your `reason` and redirects in one turn if you chose wrong.
- Never invent an agent. The tools above are the complete list of what this project can
  run.
- There is no fixed order and the user decides when to move on. If they ask for the Dependency
  and Risk agent before the migration intent is recorded, start it anyway and say in
  `reason` that the brief is still open.
- MISSING DETAIL IS NOT A REASON TO WITHHOLD ROUTING. Gathering the specifics — which
  repository, which target version — is the agent's own first job.
- NEVER DECLINE ON AN AGENT'S BEHALF, and never ask permission to route. "Can you assess
  the repo" is a request for the work; route it.
- Answer directly, with no tool call, only for a greeting, small talk, a question about
  this platform or this track, a request for an agent not built for this track yet, or a
  follow-up about something already produced in this conversation.

When you call a tool, `reason` is one short line shown to the user, addressed to them,
saying why that agent — for example "You described why the billing system is being
modernized, so I've started the Migration Intent agent." When you answer directly,
just answer: your reply is what they see.
"""

#: Track 3's roster by display name, in hand-off order — used only to NAME the agents
#: not built yet. Which agents can run comes from the capabilities map, never from here.
_MODERNIZATION_ROSTER: tuple[str, ...] = (
    "Migration Intent", "Dependency and Risk", "Design", "Strategy",
    "Development", "Code Review", "Security", "Testing", "Deployment", "Documentation",
)


def _system_prompt(
    capabilities: Mapping[str, Any] | None = None, track: str = DEFAULT_TRACK
) -> str:
    """The routing prompt with the agent roster generated from `REGISTRY` — or from
    a track-scoped subset of it, when `capabilities` is given.

    Each line carries the agent's display name, its tool id and its `_CAPABILITIES`
    text, because the roster is where the model reads what an agent is FOR. A roster of
    bare names is the failure `_CAPABILITIES` exists to prevent, reached by the other
    door.

    `capabilities=None` reproduces today's behaviour EXACTLY — the full nine-agent
    roster — so every existing caller (and the tests pinning this exact string) is
    unaffected. A track-scoped caller passes `registry_for_track(track)`, and the
    rendered roster then names only that track's agents — a project on a track with
    an empty portfolio gets a roster of nothing, which `route` handles before this
    is ever called (see `route`'s early return).

    Generation is what stops the roster naming an agent `REGISTRY` lacks — but only for
    the roster. The prompt is a template a person will edit, and a `route_to_*` written
    into its BODY, or into a bullet of some other shape, is just as routable to the
    model and is not derived from anything. What closes that is a test:
    `test_the_prompt_names_no_routing_tool_outside_the_registry` compares every
    `route_to_*` occurrence in the WHOLE rendered prompt against `REGISTRY`, and does
    not care what line it sits on.
    """
    source = _default_capabilities() if capabilities is None else capabilities
    roster = "\n".join(
        f"- The {DISPLAY_NAMES[agent_id]} agent ({_TOOL_PREFIX}{agent_id}): "
        f"{_CAPABILITIES[agent_id]}."
        for agent_id in source
    )
    if track == "modernization":
        built = {DISPLAY_NAMES[agent_id] for agent_id in source}
        unbuilt = ", ".join(n for n in _MODERNIZATION_ROSTER if n not in built) or "none"
        return _MODERNIZATION_PROMPT_TEMPLATE.format(roster=roster, unbuilt=unbuilt)
    return _PROMPT_TEMPLATE.format(roster=roster)


#: Appended to the routing prompt when a delivery agent answered the previous turn.
#:
#: THE BUG. `route` runs on every turn, and without this the router sees a reply of
#: "2" or "main" with no sign that anyone asked a question. The prompt permits
#: answering directly for "a follow-up about something already produced", and a bare
#: word looks exactly like one — so the Orchestrator answered, re-asked what the agent
#: had just asked, and the agent then asked a third time. Observed live:
#:
#:     Development:  Here are the projects... which one?
#:     You:          2
#:     Orchestrator: Great, you have selected Company. Which repository?
#:     Development:  Here are the repositories in Company... which one?
#:
#: A NUDGE, NOT A LOCK. "Any agent can come at any time according to the chat" is the
#: whole premise of this engine; pinning a conversation to whoever spoke first would
#: rebuild the linearity it exists to remove. So this says what is true — that agent
#: is mid-conversation — and leaves the decision where it was.
_CONTINUITY_TEMPLATE = """

THE {name} AGENT IS MID-CONVERSATION. It answered the previous turn, and it may have
ended by asking the user something. If this message reads as an ANSWER to it — a
number, a branch or repository name, a yes or no, a short phrase that only means
anything as a reply — route it back to {name} ({tool}). Do not answer it yourself,
and do not re-ask what has already been asked: the agent asked because it needs the
answer to continue, and it is the only one that can act on it.

This does not pin the conversation. A message asking for different work goes to
whichever agent does that work, exactly as it would have on any other turn.
"""


def _system_prompt_with_continuity(
    last_agent: str | None,
    capabilities: Mapping[str, Any] | None = None,
    track: str = DEFAULT_TRACK,
) -> str:
    """`_system_prompt`, plus who is mid-conversation when anyone is.

    An unknown agent id is treated as no agent rather than raising: this is a hint,
    and a routing turn is the wrong place to fail over one. `last_agent` outside
    `capabilities` (the run's OWN prior agent, on a project whose track no longer —
    or never did — offer it) is treated the same way: naming it in the continuity
    note would tell the model to route back to an agent the tool list does not
    include, which `_validated` would then have to refuse anyway.
    """
    source = _default_capabilities() if capabilities is None else capabilities
    if not last_agent or last_agent not in source:
        return _system_prompt(capabilities, track)
    return _system_prompt(capabilities, track) + _CONTINUITY_TEMPLATE.format(
        name=DISPLAY_NAMES[last_agent], tool=f"{_TOOL_PREFIX}{last_agent}",
    )


def _llm_kwargs(resolved: Any) -> dict:
    """ChatLiteLLM construction kwargs for a resolved BYOK model.

    Split out from `_build_llm` so what is sent to the provider can be asserted
    without importing litellm (a ~7s import) in a test.

    `litellm_key_kwargs` is not optional. ChatLiteLLM keeps a SEPARATE named field per
    provider (`anthropic_api_key`, ...) that is defaulted from the environment and
    WINS over the generic `api_key` — which is how agents authenticated with a stale
    platform key while reporting the tenant's valid key as invalid.

    `max_retries=0` because `guarded_completion` is the sole retry mechanism.
    ChatLiteLLM's own tenacity retry wraps every call underneath it, uncoordinated, so
    one guarded call became up to 3x3 real requests at a provider already rate-limiting
    (observed live; see `dev_agent._build_llm`).

    Temperature goes through `temperature_kwargs`, not a literal: the gpt-5 family and
    the newest Claude models reject any non-default temperature and litellm raises
    BEFORE the call, so a hardcoded value fails every routing turn for those tenants.
    """
    kwargs: dict = {
        "model": resolved.model,
        "custom_llm_provider": resolved.litellm_provider,
        "api_base": resolved.base_url,
        "api_key": resolved.api_key,
        "max_tokens": _MAX_TOKENS,
        "max_retries": 0,
        **litellm_key_kwargs(resolved.litellm_provider, resolved.api_key),
    }
    kwargs.update(temperature_kwargs(resolved.model, 0.0))
    return kwargs


def _build_llm(resolved: Any) -> Any:
    """Build the routing client. Imports litellm lazily: that import costs ~7s, and
    merely importing this module must never cost it. `ws.py` imports its orchestrator2
    dependency (`dispatch`) at module scope, i.e. at process start, so an eager import
    here would land on boot the moment routing is wired into that socket."""
    from langchain_litellm import ChatLiteLLM  # noqa: PLC0415

    return ChatLiteLLM(**_llm_kwargs(resolved))


# THE ROLE VOCABULARY OF THIS PLATFORM, enumerated against the column that stores it
# — `shared/models/orm.py`'s `conversation_messages.role`, whose comment reads
# `user|agent|orchestrator|system|tool`. It is NOT the OpenAI vocabulary, and assuming
# it was is what made this function drop every assistant turn ever persisted here.
#
# `agent` is THE assistant role in this codebase. Every writer in the tree persists an
# agent's reply under it (`copilot_api.py:1619,1628,2393,2657,2660`,
# `orchestrator_api.py:1729`, `requirements_agent_api.py:573`,
# `development_agent_api.py:602`, `design_architecture_agent_api.py:399`,
# `deployment_agent_api.py:605`), and `shared/routers/runs.py:256` normalises
# everything the runs API hands back to exactly `user` or `agent`.
#
# `orchestrator` is accepted deliberately rather than by accident: nothing writes it
# today (the Orchestrator's own replies are stored as `agent` with
# `author_id="orchestrator"`), but the column's vocabulary allows it, and anything
# stored under it would be a reply a user was shown — so it is an assistant turn, not
# an artefact. `assistant`/`ai` are kept because a caller holding an OpenAI- or
# LangChain-shaped transcript is a plausible second source and costs nothing to accept.
_USER_ROLES = frozenset({"user", "human"})
_ASSISTANT_ROLES = frozenset({"assistant", "ai", "agent", "orchestrator"})

# The complete set of roles this function drops, named individually rather than left to
# fall out of a whitelist — naming them is the difference between a decision and an
# oversight, and the oversight is what cost every `agent` turn. `system` is a prompt the
# platform supplied to itself; `tool` is machine output addressed to the model that
# asked for it. Neither is something a person wrote or read, and this router decides on
# what the conversation ASKED FOR, which is said in user and assistant turns.
#
# The bounded cost, stated rather than implied: a tool result can carry a fact that
# would sway a routing decision ("the test run failed"), and the assistant turn beside
# it is trusted to say so in prose. That is a judgement, not a guarantee.
#
# A role in none of these three sets raises rather than dropping — see
# `_history_messages`.
_ARTEFACT_ROLES = frozenset({"system", "tool"})

_KNOWN_ROLES = _USER_ROLES | _ASSISTANT_ROLES | _ARTEFACT_ROLES


def _history_messages(history: Any) -> list[BaseMessage]:
    """The recent conversation as LangChain messages, most recent `_HISTORY_LIMIT`.

    Accepts LangChain messages as-is, or mappings with `role` and `content`, which is
    what `shared/services/conversation_service.get_transcript` returns (its other keys
    — `seq`, `author_id`, `model`, ... — are ignored). `content` may be a plain string
    OR a list of typed blocks — the same two shapes `_text_of` reads
    off a response, via the same `_content_text` — because a block list is what several
    providers store for a turn that carried an image or a tool result alongside its
    text, and it is at least as likely a shape for a real caller as a bare string.

    WHAT IS DROPPED, BY NAME. Exactly two roles, `system` and `tool`
    (`_ARTEFACT_ROLES`), because each is transcript machinery rather than something a
    person wrote or read.
    Nothing else is dropped by role: `agent` and `orchestrator` are how THIS platform
    spells an assistant turn (see `_ASSISTANT_ROLES` above), and an earlier version of
    this function accepted only `assistant`/`ai` — so every assistant reply the platform
    has ever stored was silently discarded, and the routing decision was made on the
    user's half of the conversation alone. A role in none of the three sets raises
    rather than being dropped, so the next addition to that column's vocabulary cannot
    repeat this quietly.

    Also dropped: a turn with no text — `content` of `None` (an explicit null, or no
    `content` key at all, which is the canonical shape for an assistant turn that made
    only tool calls) and a turn whose text is empty after extraction. A turn made only
    of tool calls or non-text blocks contributes nothing to a decision made on text;
    that it happened at all is NOT represented, and this is the one knowing omission
    left in this function.

    Everything else raises `TypeError` — an entry that is neither a message nor a
    mapping, a role outside the vocabulary above, and a `content` that is present but is
    neither a string nor a list. Routing on a conversation that was silently truncated
    is a mis-route, and a mis-route looks exactly like a correct route: the class of
    silent failure this engine was rebuilt to end. Two earlier versions of this function
    did precisely that — one dropped a block list on the floor
    (`isinstance(content, str)` was the whole test), the other dropped role `agent` —
    in the function whose docstring argues against it.

    Surfacing a fault costs a failed turn. `ws.py`'s turn loop renders an exception
    raised while serving a turn as a typed `error` the user can see, which is where this
    lands once `route` is called from inside that loop — a property of the call site,
    not one this function can guarantee alone.
    """
    if history is None:
        return []
    if isinstance(history, (str, bytes, Mapping)):
        raise TypeError(
            f"history must be a sequence of messages, not {type(history).__name__}"
        )

    messages: list[BaseMessage] = []
    for entry in list(history)[-_HISTORY_LIMIT:]:
        if isinstance(entry, BaseMessage):
            messages.append(entry)
            continue
        if not isinstance(entry, Mapping):
            raise TypeError(
                "history entries must be LangChain messages or mappings with 'role' "
                f"and 'content'; got {type(entry).__name__}"
            )

        # Role first: an artefact entry is dropped whatever its content is, so an
        # unusual content shape on a turn nobody routes on cannot fail the turn.
        role = str(entry.get("role") or "").strip().lower()
        if role in _ARTEFACT_ROLES:
            continue
        if role not in _KNOWN_ROLES:
            raise TypeError(
                f"history entry has role {role!r}, which this router does not "
                f"recognise; it must be one of {sorted(_KNOWN_ROLES)}. Dropping it "
                f"would route the turn on a conversation missing a message"
            )

        content = entry.get("content")
        if content is None:
            # No `content` key, or an explicit null — the canonical shape for an
            # assistant turn that made only tool calls. That is an absence of text,
            # not a shape we cannot read, so it joins the empty-text drop below
            # rather than failing the turn.
            continue
        text = _content_text(content)
        if text is None:
            raise TypeError(
                "history content must be a string or a list of content blocks; got "
                f"{type(content).__name__} for role {role!r}"
            )
        if not text:
            continue

        if role in _USER_ROLES:
            messages.append(HumanMessage(content=text))
        else:
            messages.append(AIMessage(content=text))
    return messages


def _content_text(content: Any) -> str | None:
    """The text in a `content` value, or `None` if it is a shape we cannot read.

    `content` is a plain string for most providers and a LIST OF TYPED BLOCKS for
    others — the same two shapes on a stored history turn and on a live response,
    which is why both callers come through here rather than each testing
    `isinstance(content, str)` on its own. That local test is what silently dropped a
    block-list history turn.

    The two return values are distinct on purpose: `""` means "a shape we understand
    that carries no text" (an empty string, a block list of images), while `None`
    means "a shape we do not understand at all". `_history_messages` raises on a `None`
    RETURNED FROM HERE and skips on `""`; `_text_of` treats both as no text, because a
    response is the model's output and `_validated` already guarantees the user sees
    something. Fusing them into `""` would take that choice away from both callers.

    A `content` that IS `None` never reaches this function: that is an absence of text
    rather than an unreadable shape, so `_history_messages` drops it before calling.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts).strip()
    return None


def _text_of(response: Any) -> str:
    """The response's text. A naive `str(content)` would put a Python repr in front of
    the user, since this text IS the direct reply. An unreadable shape yields `""`,
    which `_validated` turns into the could-not-choose reply rather than silence."""
    return _content_text(getattr(response, "content", None)) or ""


def _agent_id_from_tool_name(name: Any) -> str | None:
    """The agent id a tool name encodes, or `None` if it is not one of ours.

    Deliberately does NOT check the id against `AGENT_IDS` — `route` does that, in one
    place, so the check also covers a decision that did not come through here. What
    this rejects is a name that is not even shaped like a routing tool: an
    un-prefixed name is not "the id, loosely" — `testing` is a name the model was
    never given, and treating it as `testing` would be guessing at a malformed answer.
    """
    if not isinstance(name, str) or not name.startswith(_TOOL_PREFIX):
        return None
    return name[len(_TOOL_PREFIX):] or None


def _call_field(call: Any, field: str) -> Any:
    """One field of a tool call. LangChain gives dicts; be tolerant of an object."""
    if isinstance(call, Mapping):
        return call.get(field)
    return getattr(call, field, None)


def _decision_from_response(response: Any) -> RoutingDecision:
    """Read the model's answer. Does not validate the id — `route` does (see
    `_validated`), so the strict check has exactly one home and cannot be bypassed by
    a caller that reaches this function directly."""
    tool_calls = list(getattr(response, "tool_calls", None) or [])

    if not tool_calls:
        # No tool call is a real answer, not a failure: most turns are questions,
        # greetings and follow-ups, and starting an agent for those interrupts work.
        reply = _text_of(response)
        return RoutingDecision(
            agent_id=None,
            reason=_ANSWERED_DIRECTLY,
            # Empty reply -> `_validated` supplies one. A `None` here can never
            # reach the caller as silence.
            direct_reply=reply or None,
        )

    if len(tool_calls) > 1:
        # A turn runs one agent and this type holds one id, so picking the first
        # would be a silent guess — the same guess `prefilter` already refuses for a
        # message naming two agents. Ask instead.
        chosen = [_agent_id_from_tool_name(_call_field(c, "name")) for c in tool_calls]
        named = [DISPLAY_NAMES[a] for a in chosen if a in DISPLAY_NAMES]
        listed = " and ".join(named) if named else "more than one agent"
        return RoutingDecision(
            agent_id=None,
            reason="More than one agent was chosen, and a turn runs one.",
            direct_reply=(
                f"That looks like work for {listed}. I can only start one at a time — "
                f"which should go first?"
            ),
        )

    call = tool_calls[0]
    agent_id = _agent_id_from_tool_name(_call_field(call, "name"))
    args = _call_field(call, "args") or {}
    reason = str((args.get("reason") if isinstance(args, Mapping) else "") or "").strip()
    if not reason and agent_id in DISPLAY_NAMES:
        # `reason` is required by the schema, but a model can omit a required
        # argument, and a blank line in the UI is not a reason.
        reason = f"This is work for the {DISPLAY_NAMES[agent_id]} agent."
    return RoutingDecision(agent_id=agent_id, reason=reason, direct_reply=None)


def _validated(
    decision: RoutingDecision, valid_ids: Any = None
) -> RoutingDecision:
    """Enforce the two things a caller may rely on, whatever the model said.

      1. `agent_id` is `None` or one of `valid_ids` (default `AGENT_IDS`, today's
         exact behaviour). The tools are generated from the same set, so an
         invented id should be impossible — but models hallucinate, and an id
         outside it reaches `dispatch.run_agent` as an `error` event for an agent
         the ROUTER chose, which reads as the platform being broken rather than as
         a bad pick. A track-scoped caller passes `agent_ids_for_track(track)`
         here, so this ALSO catches the case where the model picked a real agent
         id that simply is not in this run's track's portfolio — the same refusal
         as a hallucinated one, because from a caller's side they are the same
         failure: an id `dispatch.run_agent` (also track-scoped) will not run.
      2. `agent_id is None` implies a non-empty `direct_reply`. Otherwise the turn
         ends with nothing shown and nothing run — a silent no-op, which is the
         failure this engine exists to remove.
    """
    ids = agent_ids_for_track(DEFAULT_TRACK) if valid_ids is None else valid_ids
    if decision.agent_id is not None and decision.agent_id not in ids:
        return RoutingDecision(
            agent_id=None,
            reason=_COULD_NOT_CHOOSE_REASON,
            direct_reply=_COULD_NOT_CHOOSE_REPLY,
        )
    if decision.agent_id is None and not (decision.direct_reply or "").strip():
        return RoutingDecision(
            agent_id=None,
            reason=decision.reason.strip() or _COULD_NOT_CHOOSE_REASON,
            direct_reply=_COULD_NOT_CHOOSE_REPLY,
        )
    return decision


async def _ask_model(
    text: str,
    *,
    history: Any,
    run_id: str,
    tenant_id: str,
    project_id: str | None,
    model_id: str | None,
    offering_id: str | None,
    system_prompt: str,
    capabilities: Mapping[str, Any] | None = None,
) -> RoutingDecision:
    """Ask the Context Agent which agent should handle `text`.

    `capabilities=None` offers every tool in `REGISTRY` — today's exact behaviour.
    A track-scoped caller passes `registry_for_track(track)`, and the model is then
    bound only the tools for that track's portfolio, matching the roster
    `system_prompt` (built by the caller, from the same map) already describes.

    `system_prompt` is PASSED, not built here. `route` is the only thing that knows
    which agent answered last, and a default of `_system_prompt()` would let a call
    site drop that silently — the routing would still work, just worse, in the exact
    way that produced the ping-pong.

    BYOK, PROJECT-SCOPED, WITH NO ENV FALLBACK — the same contract as
    `dispatch.run_agent`, and for the same reason. `resolve_model_for_run` enforces
    both the project's grant (`effective_project_offerings`) and the project's monthly
    budget (`check_budgets`), and BOTH are keyed on `project_id`: without it a project
    can use a model it was never granted and spend past a cap its Business Unit set.
    This is a real model call on a NEW code path, so resolving without the project id
    here would reopen the gap dispatch just closed, somewhere dispatch's tests cannot
    see it.

    The project id is PASSED, not read from the `_RUN_PROJECT` contextvar the resolver
    falls back to. This module never sets that contextvar, and the router runs BEFORE
    `dispatch.run_agent` — which is the thing that sets it — so ambient state would be
    empty exactly when it mattered.

    If resolution fails, THIS RAISES. No env key, no platform key, no fail-soft to
    "I could not choose": `copilot_api._classify_switch`, the classifier this replaces,
    falls back to a hardcoded `ANTHROPIC_MODEL` + `ANTHROPIC_API_KEY`, which makes a
    laptop succeed where production — which has no such key — fails. Answering "I
    could not choose" would be its own kind of lie: the problem is an unconfigured
    provider, and an administrator sent hunting a routing bug will not find it.
    `ws.py`'s turn loop already renders an exception raised while serving a turn as a
    typed `error` the user can see — which is where this will land once `route` is
    called from inside it. Nothing calls `route` yet, so that is a property of the
    call site still to be written, not something this module can guarantee alone.
    """
    # Built BEFORE resolving, so a malformed `history` from a call site costs a
    # TypeError rather than a model resolution first.
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        *_history_messages(history),
        HumanMessage(content=text),
    ]

    resolved = await resolve_model_for_run(
        tenant_id,
        model_id,
        offering_id=offering_id,
        project_id=project_id,
    )
    bound = _build_llm(resolved).bind_tools(_tool_specs(capabilities))
    response = await guarded_completion(
        resolved,
        bound,
        messages,
        tenant_id=tenant_id,
        run_id=run_id,
        # Cost and usage are read per agent_type. Attributing the routing call to a
        # delivery agent would make that agent's spend permanently wrong.
        agent_type="orchestrator_router",
        config={"metadata": {"user_api_key_alias": resolved.alias}},
    )
    return _decision_from_response(response)


#: Shown when a project's track has no delivery agents built yet — `track_id` in
#: `{"modernization", "rpa_infra", "data_engineering"}` today, per
#: `config.agent_registry.TRACK_PORTFOLIOS`. Not a `route` failure: the track is a
#: real, deliberate choice (§Portfolio 2-4, "target design, not yet built" in
#: `help/multi-track-agent-access-design.md`), so this is an honest status, not an
#: apology for a bug.
_NO_AGENTS_FOR_TRACK_REPLY = (
    "This project's track has no delivery agents built yet, so there is nothing "
    "for me to run. Come back once they ship."
)


async def route(
    text: str,
    *,
    history: Any,
    run_id: str,
    tenant_id: str,
    project_id: str | None,
    model_id: str | None,
    offering_id: str | None,
    last_agent: str | None = None,
    track: str = "greenfield",
) -> RoutingDecision:
    """Decide which agent in `track`'s portfolio handles `text`, or answer directly.

    `prefilter` first: an explicit imperative naming an agent is answered without
    spending a model call, and the reason says so — "because you named it" is the only
    honest thing to tell the user about a decision no intent-reading went into.
    Everything else goes to the Context Agent, which reads the message for MEANING.
    That second half is the whole point of the phase: the retired `stage_switch.py`
    matched an
    agent alias anywhere in the text, so "I need a PRD" routed nowhere and the user had
    to name the agent by hand.

    EVERY AGENT IN `track`'S PORTFOLIO IS A CANDIDATE ON EVERY TURN. There is no
    ordering in this engine — no `STAGE_ORDER.index(active) + 1`, no notion of a next
    agent, nothing about what ran before.

    `track` SCOPES BOTH HALVES. `prefilter` itself stays track-agnostic (it is pure,
    and pinning its regex to one track's names would mean rebuilding it per track on
    every call) — instead, a `prefilter` match outside `track`'s portfolio is treated
    as NO match here, falling through to the model rather than being trusted. The
    model half is scoped directly: its tools and roster come from
    `registry_for_track(track)`, so an agent outside the portfolio is never even
    offered, and `_validated` checks the model's pick against
    `agent_ids_for_track(track)` rather than the full `AGENT_IDS`. Defaults to
    `"greenfield"` — the portfolio Greenfield and Enhancement share — which
    reproduces every existing call site's behaviour exactly: nothing that calls
    `route()` without naming a track sees any change.

    A track whose portfolio is empty (today: `modernization`, `rpa_infra`,
    `data_engineering` — see `TRACK_PORTFOLIOS`) short-circuits before the model is
    ever called: there is nothing to offer it, and a `bind_tools([])` call would be a
    confusing way to say so. `direct_reply` explains why instead.

    `last_agent` does not weaken "every agent in the portfolio is a candidate". It
    names the agent that answered the PREVIOUS turn, and it exists because an agent
    that asked the user a question owns the answer to it: "2" and "main" are not
    routable messages, they are replies. It changes what the model is TOLD, never
    what it is allowed to choose — every agent in the portfolio remains a candidate,
    and `prefilter` still wins outright. `None` is the ordinary first-message case,
    not an error.

    `project_id` is KEYWORD-REQUIRED WITH NO DEFAULT, like `dispatch.run_agent`'s: it
    decides which models this run may use and whose budget it spends, and a default of
    `None` would let a future call site drop project scoping without saying so. It
    comes from the `runs` row, never from the client (see `ws._resolve_run`).

    Guarantees, both enforced in `_validated` regardless of what the model returned:
    `agent_id` is `None` or one of `agent_ids_for_track(track)`, and when it is `None`
    `direct_reply` is non-empty. Not guaranteed: that the agent chosen is the RIGHT
    one — that is a model's judgement, which is why `reason` is shown to the user and
    why `agent.selected` is emitted before any of the agent's text.
    """
    valid_ids = agent_ids_for_track(track)

    named = prefilter(text, valid_ids)
    if named is not None and named in valid_ids:
        return RoutingDecision(
            agent_id=named,
            reason=f"You asked for the {DISPLAY_NAMES[named]} agent by name.",
            direct_reply=None,
        )

    if not valid_ids:
        return RoutingDecision(
            agent_id=None,
            reason=f"No delivery agents are registered for track {track!r} yet.",
            direct_reply=_NO_AGENTS_FOR_TRACK_REPLY,
        )

    capabilities = registry_for_track(track)
    decision = await _ask_model(
        text,
        history=history,
        run_id=run_id,
        tenant_id=tenant_id,
        project_id=project_id,
        model_id=model_id,
        offering_id=offering_id,
        # The one piece of turn-to-turn state this router has. It does not order the
        # agents and does not decide anything; it tells the model that a question is
        # outstanding, which a bare "2" does not carry on its own.
        system_prompt=_system_prompt_with_continuity(last_agent, capabilities, track),
        capabilities=capabilities,
    )
    return _validated(decision, valid_ids)
