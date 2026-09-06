"""What one agent hands the next: everything the run already holds.

`handoff_context(run_id, tenant_id, target_agent)` renders a compact markdown block
describing the artifacts that exist on a run, labelled by the agent that produced
each one, and returns `""` when the run genuinely holds none.

This replaces `agents_orchestrator/orchestrator/copilot_api.py::_upstream_context`,
and it exists because that function had three defects that this module is shaped
around not reproducing.

1. IT SLICED THE PIPELINE, SO AGENTS COULD NOT SEE EACH OTHER'S WORK
--------------------------------------------------------------------
The old function built its column list from `progression.STAGE_ORDER[:idx]` — only
the stages positioned BEFORE the target in a hardcoded order. In this engine there
is no such order: any of the nine agents can run at any time, chosen from the
conversation, so Design may well run after Development. Under the old rule Design
would then be shown nothing about the development work, and would ask the user to
re-supply what the run already had.

So this module includes EVERY deliverable present on the run, whatever produced it
and whenever. Nothing here filters by position, and nothing here has a notion of a
"next" or "prior" agent. `AGENT_IDS` is iterated for a STABLE PRESENTATION ORDER — so
the same run renders identically twice — and for nothing else: it decides the sequence
sections appear in, never which sections exist. Every agent is considered on every
call.

2. IT READ THE RUN THROUGH AN RLS-BYPASSING SESSION WITH NO TENANT PREDICATE
----------------------------------------------------------------------------
The old function used `get_db_session_superuser()` and matched on `Run.id` alone, so
a run id belonging to another tenant read back — and its artifacts were then fed
straight into an agent's prompt. `_load_run_artifacts` mirrors
`orchestrator2/ws.py::_resolve_run` instead: the read runs inside the caller's
tenant GUC session AND carries an explicit tenant predicate — since Phase 4 that
predicate is `OrchestratorDeliverable.tenant_id`, written out in
`deliverables._load`. Either alone would do on paper; RLS is inert in this deployment
(the app connects as a rolbypassrls superuser), so today the explicit predicate is the
one actually doing the work — which is exactly why both are written.

A run that does not belong to `tenant_id` is indistinguishable from one that does
not exist — both yield `""`, and the caller learns nothing about what exists
elsewhere.

3. IT REPORTED A FAILED READ AS "THERE IS NO PRIOR WORK"
---------------------------------------------------------
The old function wrapped the query in `except Exception: return ""`. A database
outage therefore reached the agent as an empty run, and the agent re-asked the user
for work that already existed — a failure disguised as normal operation, which is
the bug class this rebuild exists to remove.

The split here is deliberate, and it is drawn between *the run has nothing to say*
and *we could not find out*:

  · RETURNS `""` — the run exists and every artifact column is empty; the run does
    not exist; the run belongs to another tenant; `run_id` or `tenant_id` is not a
    usable id; or nothing readable could be attributed to one of the nine agents. In
    all of those the honest answer to "what has been produced on this run, for you?"
    is "nothing", and no read failed to produce it. The last two are logged at
    WARNING because they can only be caller bugs, even though the answer is
    well-defined.

  · RAISES `ContextUnavailableError` — the read itself failed (connection refused,
    timeout, a driver error, anything else out of the session or the query). The
    original exception is chained as `__cause__`. This matches the posture
    `_resolve_run` already takes on this socket: a turn that cannot establish what
    the run holds is refused rather than run blind. In `ws.py` an exception in a turn
    becomes an `error` event and the socket stays up, so the user is told, which is
    precisely what the old `return ""` prevented.

SIZE
----
`MAX_CONTEXT_CHARS` caps the WHOLE returned string — see the constant for the number
and the reasoning. The old function capped each artifact at 8000 characters with no
total, so nine of them could emit 72,000. Here the budget left after the wrapper is
divided EQUALLY among the artifacts present, so one oversized payload cannot starve
the others, and any artifact that is shortened says so in the rendered output. A
silently shortened context is defect 3 in another costume.

IT READS DELIVERABLES, NOT THE RUN'S ARTIFACT COLUMNS
-----------------------------------------------------
Since Phase 4 the source is `orchestrator_deliverables`, not `runs.*_artifacts`. The
columns are what the STANDALONE agents write; the Orchestrator's agents share their
names and capability and are a different thing, so their output has its own table with
no approval concept, and this module never names a column of the other one. A test
asserts that by inspecting this module's source.

ONLY THE NEWEST VERSION PER AGENT IS HANDED FORWARD
---------------------------------------------------
Nothing is ever overwritten — an agent can be re-run at any time and every version
stays visible in the Deliverables tab. But an agent handed two versions of one PRD has
to guess which is current, and it will sometimes guess wrong, so `latest_per_agent`
resolves that here rather than leaving it to the model.

WHAT REPLACED THE IMPORT-TIME COLUMN CHECK
------------------------------------------
The previous version proved AT IMPORT that every agent resolved a real `runs` column.
Storage is now one table, so the equivalent proofs are that the `agent_id` CHECK
constraint equals `AGENT_IDS` (tests/orchestrator2/test_deliverables_schema.py) and
that `DISPLAY_NAME` covers `AGENT_IDS` (checked at import in `deliverables.py`). The
guarantee moved; it was not dropped.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from agents_orchestrator.orchestrator2.registry import AGENT_IDS, UnknownAgentError
from config.agent_registry import AGENT_REGISTRY

# Imported eagerly, unlike the agent graphs `registry.py` defers: `shared/models/orm.py`
# is pure schema by contract ("Do NOT import from shared.db here", "Do NOT import from
# config.env") so it drags in no connection, and the import-time column check below
# cannot run without it.

logger = logging.getLogger(__name__)


class ContextUnavailableError(Exception):
    """The run's artifacts could not be read, so what the run holds is UNKNOWN.

    Deliberately distinct from an empty result. The code this replaces returned `""`
    for both, which told the agent "there is no prior work" whenever the database was
    merely unreachable — and the agent then re-asked the user for things that already
    existed. Callers must not catch this and substitute `""`.
    """


# ── the size budget ──────────────────────────────────────────────────────────
#
# 24,000 characters is roughly 6,000 tokens at the usual ~4 chars/token, so this
# block costs a bounded and predictable slice of the target agent's window — under
# 5% of a 128k-token context — leaving the conversation and the agent's own
# reasoning the rest. It is a ceiling on the WHOLE returned string, not per
# artifact: the function this replaces capped each artifact at 8,000 with no total,
# so nine of them could hand an agent 72,000 characters.
MAX_CONTEXT_CHARS: int = 24_000

_HEADER = (
    "--- WORK ALREADY ON THIS RUN ---\n\n"
    "Everything below already exists on this run, labelled by the agent that produced\n"
    "it. Agents here run in whatever order the conversation asks for, so this is what\n"
    "EXISTS rather than what came earlier in some fixed sequence. Treat it as the\n"
    "source of truth and do not ask the user to re-supply anything already present.\n\n"
)
_FOOTER = "\n--- END WORK ALREADY ON THIS RUN ---\n\n"

_FENCE_OPEN = "\n```json\n"
_FENCE_CLOSE = "\n```\n"

# Announced whenever an artifact is shortened. `_NOTE_RESERVE` is the space held back
# per section for it: the cap arithmetic subtracts the RESERVE, and the note actually
# emitted is never longer, so the total can never exceed MAX_CONTEXT_CHARS.
_NOTE_TEMPLATE = "_(truncated: showing the first {shown:,} of {total:,} characters)_\n"
_NOTE_FALLBACK = "_(truncated: only the start of this artifact is shown)_\n"
_NOTE_RESERVE = 120

# Backstop, so the guarantee in the docstring is true on every path and not merely
# intended. The arithmetic in `_render` already keeps the block inside the cap; this
# fires only if a future edit breaks that, and it still says it truncated.
_HARD_TRIM_NOTE = "\n… [context truncated to fit the size cap]\n"

# Smallest per-artifact share worth rendering. Checked at import against the worst
# case (all nine agents, longest headings) so `_render` can never compute a share of
# zero or less and silently emit nothing but scaffolding.
_MIN_SHARE_CHARS = 500


def _display_name(agent_id: str) -> str:
    """The agent's user-facing name, from the registry — `plan` is the Project
    Manager Agent, and that is what a reader of the block must see."""
    definition = AGENT_REGISTRY.get(agent_id)
    return getattr(definition, "name", None) or agent_id


def _heading(agent_id: str, *, target_agent: str) -> str:
    """The section heading for one artifact.

    The target agent's OWN artifact is included like any other (see
    `handoff_context`) but marked, so it can tell its earlier output from someone
    else's rather than treating both as external input.
    """
    name = _display_name(agent_id)
    if agent_id == target_agent:
        return f"### {name} artifact (this agent's own earlier output on this run)\n"
    return f"### {name} artifact\n"


def _section_scaffold_chars() -> int:
    return len(_FENCE_OPEN) + len(_FENCE_CLOSE) + _NOTE_RESERVE


def _worst_case_share() -> int:
    """The per-artifact share when all nine agents have produced something and every
    heading is the longest one they can have."""
    widest = max(
        len(_heading(agent_id, target_agent=agent_id)) for agent_id in AGENT_IDS
    )
    count = len(AGENT_IDS)
    scaffold = (
        len(_HEADER) + len(_FOOTER) + count * (widest + _section_scaffold_chars())
    )
    return (MAX_CONTEXT_CHARS - scaffold) // count


# Import-time, like `registry.py`'s REGISTRY/STAGE_ORDER assertion: shrinking the cap
# or growing the wrapper until an artifact gets no usable room must break here, not
# hand agents a block of headings with nothing under them.
assert len(_NOTE_FALLBACK) <= _NOTE_RESERVE, "the truncation note must fit its reserve"
assert _worst_case_share() >= _MIN_SHARE_CHARS, (
    f"MAX_CONTEXT_CHARS={MAX_CONTEXT_CHARS} leaves only {_worst_case_share()} chars "
    f"per artifact once the wrapper is paid for; at least {_MIN_SHARE_CHARS} needed"
)


def _as_uuid(value: str) -> uuid.UUID | None:
    """The value as a UUID, or None when it is not one.

    Returning None rather than the raw string (as `copilot_api._as_run_uuid` does)
    is what lets the reader refuse outright instead of pushing caller-supplied text
    into a UUID comparison.
    """
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _render_body(value: Any) -> str:
    """One deliverable, as the agent should read it.

    A deliverable's content is MARKDOWN — the same text the user saw. Passing it
    through `json.dumps` would wrap it in quotes and escape every newline, so the
    receiving agent gets `"# Heading\n\n- a bullet"` instead of a document. It is
    technically present and materially harder to read, which is the kind of loss that
    never shows up as an error.

    Anything that is somehow not a string still goes through `json.dumps` with
    `default=str`, so a malformed value cannot take the turn down over formatting.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, default=str)


def _render(sections: list[tuple[str, str]], target_agent: str) -> str:
    """Assemble the block, keeping the total inside `MAX_CONTEXT_CHARS`.

    The budget left after the wrapper is split EQUALLY between the artifacts present,
    rather than spent first-come-first-served, so a 200KB payload from one agent
    cannot push every other agent's work out of the block.

    Why the total stays inside the cap: the scaffold charged below reserves
    `_NOTE_RESERVE` for every section whether or not it truncates, and the note
    actually emitted is never longer than that reserve, so
    `header + footer + Σ(heading + fences + note) + Σ min(body, share)` is at most
    `scaffold + count * share`, which is at most `MAX_CONTEXT_CHARS` by construction.
    """
    headings = {agent_id: _heading(agent_id, target_agent=target_agent)
                for agent_id, _ in sections}
    scaffold = (
        len(_HEADER)
        + len(_FOOTER)
        + sum(len(headings[agent_id]) + _section_scaffold_chars()
              for agent_id, _ in sections)
    )
    share = (MAX_CONTEXT_CHARS - scaffold) // len(sections)

    parts: list[str] = [_HEADER]
    for agent_id, body in sections:
        parts.append(headings[agent_id])
        parts.append(_FENCE_OPEN)
        if len(body) > share:
            note = _NOTE_TEMPLATE.format(shown=share, total=len(body))
            if len(note) > _NOTE_RESERVE:
                note = _NOTE_FALLBACK
            parts.append(body[:share])
            parts.append(_FENCE_CLOSE)
            parts.append(note)
            logger.info(
                "orchestrator2 context shortened %s's artifact from %d to %d chars",
                agent_id, len(body), share,
            )
        else:
            parts.append(body)
            parts.append(_FENCE_CLOSE)
    parts.append(_FOOTER)

    block = "".join(parts)
    if len(block) > MAX_CONTEXT_CHARS:
        logger.warning(
            "orchestrator2 context exceeded its own cap (%d > %d) — hard-trimmed; "
            "the per-artifact share arithmetic is wrong",
            len(block), MAX_CONTEXT_CHARS,
        )
        block = block[: MAX_CONTEXT_CHARS - len(_HARD_TRIM_NOTE)] + _HARD_TRIM_NOTE
    return block


async def _load_run_artifacts(run_id: str, tenant_id: str) -> dict[str, Any]:
    """What this run already holds, keyed by the agent that produced it.

    READS DELIVERABLES, NOT THE RUN'S `*_artifacts` COLUMNS. Those columns are what
    the STANDALONE agents write. The Orchestrator's agents share their names and
    capability and are a different thing, so their output lives in its own table with
    no approval concept, and mixing the two is exactly what that separation prevents.

    ONLY THE NEWEST VERSION PER AGENT. Every version stays visible in the panel — an
    agent can be re-run at any time and nothing is ever overwritten — but handing a
    downstream agent two versions of one PRD makes it guess which is current, and it
    will sometimes guess wrong.

    THE READ IS TENANT-SCOPED TWICE, and that is the point: inside
    `get_db_session_for_tenant` (which sets the tenant GUC, so row-level security
    applies) AND with an explicit tenant predicate in `deliverables._load`. The
    function this replaces used the superuser session — under which RLS is not a
    backstop at all — and filtered on `Run.id` alone, so another tenant's run read
    back and its artifacts went into an agent's prompt.

    Returns `{}` when the run holds nothing, does not exist, belongs to another
    tenant, or is named by an id that cannot address a row. The middle two are the
    same answer on purpose: "not yours" must not be distinguishable from "not there".

    RAISES `ContextUnavailableError` when the read itself fails, chaining the cause.
    A failed read is NOT an empty run, and reporting it as one is the defect this
    module was written to remove: the agent hears "no prior work exists" and re-asks
    the user for things that are already done.
    """
    from agents_orchestrator.orchestrator2 import deliverables

    try:
        latest = await deliverables.latest_per_agent(run_id, tenant_id)
    except Exception as exc:  # noqa: BLE001 — unknown != empty; see the class docstring
        logger.warning(
            "orchestrator2 could not read deliverables for run=%s tenant=%s: %s — "
            "raising rather than reporting an empty run",
            run_id, tenant_id, exc,
        )
        raise ContextUnavailableError(
            "the run's existing work could not be read"
        ) from exc

    return {
        agent_id: row.get("content") or ""
        for agent_id, row in latest.items()
        if (row.get("content") or "").strip()
    }


async def handoff_context(run_id: str, tenant_id: str, target_agent: str) -> str:
    """Everything this run already holds, rendered for `target_agent`'s prompt.

    Returns a markdown block naming each artifact's author, or `""` when the run
    genuinely holds none. `""` never means the read failed — that raises
    `ContextUnavailableError`.

    EVERY ARTIFACT ON THE RUN IS INCLUDED, whichever agent produced it and whenever.
    There is no ordering in this engine: the router picks any of the nine from the
    conversation, so Design may run after Development, and the function this replaces
    would have shown Design nothing about the development work because Development
    sits later in a hardcoded list. `target_agent` therefore changes the LABELLING
    here and nothing about the selection.

    THE TARGET'S OWN ARTIFACT IS INCLUDED, marked as its own. It is the one section
    that could defensibly be dropped as redundant — an agent resuming a thread
    already has its own output in its checkpointer — but not every agent's
    checkpointer survives a process restart (`registry.py` compiles `testing` against
    a `MemorySaver`), and the run row does. Dropping it would be a silent information
    loss on exactly the path where it is least visible; the marking in `_heading`
    keeps it from reading as somebody else's work.

    Raises `UnknownAgentError` for an id that is not one of the nine, matching
    `registry.get_capability`: an invented target is a caller bug, and answering it
    with a cheerful empty string is how the old engine hid missing agents.

    The result is at most `MAX_CONTEXT_CHARS` characters, and any artifact shortened
    to fit says so in the text.
    """
    if target_agent not in AGENT_IDS:
        raise UnknownAgentError(
            f"'{target_agent}' is not a known agent id. Known ids: "
            f"{sorted(AGENT_IDS)}"
        )

    artifacts = await _load_run_artifacts(run_id, tenant_id)

    for key in artifacts:
        if key not in AGENT_IDS:
            # Only the nine can be attributed to an author, and an unattributed block
            # in an agent's prompt is worse than no block. Loud, because the only way
            # to get here is a bug in whatever produced the mapping.
            logger.warning(
                "orchestrator2 context dropped an artifact under unknown key %r", key
            )

    sections: list[tuple[str, str]] = []
    # AGENT_IDS is iterated for a stable rendering sequence ONLY — every key is
    # considered, so nothing is filtered by where an agent sits in it.
    for agent_id in AGENT_IDS:
        value = artifacts.get(agent_id)
        if not value:
            # Null, or an empty object/list/string: the column exists but the agent
            # has produced nothing to hand on.
            continue
        sections.append((agent_id, _render_body(value)))

    if not sections:
        return ""
    return _render(sections, target_agent)
