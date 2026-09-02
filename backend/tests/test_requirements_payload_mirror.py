"""Requirements the agent writes must reach the table everything else reads.

TWO TABLES, AND THE CHAT WROTE ONLY ONE OF THEM.

`_persist_session_artifacts` patched `AgentSession.requirements_payload`. Everything
that answers "what are this project's requirements" reads `Run.requirements_payload`
instead:

  - `story_artifacts_from_run` — the Requirements page's story list
  - `_fetch_artifacts_for_project` — what the Design agent's read_project_requirements
    returns
  - the pipeline hand-off

So a user could ask the agent to create stories on Jira, watch it report success, and
find the Requirements screen unchanged. The payload existed; it was in a table nothing
on that path consults. Only `ingest_board` — the "Pull stories" button — ever wrote the
Run, which is why re-pulling was the only way to see agent-authored work.

The AgentSession write STAYS. It is session-scoped and is what `build_context` resolves
for an orchestrated run. The Run write is the project-scoped mirror alongside it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.requirements_agent import requirements_agent_api as api  # noqa: E402

TENANT = "81a736f4-cd44-4f63-842c-ae57023d0346"
PROJECT = "f45e7d23-c821-44b3-a88b-6175f67ddef0"
RUN = "22222222-2222-2222-2222-222222222222"
PAYLOAD = {"stories": [{"source_key": "SCRUM-17", "title": "Password reset"}]}


@pytest.fixture
def wiring(monkeypatch):
    """Capture what each half of the persistence writes."""
    seen = {"session": None, "run": None, "chat_run_args": None}

    async def _patch_session(session_id, patch, tenant_id=None):
        seen["session"] = (session_id, patch, tenant_id)

    async def _persist(run_id, artifact_type, payload, tenant_id=None):
        seen["run"] = (run_id, artifact_type, payload, tenant_id)

    async def _chat_run(session, tenant_id, project_id, stage):
        seen["chat_run_args"] = (tenant_id, project_id, stage)
        return RUN

    class _Db:
        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    import shared.services.agent_session_store as store
    import shared.services.artifact_service as art
    import shared.services.chat_artifacts as chat
    import shared.db as shared_db

    monkeypatch.setattr(store, "patch_session_artifacts", _patch_session)
    monkeypatch.setattr(art, "persist_artifact", _persist)
    monkeypatch.setattr(chat, "_get_or_create_chat_run", _chat_run)
    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", lambda _t: _Db())
    return seen


def _with_project(monkeypatch, project_id):
    import config.ws_helper as ws

    monkeypatch.setattr(ws, "get_project_id", lambda: project_id)


# -- the fix -------------------------------------------------------------------


@pytest.mark.unit
async def test_the_payload_reaches_the_run_not_only_the_session(wiring, monkeypatch):
    """THE BUG: only the session was written, and nothing reads the session for a
    project-scoped question."""
    _with_project(monkeypatch, PROJECT)

    await api._persist_session_artifacts(
        session_id="s1", user_id="u1", requirements_payload=PAYLOAD, tenant_id=TENANT
    )

    assert wiring["session"] is not None, "the session write must not be lost"
    run_id, artifact_type, payload, tenant = wiring["run"]
    assert run_id == RUN
    assert artifact_type == "requirements"
    assert payload == PAYLOAD
    assert tenant == TENANT


@pytest.mark.unit
async def test_it_mirrors_onto_the_requirements_chat_run(wiring, monkeypatch):
    """The SAME run the chat's generated files attach to — one per project and stage —
    rather than a second one the story list would then have to choose between."""
    _with_project(monkeypatch, PROJECT)

    await api._persist_session_artifacts(
        session_id="s1", user_id="u1", requirements_payload=PAYLOAD, tenant_id=TENANT
    )

    assert wiring["chat_run_args"] == (TENANT, PROJECT, "requirements")


# -- when there is nothing to mirror onto --------------------------------------


@pytest.mark.unit
async def test_a_conversation_outside_a_project_writes_only_the_session(wiring, monkeypatch):
    """No project means no Run to mirror onto; the session copy is the whole record."""
    _with_project(monkeypatch, None)

    await api._persist_session_artifacts(
        session_id="s1", user_id="u1", requirements_payload=PAYLOAD, tenant_id=TENANT
    )

    assert wiring["session"] is not None
    assert wiring["run"] is None


@pytest.mark.unit
async def test_no_tenant_means_no_mirror(wiring, monkeypatch):
    _with_project(monkeypatch, PROJECT)

    await api._persist_session_artifacts(
        session_id="s1", user_id="u1", requirements_payload=PAYLOAD, tenant_id=None
    )

    assert wiring["run"] is None


@pytest.mark.unit
async def test_a_handoff_only_patch_does_not_mirror(wiring, monkeypatch):
    """There is no requirements payload in that turn — mirroring would write None over
    a real one."""
    _with_project(monkeypatch, PROJECT)

    await api._persist_session_artifacts(
        session_id="s1", user_id="u1", handoff_event={"to": "design"}, tenant_id=TENANT
    )

    assert wiring["run"] is None


# -- it must never break the turn ---------------------------------------------


@pytest.mark.unit
async def test_a_mirror_failure_does_not_raise(monkeypatch):
    """The turn has already produced its answer. Failing here would turn a successful
    conversation into an error over a visibility concern."""
    import shared.db as shared_db
    import shared.services.agent_session_store as store

    async def _ok(*_a, **_kw):
        return None

    monkeypatch.setattr(store, "patch_session_artifacts", _ok)
    _with_project(monkeypatch, PROJECT)

    def _boom(_t):
        raise RuntimeError("database is unreachable")

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", _boom)

    # Must return normally.
    await api._persist_session_artifacts(
        session_id="s1", user_id="u1", requirements_payload=PAYLOAD, tenant_id=TENANT
    )


@pytest.mark.unit
async def test_a_session_patch_failure_still_lets_the_mirror_run(wiring, monkeypatch):
    """The two writes are independent; losing one must not silently lose the other."""
    import shared.services.agent_session_store as store

    async def _boom(*_a, **_kw):
        raise RuntimeError("session store down")

    monkeypatch.setattr(store, "patch_session_artifacts", _boom)
    _with_project(monkeypatch, PROJECT)

    await api._persist_session_artifacts(
        session_id="s1", user_id="u1", requirements_payload=PAYLOAD, tenant_id=TENANT
    )

    assert wiring["run"] is not None, "the Run mirror must not depend on the session write"


# -- the reader must prefer the freshest payload -------------------------------


@pytest.mark.unit
def test_the_story_list_orders_runs_by_when_they_were_written():
    """A chat run is created once per project and REUSED, so its creation time is when
    the user first opened the chat. Ordering by created_at would let a later "Pull
    stories" permanently shadow requirements the agent wrote afterwards onto that older
    run."""
    import inspect

    from shared.routers import artifacts as mod

    src = inspect.getsource(mod.list_artifacts_for_project)
    assert "Run.updated_at.desc()" in src
    assert "Run.created_at.desc()" not in src
