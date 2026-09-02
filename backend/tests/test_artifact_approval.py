"""A document joins the project's record when whoever runs the project says so.

WHAT THIS REPLACED. Storing was gated on a per-turn question in chat: the agent asked
"shall I save this?" and stored on a yes. That put the decision with whoever happened to
be chatting. It now belongs to whoever runs the project.

`store_artifact` writes the row as PENDING and parks the bytes under the tenant's
`_pending` prefix — NOT at the artifact's real path. So a pending document is listed, is
not downloadable, and is not in the project's hierarchy. Approval moves the bytes and
records the decision; rejection deletes them and records the decision.

THE STATUS USED TO BE A CONSTANT. `ArtifactOut.status` was the literal string "approved"
for every artifact because the table had no approval column — a placeholder that read
like a fact. Migration 0040 gave it one.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.models.orm import AuditEvent  # noqa: E402
from shared.routers import artifacts as mod  # noqa: E402
from shared.services.artifact_store import is_pending_path, pending_blob_path  # noqa: E402

TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()


# -- the pending path ----------------------------------------------------------


@pytest.mark.unit
def test_pending_bytes_sit_under_the_tenant_but_off_the_real_path():
    final = f"{TENANT}/bu/proj/design/run/document/hld.pdf"
    pending = pending_blob_path(final)

    assert pending == f"{TENANT}/_pending/bu/proj/design/run/document/hld.pdf"
    # The tenant segment does not move — it is the isolation boundary and
    # `is_blob_path` tests exactly that prefix.
    assert pending.split("/")[0] == str(TENANT)
    assert is_pending_path(pending)
    assert not is_pending_path(final)


@pytest.mark.unit
def test_the_transform_is_reversible_by_construction():
    """Approval derives the destination from the row's blob_path rather than composing
    it a second time, so the two cannot drift apart."""
    final = f"{TENANT}/bu/proj/design/run/document/hld.pdf"
    assert pending_blob_path(final).replace("/_pending/", "/", 1) == final


# -- approval moves the bytes --------------------------------------------------


class _Blob:
    def __init__(self, *, fail=False):
        self.moved: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self._fail = fail

    async def move_blob(self, src, dst, content_type=None):
        if self._fail:
            raise RuntimeError("storage unreachable")
        self.moved.append((src, dst))
        return f"https://acct.blob.core.windows.net/c/{dst}"

    async def delete_blob(self, name):
        if self._fail:
            raise RuntimeError("storage unreachable")
        self.deleted.append(name)
        return True


class _Db:
    def __init__(self):
        self.added: list = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


def _artifact(status="pending"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        tenant_id=TENANT,
        artifact_type="document",
        blob_url=None,
        blob_path=f"{TENANT}/bu/proj/design/run/document/hld.pdf",
        content_type="application/pdf",
        size_bytes=100,
        approval_status=status,
        approved_by=None,
        approved_at=None,
        rejection_reason=None,
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )


def _request(blob):
    return SimpleNamespace(
        state=SimpleNamespace(tenant_id=TENANT, user_id="admin-1"),
        app=SimpleNamespace(state=SimpleNamespace(blob_client=blob)),
    )


@pytest.fixture
def patched(monkeypatch):
    run = SimpleNamespace(project_id=PROJECT, stage="design")

    def _install(artifact):
        async def _for_decision(db, request, artifact_id):
            return artifact, run

        monkeypatch.setattr(mod, "_artifact_for_decision", _for_decision)

    return _install


@pytest.mark.unit
async def test_approval_promotes_the_bytes_to_the_real_path(patched):
    art = _artifact()
    patched(art)
    blob, db = _Blob(), _Db()

    out = await mod.approve_artifact(str(art.id), _request(blob), db)

    assert blob.moved == [(pending_blob_path(art.blob_path), art.blob_path)]
    assert art.approval_status == "approved"
    assert art.blob_url is not None
    assert out.status == "approved"


@pytest.mark.unit
async def test_approval_records_who_decided(patched):
    art = _artifact()
    patched(art)
    db = _Db()

    await mod.approve_artifact(str(art.id), _request(_Blob()), db)

    assert art.approved_by == "admin-1"
    assert art.approved_at is not None
    event = next(o for o in db.added if isinstance(o, AuditEvent))
    assert event.event_type == "artifact_approve"
    assert event.actor_id == "admin-1"


@pytest.mark.unit
async def test_a_failed_move_leaves_the_artifact_pending(patched):
    """A row marked approved whose bytes never moved would advertise a download that
    404s — the same false success the upload warning exists to prevent."""
    art = _artifact()
    patched(art)
    db = _Db()

    with pytest.raises(HTTPException) as e:
        await mod.approve_artifact(str(art.id), _request(_Blob(fail=True)), db)

    assert e.value.status_code == 502
    assert art.approval_status == "pending"
    assert not db.committed


@pytest.mark.unit
async def test_approving_twice_does_not_move_twice(patched):
    """The second move's source has already been deleted; a double-click must not turn
    into a 502."""
    art = _artifact(status="approved")
    patched(art)
    blob = _Blob()

    out = await mod.approve_artifact(str(art.id), _request(blob), _Db())

    assert blob.moved == []
    assert out.status == "approved"


@pytest.mark.unit
async def test_a_rejected_artifact_cannot_be_approved(patched):
    """Its bytes were deleted, so there is nothing left to promote."""
    art = _artifact(status="rejected")
    patched(art)

    with pytest.raises(HTTPException) as e:
        await mod.approve_artifact(str(art.id), _request(_Blob()), _Db())

    assert e.value.status_code == 409


# -- rejection deletes the bytes, keeps the decision ---------------------------


@pytest.mark.unit
async def test_rejection_deletes_the_pending_bytes_and_keeps_the_row(patched):
    """The row survives on purpose: deleting it would erase the fact that the agent
    produced something and somebody declined it."""
    art = _artifact()
    patched(art)
    blob, db = _Blob(), _Db()

    out = await mod.reject_artifact(
        str(art.id), mod.ArtifactDecisionIn(reason="Out of scope"), _request(blob), db
    )

    assert blob.deleted == [pending_blob_path(art.blob_path)]
    assert art.approval_status == "rejected"
    assert art.rejection_reason == "Out of scope"
    assert out.status == "rejected"


@pytest.mark.unit
async def test_a_rejection_that_cannot_clear_its_bytes_still_records_the_decision(patched):
    """The leftover is in the pending area, which nothing serves and no listing reads.
    Losing the decision would be worse."""
    art = _artifact()
    patched(art)
    db = _Db()

    await mod.reject_artifact(
        str(art.id), mod.ArtifactDecisionIn(), _request(_Blob(fail=True)), db
    )

    assert art.approval_status == "rejected"
    assert db.committed


@pytest.mark.unit
async def test_an_approved_artifact_is_deleted_not_rejected(patched):
    art = _artifact(status="approved")
    patched(art)

    with pytest.raises(HTTPException) as e:
        await mod.reject_artifact(
            str(art.id), mod.ArtifactDecisionIn(), _request(_Blob()), _Db()
        )

    assert e.value.status_code == 409
    assert "Delete it instead" in e.value.detail


# -- authorisation -------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("route", ["approve", "reject"])
def test_both_decisions_require_the_approve_permission(route):
    r = next(
        x for x in mod.artifacts_router.routes
        if getattr(x, "path", "") == f"/artifacts/{{artifact_id}}/{route}"
    )
    captured = {
        c.cell_contents
        for dep in r.dependencies
        for c in (dep.dependency.__closure__ or ())
        if isinstance(c.cell_contents, str)
    }
    assert captured == {"approve"}


@pytest.mark.unit
def test_deciding_also_requires_running_the_project():
    """`approve` says the caller takes approval decisions AT ALL; it does not say which
    projects. Without the second check any holder of it anywhere in the tenant could
    accept another team's documents into their record."""
    import inspect

    src = inspect.getsource(mod._artifact_for_decision)
    assert "assert_can_administer_project" in src
