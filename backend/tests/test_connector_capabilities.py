"""A grant records an intention; the connector decides what is possible.

Those two disagreed silently and in both directions — Slack granted for `read` could
do nothing, Figma granted for `write` could never write — and in both cases the hub
showed a healthy grant. These tests pin the rule that closes it, and the far more
important rule about when NOT to apply it.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz import connector_capabilities as cap
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.governance.effects import EffectNotAvailable, apply_on_approve

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


# ── introspection ────────────────────────────────────────────────────────────

def test_a_notify_sink_supports_write_only():
    """Slack declares no read capabilities at all. `read` on it is a grant to do
    nothing, which is exactly what our least-privilege default would have given."""
    assert cap.supported_modes("slack") == frozenset({"write"})
    assert cap.supported_level("slack") == "write"


def test_a_board_supports_both():
    assert cap.supported_modes("jira") == frozenset({"read", "write"})
    assert cap.supported_level("jira") == "read_write"


def test_an_unknown_kind_is_unknown_not_empty():
    """None and frozenset() mean different things, and conflating them would make
    every MCP server and every unrecognised kind ungrantable."""
    assert cap.supported_modes("no_such_connector") is None
    assert cap.supported_level("no_such_connector") is None


def test_an_unknown_kind_never_refuses():
    """THE RULE THAT MATTERS MOST. Refusing on absent knowledge would make an
    unconstructable connector ungrantable — and there is one on this branch."""
    for level in ("read", "write", "read_write"):
        assert cap.unsupported_reason("no_such_connector", level) is None


@pytest.mark.parametrize(
    "kind,level,refused",
    [
        ("slack", "read", True),
        ("slack", "read_write", True),
        ("slack", "write", False),
        ("jira", "read", False),
        ("jira", "write", False),
        ("jira", "read_write", False),
    ],
)
def test_only_unsupported_levels_are_refused(kind, level, refused):
    assert (cap.unsupported_reason(kind, level) is not None) is refused


def test_the_refusal_says_what_to_do_instead():
    """Shown to an admin trying to give somebody access — it has to name the fix."""
    reason = cap.unsupported_reason("slack", "read")
    assert "no read capabilities" in reason
    assert "write" in reason


def test_a_write_only_board_is_warned_about_but_allowed():
    """Permitted and partly hollow. create_item needs no id; the other three writes
    act on an item somebody had to find first, and only a read can find one."""
    assert cap.unsupported_reason("jira", "write") is None
    warnings = cap.warnings_for("jira", "write")
    assert warnings and "id" in warnings[0]


def test_no_warning_where_there_is_nothing_to_warn_about():
    assert cap.warnings_for("jira", "read_write") == []
    assert cap.warnings_for("slack", "write") == []


# ── through the API ──────────────────────────────────────────────────────────

@pytest.fixture
async def tree():
    org, unit, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    admin = f"orgadmin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Cap Test')"
        ), {"i": org, "s": "cap-" + org[:8]})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'payments', 'Payments')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Ledger')"
        ), {"i": proj, "w": unit, "t": org})
    from shared.authz.grant import grant_role
    await grant_role(admin, org, "org_admin", tenant_id=org, scope_kind="organization")
    yield {"org": org, "unit": unit, "project": proj, "admin": admin}


def _client():
    return TestClient(process_api.app)


def _admin(t):
    return {"Authorization": "Bearer " + create_access_token(
        user_id=t["admin"], tenant_id=t["org"], permissions=["admin:*"])}


# THE GRANT DOOR NO LONGER HAS A LEVEL TO CHECK (migration 0024).
#
# Five tests stood here asserting that granting resolved a capability-aware default
# and refused an unsupported level: Slack granted with no level chosen came out
# `write` rather than a useless `read`, Jira came out `read`, and `slack` + `read`
# was a 422. A grant carries no level now, so there is nothing at this door to check.
#
# The check did not disappear — it MOVED to where the level is now chosen, which is
# the project's per-stage picker. That is what the next test covers, and losing it
# would mean a stage could be set to Slack-read and get a connector that silently
# permits nothing.


def test_a_grant_no_longer_carries_or_checks_a_level(tree):
    """Slack was ungrantable to anybody while the old default fought the check.

    Now it grants like anything else, because granting says nothing about level.
    """
    for kind in ("slack", "jira"):
        r = _client().post("/integrations/access", headers=_admin(tree),
                           params={"kind": "connector", "id": kind,
                                   "workspaceId": tree["unit"]})
        assert r.status_code == 200, r.text
        assert "access" not in r.json()


def test_a_stage_mode_the_connector_cannot_honour_is_refused(tree):
    """WHERE THE CHECK LIVES NOW. Slack implements no reads, so a stage set to
    `read` on Slack would hold a connector that can do nothing while the picker
    shows a configured chip. Refused at the door that writes it."""
    from shared.routers.projects import _validated_modes

    with pytest.raises(ValueError) as exc:
        _validated_modes({"requirements::connector::slack": "read"})
    assert "no read capabilities" in str(exc.value)


def test_a_stage_mode_the_connector_can_honour_is_accepted(tree):
    from shared.routers.projects import _validated_modes

    ok = {"requirements::connector::slack": "write",
          "development::connector::jira": "both"}
    assert _validated_modes(ok) == ok


def test_an_mcp_stage_mode_is_not_capability_checked(tree):
    """MCP servers have no manifest. Checking them against one would make every
    MCP assignment impossible."""
    from shared.routers.projects import _validated_modes

    ref = str(_uuid.uuid4())
    modes = {"development::mcp::" + ref: "read"}
    assert _validated_modes(modes) == modes


def test_an_mcp_grant_is_not_capability_checked(tree):
    """MCP servers come through the same table and have no manifest. Checking them
    against one would make every MCP grant impossible."""
    r = _client().post("/integrations/access", headers=_admin(tree),
                       params={"kind": "mcp", "id": str(_uuid.uuid4()),
                               "workspaceId": tree["unit"], "access": "read_write"})
    assert r.status_code == 200, r.text


def test_narrowing_a_project_to_an_unsupported_level_is_refused(tree):
    """The project-wide default keeps its own copy of the check — it is a second
    writer of a level, and both writers must refuse what the connector cannot do."""
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "slack", "workspaceId": tree["unit"]})
    r = c.put(f"/projects/{tree['project']}/integrations/access", headers=_admin(tree),
              json={"kind": "connector", "targetId": "slack", "access": "read"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "unsupported_access_level"


# ── through the approval door ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approving_an_unsupported_level_is_refused(tree):
    """The third door. A request raised before capabilities were known would
    otherwise write a hollow grant by the one route that skips the checking."""
    request = {
        "type": "connector_access",
        "tenantId": tree["org"],
        "workspaceId": tree["unit"],
        "projectId": None,
        "currentApproverRole": "org_admin",
        "payload": {"targetId": "slack", "kind": "connector",
                    "access": "read", "scope": "unit"},
    }
    async with get_db_session_for_tenant(tree["org"]) as s:
        with pytest.raises(EffectNotAvailable) as exc:
            await apply_on_approve(s, request)
    assert "read capabilities" in str(exc.value)
