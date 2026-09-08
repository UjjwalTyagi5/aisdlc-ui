"""Which agent spoke is recorded when it speaks, and read back when the chat reopens.

The renderer in `test_transcript_memory.py` can only attribute a turn if something
wrote the attribution down. This is the write side, plus the read path the frontend
uses to replay a reopened conversation.

TWO THINGS WERE WRONG, and the second was invisible until the first was fixed:

1. Nothing ever set `conversation_messages.agent_id` — the column did not exist. `role`
   is `agent` for all nine, so a replayed or forwarded transcript reads as one voice.

2. `GET /runs/{id}/transcript` filled its `stage` field from `author_id`, which is the
   USER's id. Every agent turn in a reopened chat was therefore labelled with the
   person who typed at it. Nobody noticed because the only alternative on offer was
   also wrong.
"""
import pytest


# ── the write side ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_agent_that_replied_is_recorded(monkeypatch):
    from agents_orchestrator.orchestrator2 import sessions

    seen = {}

    async def _persist(run_id, role, content, *, tenant_id, author_id, **kwargs):
        seen.update(role=role, agent_id=kwargs.get("agent_id"))

    monkeypatch.setattr(sessions.cs, "persist_turn", _persist)
    await sessions.record_turn(
        "r1", "agent", "cloned the repo",
        tenant_id="t1", user_id="u1", agent_id="development",
    )
    assert seen == {"role": "agent", "agent_id": "development"}


@pytest.mark.asyncio
async def test_a_user_turn_is_attributed_to_no_agent(monkeypatch):
    from agents_orchestrator.orchestrator2 import sessions

    seen = {}

    async def _persist(run_id, role, content, *, tenant_id, author_id, **kwargs):
        seen.update(agent_id=kwargs.get("agent_id"))

    monkeypatch.setattr(sessions.cs, "persist_turn", _persist)
    await sessions.record_turn("r1", "user", "main", tenant_id="t1", user_id="u1")
    assert seen == {"agent_id": None}


@pytest.mark.asyncio
async def test_the_socket_records_the_agent_that_actually_answered(monkeypatch):
    """The wiring. `record_turn` accepting the field proves nothing if `ws.py` never
    passes it — the same shape of gap as the four wrapper omissions before it."""
    import inspect

    from agents_orchestrator.orchestrator2 import ws

    src = inspect.getsource(ws)
    # The delivery-agent reply must carry the resolved agent id.
    assert 'agent_id=agent_id' in src, (
        "ws.py records agent turns without saying which agent answered"
    )


def test_the_orchestrators_own_reply_is_not_filed_as_one_of_the_nine():
    """A direct reply from the router is the Orchestrator, not a delivery agent.

    Filing it as `agent` with no id makes it indistinguishable from a pre-migration
    turn; filing it as one of the nine would tell the next agent that a delivery agent
    said something it did not.
    """
    import inspect

    from agents_orchestrator.orchestrator2 import ws

    src = inspect.getsource(ws)
    assert '"orchestrator", decision.direct_reply' in src, (
        "the router's own answer is still recorded as an unattributed agent turn"
    )


# ── the read side ────────────────────────────────────────────────────────────


def test_the_transcript_endpoint_labels_a_turn_with_its_agent_not_its_typist():
    """`stage` drives the badge on a replayed turn. It was `author_id` — the user."""
    import inspect

    from shared.routers import runs

    src = inspect.getsource(runs.get_run_transcript)
    assert 'r.get("author_id")' not in src, (
        "the replayed chat still labels agent turns with the user who typed at them"
    )
    assert 'r.get("agent_id")' in src


def test_the_transcript_service_returns_the_attribution_it_stores():
    """A column written and never projected is a column nobody can read."""
    import inspect

    from shared.services import conversation_service as cs

    assert '"agent_id"' in inspect.getsource(cs.get_transcript)



# ── the model and the loader have to agree ───────────────────────────────────


def test_the_orm_declares_every_field_the_transcript_loader_reads():
    """CAUGHT LIVE, AFTER EVERYTHING ELSE WAS GREEN.

    The migration added `conversation_messages.agent_id` and the loader read
    `row.agent_id`, but the ORM model was never given the column — an earlier edit
    failed silently and was not retried. Every test above passes dicts, so nothing
    noticed; the first real database read raised
    `AttributeError: 'ConversationMessage' object has no attribute 'agent_id'`, which
    `handoff_context` then reported to the agent as "the conversation could not be
    read".

    A fake that models rows as dicts can never catch a missing mapped column. This
    asserts against the mapper itself.
    """
    from shared.models.orm import ConversationMessage

    mapped = set(ConversationMessage.__mapper__.columns.keys())
    for field in ("seq", "role", "agent_id", "content", "session_id", "tenant_id"):
        assert field in mapped, (
            f"`{field}` is read by transcript.load_run_transcript but the ORM does "
            f"not map it — the read will raise on the first real row"
        )
