"""The publish/reject routes demand the permission belonging to the STAGE IN THE PATH.

WHY THIS NEEDS ITS OWN DEPENDENCY. `require_permission` takes one fixed string, but
`/projects/{project_id}/stages/{stage}/versions/{version}/publish` needs a different
permission per stage. Hardcoding one would either let Requirements' approver sign off a
deployment, or force nine near-identical routes that drift.

TWO FAILURE MODES THIS PINS, both of which look fine from the outside:

  · a stage with no approve permission being waved through, which is how the five
    track agents (not in AGENT_REGISTRY, so no `artifact:approve_*` exists) could
    otherwise be published to
  · one stage's approver being accepted for another stage
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.authz import dependency as dep  # noqa: E402
from shared.authz.permissions import _PHASE_PERMISSION  # noqa: E402
from shared.services.orchestrator.progression import STAGE_ORDER  # noqa: E402

pytestmark = pytest.mark.unit


class _Conn:
    """Enough of an HTTPConnection for the dependency: a path param and a scope."""

    def __init__(self, stage=None):
        self.path_params = {"stage": stage} if stage is not None else {}
        self.scope = {"type": "http"}


@pytest.fixture
def recorded(monkeypatch):
    """Capture the permission string the dependency resolves to, without running the
    real check (which resolves a workspace and writes an audit row)."""
    seen: list[str] = []

    def _fake_require_permission(perm, **kw):
        async def _dep(conn):
            seen.append(perm)
        return _dep

    monkeypatch.setattr(dep, "require_permission", _fake_require_permission)
    return seen


async def test_each_stage_resolves_to_its_own_approve_permission(recorded):
    """THE MAPPING. Asserted against `_PHASE_PERMISSION` rather than a copied list, so
    this cannot pass while the two disagree."""
    for stage in STAGE_ORDER:
        await dep.require_stage_approval()(_Conn(stage))
    assert recorded == [_PHASE_PERMISSION[s] for s in STAGE_ORDER]


async def test_the_permissions_are_actually_distinct(recorded):
    """Guards the test above from being vacuous: if every stage mapped to one string,
    the assertion would still hold and the gate would be worthless."""
    for stage in STAGE_ORDER:
        await dep.require_stage_approval()(_Conn(stage))
    assert len(set(recorded)) == len(STAGE_ORDER)


@pytest.mark.parametrize("stage", [
    "strategy", "migration_mapping", "validation", "data_engineering",
])
async def test_a_track_agent_stage_is_refused(recorded, stage):
    """These four appear in the UI catalogue with owners but are NOT in AGENT_REGISTRY,
    so no `artifact:approve_*` exists for them. Fail closed rather than waving them
    through to a permission nobody can hold. (Discovery left this list when Track 3's
    Phase 1 built it: it now has `artifact:approve_discovery`, held by its owner.)"""
    with pytest.raises(HTTPException) as exc:
        await dep.require_stage_approval()(_Conn(stage))
    assert exc.value.status_code == 403
    assert not recorded


@pytest.mark.parametrize("stage", ["review", "not_a_stage", "", None])
async def test_an_unknown_or_ui_named_stage_is_refused(recorded, stage):
    """`review` is the UI name; the backend stage is `code_review`. Accepting the UI
    spelling here would gate a route on a permission that does not exist."""
    with pytest.raises(HTTPException) as exc:
        await dep.require_stage_approval()(_Conn(stage))
    assert exc.value.status_code == 403
    assert not recorded


async def test_the_refusal_does_not_say_why(recorded):
    """Same opaque 403 as a permission denial. "That stage does not exist" is a probe
    oracle for the pipeline's shape."""
    with pytest.raises(HTTPException) as exc:
        await dep.require_stage_approval()(_Conn("not_a_stage"))
    assert exc.value.detail == "Forbidden"


async def test_it_carries_the_boot_scan_sentinel():
    """Without `__rbac_require_permission__` the route counts as unprotected and
    `assert_all_routes_protected` fails the boot — which is the correct outcome for a
    route with no authz, and the wrong one for this."""
    assert getattr(dep.require_stage_approval(), "__rbac_require_permission__", False)


async def test_websockets_are_skipped_like_the_base_dependency(recorded):
    """Mirrors require_permission: WS connections authenticate via the ws-ticket
    inside the handler, and applying an HTTP permission dep to one crashes the socket."""
    conn = _Conn("design")
    conn.scope = {"type": "websocket"}
    await dep.require_stage_approval()(conn)
    assert not recorded
