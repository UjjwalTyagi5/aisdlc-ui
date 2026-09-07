"""The run's conversation, rendered for the next agent's prompt.

WHY THIS EXISTS. A user worked with the Development agent across many turns — cloned
the ADO repo, found a duplicate table, changed its colours to orange, pushed, opened a
PR — then asked Requirements for story tickets "of the same change table to orange":

    "I don't have any context about a 'change table to orange' modification from our
     conversation."

`handoff_context` fed agents DELIVERABLES and nothing else. That conversation produced
no document — correctly, it was chat — so Requirements was handed `""`. Probed against
the live run: transcript stored (10 turns), deliverables 0, handoff 0 characters.

    "I want the orchestrator to have a proper memory system for each run ... I can go
     from any agent to any agent to any agent, and the context should remain, like it
     generally does with an LLM."

THE SELECTION RULE, and why it is not just "the newest N turns". The budget is
guaranteed PER AGENT: the most recent turn of every agent that has spoken survives,
whatever else is dropped. A global recency window alone would push a long Development
thread out of context the moment Requirements got chatty — the reported bug wearing a
different hat. So:

  1. every agent's LATEST turn is reserved, with the user turn just before it, because
     an answer with its question removed is a fragment;
  2. the remaining budget is filled backwards from the newest turn, so the exchange
     being answered is always whole;
  3. what is left out is DECLARED. A conversation with a silent hole in it invites an
     agent to reason about a sequence that never happened.

Only the LATEST turn per agent is reserved, not all of them: an agent that has spoken
twenty times would otherwise starve the other eight.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable

from sqlalchemy import select

import shared.db as shared_db
from shared.models.orm import ConversationMessage

from .deliverables import DISPLAY_NAME

logger = logging.getLogger(__name__)

#: The guaranteed slice for one agent's most recent turn. Generous on purpose — the
#: requirement is that "at least one run of each agent is visible to the next agent",
#: and an agent's reply that has been cut to a sentence is visible without being
#: useful. A real Development turn (file list, diff, PR summary) runs 1–3 KB.
PER_AGENT_TRANSCRIPT_CHARS = 6_000

#: The whole conversation's budget. Sized so all nine agents can hold their guaranteed
#: slice at once (9 × 6,000 = 54,000) with room left for the recent exchange —
#: `test_the_budget_is_generous_enough_for_one_turn_from_each_of_the_nine` pins that
#: relationship rather than the numbers.
MAX_TRANSCRIPT_CHARS = 64_000

_ELISION = "\n\n… {n} earlier turn(s) elided to fit …\n\n"
_TRUNCATED = "\n… [turn truncated to fit]"

#: What each `role` is called in the rendering. `agent` is resolved to the agent's own
#: display name when `agent_id` says which; these are the fallbacks.
_ROLE_LABEL = {
    "user": "User",
    "orchestrator": "Orchestrator",
    "system": "System",
    "tool": "Tool",
    # A turn from before migration 0045, or one nobody attributed. Deliberately not
    # any of the nine: inventing attribution tells the next agent that a particular
    # agent said something it did not.
    "agent": "Agent",
}


def _label(turn: dict) -> str:
    role = str(turn.get("role") or "agent")
    if role == "agent":
        agent_id = turn.get("agent_id")
        if agent_id:
            return f"{DISPLAY_NAME.get(agent_id, agent_id)} agent"
    return _ROLE_LABEL.get(role, role.title())


def _clip(text: str, limit: int) -> str:
    """Shorten to `limit`, saying so. Silent truncation is how an agent answers
    confidently from half a document."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(_TRUNCATED))].rstrip() + _TRUNCATED


def _rendered(turn: dict, limit: int) -> str:
    return f"**{_label(turn)}:** {_clip(str(turn.get('content') or ''), limit)}"


def render(
    turns: Iterable[dict],
    *,
    budget: int = MAX_TRANSCRIPT_CHARS,
    per_agent: int = PER_AGENT_TRANSCRIPT_CHARS,
) -> str:
    """The conversation, oldest first, within `budget`.

    Returns `""` for a run that has no turns — which is a real answer, not a failure.
    """
    turns = [t for t in turns if (t.get("content") or "").strip()]
    if not turns:
        return ""
    turns = sorted(turns, key=lambda t: t.get("seq") or 0)
    by_seq = {t.get("seq"): t for t in turns}

    # ── 1. reserve the latest turn of every agent that has spoken ────────────
    latest_per_agent: dict[str, dict] = {}
    for turn in turns:
        agent_id = turn.get("agent_id")
        if agent_id:
            latest_per_agent[agent_id] = turn  # sorted ascending, so last wins

    # The guarantee is "one turn from every agent survives", so when the reservations
    # cannot all fit at full size the SLICE shrinks rather than an agent being dropped.
    # Trimming the rendered output instead — which is what this did first — cuts from
    # the oldest end and silently removes exactly the early agents the guarantee exists
    # to protect. Found by mutation: with a generous budget the two behaved identically,
    # so nothing was defending the difference.
    reserved = len(latest_per_agent) or 1
    allowance = max(1, min(per_agent, budget // reserved))

    keep: dict[Any, int] = {}          # seq -> character limit for that turn
    for turn in latest_per_agent.values():
        keep[turn["seq"]] = allowance
        # The question this answers. An answer with its question removed is a
        # fragment, and the reported failure was exactly a missing referent.
        previous = by_seq.get((turn.get("seq") or 0) - 1)
        if previous is not None and previous.get("role") == "user":
            keep.setdefault(previous["seq"], per_agent)

    # ── 2. fill backwards from the newest, so the live exchange is whole ─────
    spent = sum(min(len(str(by_seq[s].get("content") or "")), lim) for s, lim in keep.items())
    for turn in reversed(turns):
        if turn["seq"] in keep:
            continue
        cost = min(len(str(turn.get("content") or "")), per_agent)
        if spent + cost > budget:
            continue
        keep[turn["seq"]] = per_agent
        spent += cost

    # ── 3. render chronologically, declaring what was left out ──────────────
    blocks: list[str] = []
    skipped = 0
    for turn in turns:
        if turn["seq"] not in keep:
            skipped += 1
            continue
        if skipped:
            blocks.append(_ELISION.format(n=skipped).strip())
            skipped = 0
        blocks.append(_rendered(turn, keep[turn["seq"]]))
    if skipped:
        blocks.append(_ELISION.format(n=skipped).strip())

    # No trim here. `allowance` is sized so the reservations fit, and trimming the
    # rendered string cuts from the OLDEST end — which removes exactly the early
    # agents the per-agent guarantee exists to keep. The labels and elision markers
    # add a little on top of the content budget; that overhead is bounded by the turn
    # count and is the price of the transcript being readable at all.
    return "\n\n".join(blocks)


async def load_run_transcript(run_id: str, tenant_id: str) -> list[dict]:
    """This run's turns, oldest first.

    Read inside the caller's tenant GUC session AND with an explicit tenant predicate,
    for the reason `context._load_run_artifacts` gives: RLS is inert in this deployment
    (the app connects as a rolbypassrls superuser), so the predicate is what actually
    separates tenants today.

    The conversation session id IS the run id — `sessions.ensure_session` creates it
    that way — so no join is needed to get from one to the other.

    RAISES on a failed read. The caller decides what a missing conversation means, and
    it must not be able to confuse it with a run that has not said anything: reporting
    a database outage as "no prior conversation" is the defect this engine's context
    layer was rebuilt to remove.

    AN UNUSABLE ID IS NOT A FAILED READ. It returns `[]`, logged at WARNING, matching
    `context._load_run_artifacts` exactly — the answer to "what was said on this run"
    is well-defined ("nothing"), and no read failed to produce it. Raising here instead
    made `handoff_context` tell an agent the conversation was unreadable whenever a
    caller passed a malformed id, which is a louder lie than the silence it replaced.
    """
    try:
        session_uuid = uuid.UUID(str(run_id))
        tenant_uuid = uuid.UUID(str(tenant_id))
    except (ValueError, AttributeError, TypeError):
        logger.warning(
            "orchestrator2 transcript read got an unusable id (run=%r tenant=%r)",
            run_id, tenant_id,
        )
        return []
    async with shared_db.get_db_session_for_tenant(str(tenant_id)) as db:
        rows = (
            await db.execute(
                select(ConversationMessage)
                .where(
                    ConversationMessage.session_id == session_uuid,
                    ConversationMessage.tenant_id == tenant_uuid,
                )
                .order_by(ConversationMessage.seq)
            )
        ).scalars().all()
    return [
        {
            "seq": row.seq,
            "role": row.role,
            "agent_id": row.agent_id,
            "content": row.content,
        }
        for row in rows
    ]
