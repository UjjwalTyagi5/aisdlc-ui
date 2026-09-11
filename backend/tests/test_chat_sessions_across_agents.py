"""The project overview can ask for every agent's chats at once.

WHY THE PARAMETER HAD TO CHANGE. `list_sessions` required an `agent_id`, because the
only caller was a stage page's rail — one agent, its own history. The project overview
asks a different question: the person's latest conversations on this project, whichever
agent they were with. Nine calls merged and re-sorted in the browser would rebuild an
ordering the database already has, and would still be wrong at the edges — each call
returns its own newest fifty, so the tenth conversation overall can be missing from a
merge of nine complete answers.

WHAT MUST NOT CHANGE WITH IT. `agent_id` narrows when given; a stage rail must keep
seeing exactly its own agent's sessions and nobody else's. And neither form may cross
the two boundaries that matter: another person's chats, or another project's.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services import conversation_service as cs  # noqa: E402

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """Captures the statement instead of running it — the question here is what was
    asked for, not what Postgres would return."""

    def __init__(self, store):
        self._store = store

    async def execute(self, stmt):
        self._store["stmt"] = stmt
        return _Result([])


class _Ctx:
    def __init__(self, store):
        self._store = store

    async def __aenter__(self):
        return _Session(self._store)

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def asked(monkeypatch):
    """The WHERE clause `list_sessions` built, as text."""
    store: dict = {}
    monkeypatch.setattr(cs, "_ctx", lambda tenant_id: _Ctx(store))
    yield store


def _where(store) -> str:
    return str(store["stmt"].whereclause)


@pytest.mark.asyncio
async def test_no_agent_named_means_every_agent(asked):
    """THE HEADLINE. The overview asks once and gets one ordering."""
    await cs.list_sessions("t1", created_by="u1", project_id=uuid.uuid4())

    assert "agent_id" not in _where(asked), (
        "an unnamed agent must not become a filter — it would match no session at all"
    )


@pytest.mark.asyncio
async def test_naming_an_agent_still_narrows_to_it(asked):
    """NON-VACUITY, and the stage rail's whole behaviour: one agent's history."""
    await cs.list_sessions("t1", created_by="u1", agent_id="design")

    assert "agent_id" in _where(asked)


@pytest.mark.asyncio
async def test_an_empty_agent_id_is_not_a_filter(asked):
    """`""` reaches here from a query string that carried the key with no value. Treated
    as a name it matches nothing, and the overview would render "No chats yet" over a
    project full of them."""
    await cs.list_sessions("t1", created_by="u1", agent_id="")

    assert "agent_id" not in _where(asked)


@pytest.mark.asyncio
async def test_both_forms_are_still_scoped_to_one_person_and_one_project(asked):
    """The widening is about AGENTS. Dropping either of these would turn a convenience
    into somebody else's conversations on screen."""
    project = uuid.uuid4()

    for agent in (None, "design"):
        await cs.list_sessions("t1", created_by="u1", agent_id=agent, project_id=project)
        clause = _where(asked)

        assert "created_by" in clause, f"agent={agent!r} lost the owner filter"
        assert "project_id" in clause, f"agent={agent!r} lost the project filter"
        assert "scope_type" in clause, f"agent={agent!r} lost the agent-scope filter"


@pytest.mark.asyncio
async def test_the_newest_conversation_comes_first(asked):
    """"Recent chats" is the entire premise of the panel."""
    await cs.list_sessions("t1", created_by="u1")

    assert "updated_at DESC" in str(asked["stmt"]).replace("\n", " ")
