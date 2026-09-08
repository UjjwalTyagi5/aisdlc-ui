"""The run remembers, and every agent inherits the conversation.

REPORTED, with the transcript to prove it. A user worked with the Development agent
across many turns — cloned the ADO repo, found the duplicate table, changed its colours
to orange, pushed, opened a PR — then asked the Requirements agent to write story
tickets "of the same change table to orange", and got:

    "I don't have any context about a 'change table to orange' modification from our
     conversation."

I probed the live run behind it. The transcript WAS stored (10 turns). The run had 0
deliverables. And `handoff_context(run, "requirements")` returned **0 characters**.

The cause: `handoff_context` fed agents DELIVERABLES and nothing else. Development's
work was a conversation, not a document — correctly, under the capture rule — so the
next agent was handed an empty string. It was not being forgetful. It was never told.

"I want the orchestrator to have a proper memory system for each run ... I can go from
 any agent to any agent to any agent, and the context should remain, like it generally
 does with an LLM."

THE BUDGET IS PER AGENT, not just a global cap: "make it a generous budget for each
agent so that at least one run of each agent is visible to the next agent." So the
newest turn of EVERY agent that has spoken survives selection, whatever else is
dropped — a global recency window alone would push a long Development thread out
entirely the moment Requirements got chatty, which is the reported bug wearing a
different hat.
"""
import pytest

from agents_orchestrator.orchestrator2 import transcript as tr


def _turn(seq, role, content, agent_id=None):
    return {"seq": seq, "role": role, "content": content, "agent_id": agent_id}


def _dev_thread():
    """The reported conversation, in miniature."""
    return [
        _turn(1, "user", "pull the code from ADO, I want to make some changes"),
        _turn(2, "agent", "Cloned Company. Which branch?", "development"),
        _turn(3, "user", "main"),
        _turn(4, "agent", "There is a duplicate table using amber. Ready to change it?",
              "development"),
        _turn(5, "user", "yes go ahead"),
        _turn(6, "agent", "Changed the duplicate table colours to orange and pushed "
                          "feature/duplicate-table-orange.", "development"),
        _turn(7, "user", "i want to create story tickets on azure for this change"),
    ]


def test_a_run_with_no_turns_renders_nothing():
    assert tr.render([]) == ""


def test_the_conversation_is_carried_forward():
    """The whole point. The words the user typed and the agent's answers both survive."""
    out = tr.render(_dev_thread())
    assert "duplicate table" in out
    assert "orange" in out
    assert "pull the code from ADO" in out


def test_every_agent_turn_says_which_agent_said_it():
    """`role` is only `agent`. Fed forward unlabelled, a transcript reads as one voice,
    and "the Development agent already did this" is exactly the fact that went
    missing."""
    out = tr.render(_dev_thread())
    assert "Development" in out
    assert out.count("Development") >= 3, "each of its turns should be attributed"


def test_the_user_is_named_as_the_user():
    out = tr.render(_dev_thread())
    assert "User" in out


def test_an_unattributed_agent_turn_is_not_credited_to_anyone():
    """Rows written before migration 0045 carry no `agent_id`. Inventing one would tell
    the next agent that a particular agent said something it did not."""
    out = tr.render([_turn(1, "agent", "something from before the column existed")])
    assert "Agent" in out
    for name in ("Development", "Requirements", "Security"):
        assert name not in out


def test_the_orchestrator_speaking_is_not_one_of_the_nine():
    """A direct reply from the router is the Orchestrator, not a delivery agent."""
    out = tr.render([_turn(1, "orchestrator", "I can help with that.")])
    assert "Orchestrator" in out


# ── the budget ───────────────────────────────────────────────────────────────


def _long(agent_id, seq, marker):
    return _turn(seq, "agent", f"{marker} " + ("x" * 20_000), agent_id)


def test_the_newest_turn_of_every_agent_survives_a_tight_budget():
    """The guarantee the user asked for, stated directly."""
    turns = [
        _long("requirements", 1, "PRD-MARKER"),
        _long("design", 2, "HLD-MARKER"),
        _long("development", 3, "CODE-MARKER"),
        _long("security", 4, "SCAN-MARKER"),
        _turn(5, "user", "now write the stories"),
    ]
    # Deliberately too small for four full slices: 4 x 6,000 would need 24,000, and
    # plain recency would spend it on the newest two and drop Requirements and Design
    # entirely. The guarantee shrinks each slice instead, so all four still speak.
    out = tr.render(turns, budget=14_000, per_agent=6_000)
    for marker in ("PRD-MARKER", "HLD-MARKER", "CODE-MARKER", "SCAN-MARKER"):
        assert marker in out, f"{marker} was dropped; that agent became invisible"


def test_an_agents_older_turns_give_way_before_its_newest_one():
    """Guaranteed is the LATEST turn per agent. An agent that has spoken twenty times
    does not get twenty guaranteed slots — that would starve the other eight."""
    turns = [_long("development", i, f"OLD-{i}") for i in range(1, 6)]
    turns.append(_long("development", 6, "NEWEST"))
    out = tr.render(turns, budget=25_000)
    assert "NEWEST" in out
    assert "OLD-1" not in out


def test_a_single_enormous_turn_cannot_eat_the_whole_budget():
    """One agent's 20,000-character reply must not crowd out the other eight."""
    turns = [
        _long("development", 1, "DEV"),
        _long("requirements", 2, "REQ"),
    ]
    out = tr.render(turns, budget=20_000, per_agent=4_000)
    assert "DEV" in out and "REQ" in out
    assert len(out) <= 20_000 + 500, "the rendering overran its budget"


def test_a_shortened_turn_says_it_was_shortened():
    """Silent truncation is how an agent confidently answers from half a document."""
    out = tr.render([_long("development", 1, "DEV")], budget=5_000, per_agent=2_000)
    assert "DEV" in out
    assert "…" in out or "truncated" in out.lower() or "elided" in out.lower()


def test_skipped_turns_are_declared_rather_than_silently_missing():
    """A conversation with a hole in it, presented as continuous, invites an agent to
    reason about a sequence that never happened."""
    turns = [_long("development", i, f"T{i}") for i in range(1, 8)]
    out = tr.render(turns, budget=12_000, per_agent=3_000)
    assert "elided" in out.lower()
    # The COUNT, which only the per-gap marker can produce — a single generic "earlier
    # turns" banner would satisfy a laxer assertion while telling the agent nothing
    # about where the hole is.
    import re
    assert re.search(r"\d+ earlier turn", out), (
        "the transcript does not say how many turns are missing"
    )


def test_the_most_recent_exchange_is_always_present():
    """Whatever else is dropped, the turn being answered must be there."""
    turns = [_long("development", i, f"T{i}") for i in range(1, 6)]
    turns.append(_turn(6, "user", "THE-QUESTION-BEING-ANSWERED"))
    out = tr.render(turns, budget=10_000)
    assert "THE-QUESTION-BEING-ANSWERED" in out


def test_turns_are_rendered_oldest_first():
    """It is a conversation. Reversed, an agent reads the answer before the question."""
    out = tr.render(_dev_thread())
    assert out.index("pull the code from ADO") < out.index("i want to create story")


def test_the_budget_is_generous_enough_for_one_turn_from_each_of_the_nine():
    """Pins the constants against the requirement that set them: nine agents, at least
    one turn each, without relying on any of them being terse."""
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    assert tr.MAX_TRANSCRIPT_CHARS >= tr.PER_AGENT_TRANSCRIPT_CHARS * len(AGENT_IDS), (
        "the total budget cannot fit one guaranteed turn from every agent, so the "
        "guarantee the user asked for is not one"
    )


@pytest.mark.asyncio
async def test_the_handoff_carries_the_conversation_not_only_documents(monkeypatch):
    """End to end through the function agents actually receive.

    This is the reported bug: a run with a real conversation and no deliverables handed
    the next agent `""`.
    """
    from agents_orchestrator.orchestrator2 import context as ctx

    async def _no_artifacts(run_id, tenant_id):
        return {}

    async def _turns(run_id, tenant_id):
        return _dev_thread()

    monkeypatch.setattr(ctx, "_load_run_artifacts", _no_artifacts)
    monkeypatch.setattr(ctx, "load_run_transcript", _turns)

    out = await ctx.handoff_context("r1", "t1", "requirements")
    assert out, "a run with a conversation handed the next agent nothing"
    assert "orange" in out
    assert "Development" in out


@pytest.mark.asyncio
async def test_a_run_with_neither_documents_nor_conversation_is_still_empty(monkeypatch):
    """`""` has to keep meaning "this run holds nothing", or the caller cannot tell a
    fresh run from a failed read."""
    from agents_orchestrator.orchestrator2 import context as ctx

    async def _nothing(run_id, tenant_id):
        return {}

    async def _no_turns(run_id, tenant_id):
        return []

    monkeypatch.setattr(ctx, "_load_run_artifacts", _nothing)
    monkeypatch.setattr(ctx, "load_run_transcript", _no_turns)
    assert await ctx.handoff_context("r1", "t1", "requirements") == ""


@pytest.mark.asyncio
async def test_a_transcript_read_failure_does_not_lose_the_documents(monkeypatch):
    """The two sources are independent. Losing the conversation must not also lose the
    PRD — an agent with half the context and no sign of it is the failure this whole
    module is shaped around."""
    from agents_orchestrator.orchestrator2 import context as ctx

    async def _artifacts(run_id, tenant_id):
        return {"requirements": "# PRD\n\nThe billing rework." + "x" * 500}

    async def _boom(run_id, tenant_id):
        raise RuntimeError("the transcript table is unreachable")

    monkeypatch.setattr(ctx, "_load_run_artifacts", _artifacts)
    monkeypatch.setattr(ctx, "load_run_transcript", _boom)

    out = await ctx.handoff_context("r1", "t1", "design")
    assert "billing rework" in out
    assert "could not be read" in out.lower(), (
        "the agent was not told the conversation is missing, so it will read the "
        "documents as the whole story"
    )
