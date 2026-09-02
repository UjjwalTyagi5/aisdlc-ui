"""Deleting an artifact removes the bytes AND the row — or removes neither.

WHY THIS ENDPOINT EXISTS. Artifacts were immutable and permanent: `patch_artifact` is an
accepted-but-no-op stub honouring the immutability decision, and there was no delete at
all. That is right for an approved design document and wrong for what the table actually
accumulates. The motivating case came from a live session: an upload failed with
`AuthorizationPermissionMismatch`, `store_artifact` degraded as designed and wrote the
row with `blob_url = None`, and the UI then listed a downloadable document that could
never be downloaded — with no way to remove it.

THE ORDERING IS THE INVARIANT: audit -> blob -> row.

  - The AuditEvent goes first because it is the only thing that survives the operation.
    It is written inside the request transaction, so a later failure rolls it back and no
    event is left claiming a deletion that did not happen.
  - The blob goes second. If it cannot be deleted the row must STAY: bytes nothing points
    at are worse than a row the user can retry, because the row is at least visible.
  - The row goes last.

A blob that was already missing is SUCCESS, not failure — that is precisely the
failed-upload case the endpoint exists to clean up.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.models.orm import AuditEvent  # noqa: E402
from shared.routers import artifacts as mod  # noqa: E402

TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()


class _FakeBlob:
    """Records deletes. `missing` makes the blob absent; `fail` makes storage broken."""

    def __init__(self, *, missing: bool = False, fail: bool = False):
        self.deleted: list[str] = []
        self._missing = missing
        self._fail = fail

    async def delete_blob(self, blob_name: str) -> bool:
        if self._fail:
            raise RuntimeError("storage account is unreachable")
        self.deleted.append(blob_name)
        return not self._missing


class _FakeSession:
    """Just enough AsyncSession, and it RECORDS THE ORDER of operations.

    The order is the thing under test, so it cannot be inferred from final state — a
    passing end state is reachable by several orderings, only one of which is safe.
    """

    def __init__(self):
        self.added: list = []
        self.deleted: list = []
        self.calls: list[str] = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj):
        self.added.append(obj)
        self.calls.append(f"add:{type(obj).__name__}")

    async def flush(self):
        self.calls.append("flush")

    async def delete(self, obj):
        self.deleted.append(obj)
        self.calls.append(f"delete:{type(obj).__name__}")

    async def commit(self):
        self.committed = True
        self.calls.append("commit")

    async def rollback(self):
        self.rolled_back = True
        self.calls.append("rollback")


def _artifact(*, blob_path: str | None, blob_url: str | None = "https://x/y"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        tenant_id=TENANT,
        artifact_type="document",
        blob_url=blob_url,
        blob_path=blob_path,
        content_type="application/pdf",
        size_bytes=1234,
    )


def _request(blob_client):
    return SimpleNamespace(
        state=SimpleNamespace(tenant_id=TENANT, user_id="u-1"),
        app=SimpleNamespace(state=SimpleNamespace(blob_client=blob_client)),
    )


@pytest.fixture
def patched(monkeypatch):
    """Bypass the two lookups; this file is about what happens AFTER authorisation."""
    run = SimpleNamespace(project_id=PROJECT, stage="design")

    def _install(artifact):
        async def _get(db, artifact_id, tenant_id):
            return artifact, run

        async def _visible(db, request, project_id):
            return None

        monkeypatch.setattr(mod, "_get_artifact_or_404", _get)
        monkeypatch.setattr(mod, "_assert_project_visible", _visible)

    return _install


def _blob_path(leaf: str = "design.pdf") -> str:
    """A path store_artifact would have written — is_blob_path tests the tenant prefix."""
    return f"{TENANT}/{uuid.uuid4()}/document/{leaf}"


# -- the ordering invariant ----------------------------------------------------


@pytest.mark.unit
async def test_the_audit_event_is_written_before_the_bytes_are_destroyed(patched):
    art = _artifact(blob_path=_blob_path())
    patched(art)
    db, blob = _FakeSession(), _FakeBlob()

    await mod.delete_artifact(str(art.id), _request(blob), db)

    assert db.calls.index("add:AuditEvent") < db.calls.index("delete:SimpleNamespace")
    # And flushed before the row goes, so the event is in the transaction either way.
    assert db.calls.index("flush") < db.calls.index("delete:SimpleNamespace")
    assert db.committed


@pytest.mark.unit
async def test_the_audit_event_records_who_and_what(patched):
    art = _artifact(blob_path=_blob_path("brd.pdf"))
    patched(art)
    db = _FakeSession()

    await mod.delete_artifact(str(art.id), _request(_FakeBlob()), db)

    event = next(o for o in db.added if isinstance(o, AuditEvent))
    assert event.event_type == "artifact_delete"
    assert event.actor_id == "u-1"
    assert event.resource_id == str(art.id)
    assert event.payload["project_id"] == str(PROJECT)
    assert event.payload["blob_path"] == art.blob_path
    assert event.payload["had_stored_bytes"] is True


# -- the motivating case: a row whose bytes never arrived ----------------------


@pytest.mark.unit
async def test_an_artifact_whose_upload_failed_can_still_be_deleted(patched):
    """THE WHOLE POINT. blob_url is None because the upload failed and store_artifact
    degraded. Requiring a successful blob delete here would make exactly the useless
    rows the undeletable ones."""
    art = _artifact(blob_path=_blob_path(), blob_url=None)
    patched(art)
    db, blob = _FakeSession(), _FakeBlob(missing=True)

    await mod.delete_artifact(str(art.id), _request(blob), db)

    assert art in db.deleted
    assert db.committed


@pytest.mark.unit
async def test_a_row_with_no_stored_bytes_is_recorded_as_such(patched):
    """Destroying a real document and clearing a dangling row are different events and
    must not read identically in the audit log."""
    art = _artifact(blob_path=_blob_path(), blob_url=None)
    patched(art)
    db = _FakeSession()

    await mod.delete_artifact(str(art.id), _request(_FakeBlob(missing=True)), db)

    event = next(o for o in db.added if isinstance(o, AuditEvent))
    assert event.payload["had_stored_bytes"] is False


# -- a storage failure keeps the row ------------------------------------------


@pytest.mark.unit
async def test_a_blob_that_cannot_be_deleted_leaves_the_row_alone(patched):
    """Orphaned bytes nothing points at are worse than a row the user can retry."""
    art = _artifact(blob_path=_blob_path())
    patched(art)
    db, blob = _FakeSession(), _FakeBlob(fail=True)

    with pytest.raises(HTTPException) as e:
        await mod.delete_artifact(str(art.id), _request(blob), db)

    assert e.value.status_code == 502
    assert art not in db.deleted
    assert db.rolled_back and not db.committed


@pytest.mark.unit
async def test_the_storage_error_type_does_not_leak_into_the_response(patched):
    """An Azure error can carry a SAS token or the account URL in its message."""
    art = _artifact(blob_path=_blob_path())
    patched(art)

    with pytest.raises(HTTPException) as e:
        await mod.delete_artifact(str(art.id), _request(_FakeBlob(fail=True)), _FakeSession())

    assert "storage account is unreachable" not in str(e.value.detail)


# -- legacy rows hold a local filesystem path, not a blob name -----------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "legacy", [r"C:\pwc_work\outputs\brd.docx", "/var/app/generated/u1/output/brd.docx"]
)
async def test_a_legacy_local_path_is_never_sent_to_azure(patched, legacy):
    """Passing one to delete_blob asks Azure to delete a blob literally named
    `C:\\pwc_work\\...`, which answers "not found" and reads like success."""
    art = _artifact(blob_path=legacy)
    patched(art)
    db, blob = _FakeSession(), _FakeBlob()

    await mod.delete_artifact(str(art.id), _request(blob), db)

    assert blob.deleted == []
    assert art in db.deleted          # the row still goes


@pytest.mark.unit
async def test_deleting_works_when_blob_storage_is_unconfigured(patched):
    """AZURE_BLOB_ACCOUNT_URL unset is the common local-dev state; app.state.blob_client
    is None there. The row must still be removable."""
    art = _artifact(blob_path=_blob_path())
    patched(art)
    db = _FakeSession()

    await mod.delete_artifact(str(art.id), _request(None), db)

    assert art in db.deleted and db.committed


# -- authorisation -------------------------------------------------------------


@pytest.mark.unit
def test_the_route_requires_the_delete_permission_not_merely_view():
    """artifact:view is the read-only floor every tenant member holds, including
    `contributor`. Gating deletion on it would let the weakest role destroy an approved
    design."""
    route = next(
        r for r in mod.artifacts_router.routes
        if getattr(r, "path", "") == "/artifacts/{artifact_id}" and "DELETE" in getattr(r, "methods", set())
    )
    # require_permission returns a closure; the permission it captured is the thing
    # worth asserting, and repr() of the dependency does not show it.
    captured = {
        c.cell_contents
        for dep in route.dependencies
        for c in (dep.dependency.__closure__ or ())
        if isinstance(c.cell_contents, str)
    }
    assert captured == {"artifact:delete"}


@pytest.mark.unit
def test_delete_is_granted_to_delivery_roles_and_withheld_from_the_read_only_floor():
    from shared.authz.permissions import _ROLE_PERMISSIONS

    holders = {r for r, p in _ROLE_PERMISSIONS.items() if "artifact:delete" in p}
    assert holders == {
        "project_admin", "ba", "architect", "developer", "qa",
        "security_engineer", "devops_engineer", "data_engineer", "scrum_master",
    }
    # contributor is the floor; bu_admin is governance and performs no delivery act.
    assert "contributor" not in holders and "bu_admin" not in holders


@pytest.mark.unit
def test_delete_is_not_implied_by_export():
    """Exporting takes a COPY out of the platform; deleting destroys the original. If
    delete were folded into export, every role that may read a design could destroy it."""
    from shared.authz.permissions import _PERMISSION_CATALOG

    assert "artifact:delete" in _PERMISSION_CATALOG
    assert "artifact:export" in _PERMISSION_CATALOG


# -- the blob client's own contract -------------------------------------------


@pytest.mark.unit
async def test_delete_blob_treats_a_missing_blob_as_done_not_as_an_error():
    from azure.core.exceptions import ResourceNotFoundError

    from shared.storage.azure_blob import BlobStorageClient

    class _Blob:
        async def delete_blob(self):
            raise ResourceNotFoundError("nope")

    client = BlobStorageClient.__new__(BlobStorageClient)
    client._container = "c"
    client._client = SimpleNamespace(
        get_container_client=lambda _c: SimpleNamespace(get_blob_client=lambda _n: _Blob())
    )

    assert await client.delete_blob("t/r/document/x.pdf") is False


@pytest.mark.unit
async def test_delete_blob_reraises_anything_that_is_not_a_missing_blob():
    """A permission or network failure must reach the caller so it can keep the row."""
    from shared.storage.azure_blob import BlobStorageClient

    class _Blob:
        async def delete_blob(self):
            raise RuntimeError("AuthorizationPermissionMismatch")

    client = BlobStorageClient.__new__(BlobStorageClient)
    client._container = "c"
    client._client = SimpleNamespace(
        get_container_client=lambda _c: SimpleNamespace(get_blob_client=lambda _n: _Blob())
    )

    with pytest.raises(RuntimeError):
        await client.delete_blob("t/r/document/x.pdf")


# ── a synthesised id is not a stored artifact ────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "synthetic",
    [
        "2c1ee894-d371-4e53-8e71-efb825a83dac:story:SCRUM-16",
        "not-a-uuid",
        "",
        "../../etc/passwd",
    ],
)
async def test_a_non_uuid_id_is_a_404_not_a_500(synthetic):
    """NOT EVERY ID THE FRONTEND HOLDS ADDRESSES A ROW. story_artifacts_from_run
    synthesises story artifacts straight from a run's requirements_payload — there is no
    structured-story table — with ids shaped `{run_id}:story:{source_key}`.

    Comparing one to a uuid column makes Postgres raise on the cast, and PATCH, DELETE,
    GET and download all surfaced that as a bare 500. The Requirements page hands these
    ids to every one of those routes, so clicking delete on a pulled story produced an
    error that reads like a server fault when the real answer is "that id does not name
    a stored artifact".
    """
    from shared.routers.artifacts import _get_artifact_or_404

    with pytest.raises(HTTPException) as e:
        await _get_artifact_or_404(None, synthetic, str(TENANT))

    assert e.value.status_code == 404


@pytest.mark.unit
async def test_the_guard_runs_before_the_database_is_touched():
    """Passing db=None proves it: a query would raise AttributeError instead."""
    from shared.routers.artifacts import _get_artifact_or_404

    with pytest.raises(HTTPException):
        await _get_artifact_or_404(None, "definitely-not-a-uuid", str(TENANT))
