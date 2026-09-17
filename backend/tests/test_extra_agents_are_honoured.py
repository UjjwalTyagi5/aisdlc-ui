"""An extra agent granted to a person is an agent they can open.

THE LIVE FAILURE (15 Sep 2026). A Security Engineer holding the QA role on TEST
Project was granted Code Review and Security from the Members page ("Extra agent
access" → granted). On their project page both stayed padlocked, "Request access"
was the only thing they could do, and the chat gate would have refused them too.

`role_bindings.extra_agents` was written by three things — the Members menu, the
project-creation dialog, and the governance effect that approves an agent-access
request — and read by none of the access decisions: not `check_agent_access` (the
chat gates), not the page (a static role × agent table). A grant that changes
nothing tells the admin it worked.

Pinned here:
  * the access decision honours the person's extra agents, in the UI's spelling
    (`review`) as well as the registry's (`code_review`);
  * an explicit person-level override of "none" still wins — it is the one way to
    say "not this person, whatever they were granted";
  * `/projects/{id}/agent-access/me` answers from the same decision, keyed by the
    UI's phase ids, so the page shows what the API allows.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from shared.authz import agent_access as aa

TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
PROJECT = "ed20b947-360d-4881-9a83-52decc68210a"
USER = "db41380b-174c-4a17-a3c4-1f563e4bec6f"


class _Db:
    """Answers the two override lookups from a dict; records nothing else."""

    def __init__(self, person=None, role=None):
        self.person = person or {}  # agent_id → involvement
        self.role = role or {}

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "agent_access_overrides" in sql and "user_id = :u" in sql:
            inv = self.person.get(params["a"])
        elif "agent_access_overrides" in sql and "role = :r" in sql:
            inv = self.role.get(params["a"])
        elif "extra_agents FROM role_bindings" in sql:
            return SimpleNamespace(first=lambda: None)  # no binding → no extras
        else:
            raise AssertionError(sql)
        row = SimpleNamespace(involvement=inv) if inv is not None else None
        return SimpleNamespace(first=lambda: row)


async def _involvement(db, agent_id, *, role="qa", extras):
    with patch.object(aa, "extra_agents_for", AsyncMock(return_value={aa._agent_id(e) for e in extras})):
        return await aa.resolve_involvement(
            db, tenant_id=TENANT, project_id=PROJECT, role=role, user_id=USER, agent_id=agent_id,
        )


async def test_a_qa_reaches_only_testing_by_default():
    db = _Db()
    assert await _involvement(db, "testing", extras=set()) == "owner"
    assert await _involvement(db, "code_review", extras=set()) == "none"
    assert await _involvement(db, "security", extras=set()) == "none"


async def test_an_extra_agent_is_reachable_in_either_spelling():
    db = _Db()
    # stored as the Members page wrote it: the UI's phase ids
    assert await _involvement(db, "code_review", extras={"review", "security"}) == "use"
    assert await _involvement(db, "review", extras={"review", "security"}) == "use"
    assert await _involvement(db, "security", extras={"review", "security"}) == "use"
    assert await _involvement(db, "deployment", extras={"review", "security"}) == "none"
    assert await aa.check_agent_access(
        db, tenant_id=TENANT, project_id=PROJECT, role="qa", user_id=USER, agent_id="code_review",
    ) is False, "with no grant the gate still refuses"


async def test_a_person_level_override_of_none_beats_an_extra_grant():
    db = _Db(person={"security": "none"})
    assert await _involvement(db, "security", extras={"security"}) == "none"


async def test_an_extra_grant_beats_a_role_level_override():
    db = _Db(role={"security": "none"})
    assert await _involvement(db, "security", extras={"security"}) == "use"


async def test_the_owners_own_agent_is_not_demoted_by_a_redundant_grant():
    """A grant lists what a person reaches BEYOND their role; their own agent stays
    theirs to own. The Members menu marks it "role" and never writes it, but a
    request approved for one's own agent could — and must not turn owner into use."""
    db = _Db()
    assert await _involvement(db, "testing", extras={"testing"}) == "owner"


def test_the_phase_alias_covers_the_one_agent_whose_ids_differ():
    assert aa._agent_id("review") == "code_review"
    assert aa._agent_id("code_review") == "code_review"
    assert aa._agent_id("security") == "security"
    assert aa.AGENT_TO_PHASE == {"code_review": "review"}


async def test_extra_agents_for_reads_the_live_project_binding():
    seen = {}

    class _Db2:
        async def execute(self, stmt, params=None):
            seen["sql"] = str(stmt)
            seen["params"] = params
            return SimpleNamespace(first=lambda: SimpleNamespace(extra_agents=["review", "security", ""]))

    out = await aa.extra_agents_for(_Db2(), project_id=PROJECT, user_id=USER)
    assert out == {"code_review", "security"}
    assert "scope_kind = 'project'" in seen["sql"] and "status = 'active'" in seen["sql"]
    assert "expires_at" in seen["sql"], "a lapsed binding grants nothing"
    assert seen["params"] == {"u": USER, "p": PROJECT}


async def test_extra_agents_for_is_empty_for_a_slug_or_no_binding():
    class _None:
        async def execute(self, stmt, params=None):
            return SimpleNamespace(first=lambda: None)

    assert await aa.extra_agents_for(_None(), project_id="payments-portal", user_id=USER) == set()
    assert await aa.extra_agents_for(_None(), project_id=PROJECT, user_id=USER) == set()


# ── the endpoint ─────────────────────────────────────────────────────────────


async def test_my_agent_access_reports_the_reach_the_gate_will_apply():
    from shared.routers import project_scoped as ps

    request = SimpleNamespace(state=SimpleNamespace(tenant_id=TENANT, user_id=USER, permissions=[]))
    db = _Db()
    with patch.object(ps, "_project_or_404", AsyncMock(return_value=SimpleNamespace(id=PROJECT))), \
            patch("shared.authz.effective_role.effective_platform_role", AsyncMock(return_value="qa")), \
            patch.object(aa, "extra_agents_for", AsyncMock(return_value={"code_review", "security"})):
        out = await ps.my_agent_access(PROJECT, request, db)

    assert out["role"] == "qa"
    assert out["extraAgents"] == ["review", "security"], "the UI's phase ids"
    assert out["reach"]["testing"] == "owner"
    assert out["reach"]["review"] == "use", "keyed as the page knows it, not code_review"
    assert "code_review" not in out["reach"]
    assert out["reach"]["security"] == "use"
    assert out["reach"]["development"] == "none"
