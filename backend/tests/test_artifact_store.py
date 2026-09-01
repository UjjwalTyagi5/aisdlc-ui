"""The blob path is the tenant boundary — these tests are about that, not about I/O.

`shared/storage/azure_blob.py:upload_bytes` documents the contract this module exists to
uphold: the name must be `{tenant_id}/{run_id}/{artifact_type}/{filename}` and you must
"never accept blob_name directly from user input". Nothing else separates one tenant's
documents from another's in blob storage — there is no per-tenant container.

What this replaces is a flat `outputs/` directory with fixed names (`outputs/brd.docx`),
where one tenant's BRD overwrote another's and either could be downloaded by anyone
holding `artifact:view`.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services.artifact_store import (  # noqa: E402
    MAX_LEAF_LENGTH,
    blob_path_for,
    is_blob_path,
    safe_leaf_name,
    store_artifact,
)

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
RUN = "33333333-3333-3333-3333-333333333333"


# ── the sanitiser ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "hostile",
    [
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "/absolute/path.docx",
        "C:\\Users\\admin\\secret.docx",
        "..",
        "...",
        "./../brd.docx",
        "sub/dir/brd.docx",
    ],
)
def test_a_filename_can_never_add_a_path_segment(hostile):
    """The leaf must stay ONE segment however it is written.

    A filename that contributes a `/` escapes the run prefix; one that contributes `..`
    escapes it even after the separators are gone. Both would land the document under a
    different tenant's prefix, which is the only thing keeping them apart.
    """
    leaf = safe_leaf_name(hostile)
    assert "/" not in leaf
    assert "\\" not in leaf
    assert ".." not in leaf
    assert leaf not in ("", ".", "..")


@pytest.mark.unit
def test_a_name_of_pure_punctuation_still_yields_a_usable_leaf():
    """Sanitising to "" would produce a path ending in `/` — the DIRECTORY, not a file.

    Azure accepts that and it is not what anybody meant.
    """
    for junk in ("...", "///", "___", "   ", "", ".."):
        assert safe_leaf_name(junk) == "document"


@pytest.mark.unit
def test_a_long_name_is_truncated_but_keeps_its_extension():
    """A .docx truncated to .doc_trunc opens in the wrong application."""
    leaf = safe_leaf_name("A" * 400 + ".docx")
    assert len(leaf) <= MAX_LEAF_LENGTH
    assert leaf.endswith(".docx")


@pytest.mark.unit
def test_ordinary_names_survive_recognisably():
    # Sanitising must not be so aggressive that a real document becomes unidentifiable.
    assert safe_leaf_name("brd.docx") == "brd.docx"
    assert safe_leaf_name("Risk Register v2.docx") == "Risk_Register_v2.docx"


# ── the path ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_the_path_is_tenant_run_type_filename_in_that_order():
    path = blob_path_for(TENANT, RUN, "requirements", "brd.docx")
    assert path == f"{TENANT}/{RUN}/requirements/brd.docx"


@pytest.mark.unit
def test_two_tenants_generating_the_same_document_do_not_collide():
    """The bug being fixed: `outputs/brd.docx` was ONE file for the whole process."""
    a = blob_path_for(TENANT, RUN, "requirements", "brd.docx")
    b = blob_path_for(OTHER_TENANT, RUN, "requirements", "brd.docx")
    assert a != b
    assert a.startswith(f"{TENANT}/")
    assert b.startswith(f"{OTHER_TENANT}/")


@pytest.mark.unit
def test_every_segment_is_sanitised_not_only_the_filename():
    """`artifact_type` is agent-supplied today and one refactor from being caller-supplied.

    A `..` there escapes the run prefix exactly as it would in the leaf.
    """
    path = blob_path_for(TENANT, RUN, "../../evil", "brd.docx")
    assert ".." not in path
    assert path.startswith(f"{TENANT}/{RUN}/")


# ── store_artifact ───────────────────────────────────────────────────────────


class _FakeBlob:
    def __init__(self, fail: bool = False):
        self.uploads: list[tuple[str, bytes, str]] = []
        self._fail = fail

    async def upload_bytes(self, data, blob_name, content_type="application/octet-stream"):
        if self._fail:
            raise RuntimeError("storage account is unreachable")
        self.uploads.append((blob_name, data, content_type))
        return f"https://acct.blob.core.windows.net/artifacts/{blob_name}"


class _FakeSession:
    """Just enough AsyncSession for add()/flush()."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


@pytest.mark.unit
async def test_it_uploads_under_the_composed_path_and_records_the_row():
    blob, db = _FakeBlob(), _FakeSession()
    art = await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="requirements",
        filename="brd.docx", data=b"hello",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        blob_client=blob,
    )
    name, data, ctype = blob.uploads[0]
    assert name == f"{TENANT}/{RUN}/requirements/brd.docx"
    assert data == b"hello"
    assert art.blob_path == name
    assert art.blob_url is not None
    assert art.size_bytes == 5
    assert art.tenant_id == TENANT
    assert db.added == [art]


@pytest.mark.unit
async def test_a_hostile_filename_is_still_confined_to_its_tenant_prefix():
    """The end-to-end version of the sanitiser tests: what actually reaches Azure."""
    blob, db = _FakeBlob(), _FakeSession()
    await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="requirements",
        filename=f"../../{OTHER_TENANT}/steal.docx", data=b"x", blob_client=blob,
    )
    name = blob.uploads[0][0]
    assert name.startswith(f"{TENANT}/{RUN}/")
    assert OTHER_TENANT not in name.split("/")[0]
    assert ".." not in name


@pytest.mark.unit
async def test_no_blob_configured_records_the_row_and_does_not_raise():
    """AZURE_BLOB_ACCOUNT_URL is unset in most dev environments, and process_api sets
    app.state.blob_client = None there. A document that could not be uploaded is not a
    failed run — but the row must still exist so the gap is visible."""
    db = _FakeSession()
    art = await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="requirements",
        filename="brd.docx", data=b"hello", blob_client=None,
    )
    assert art.blob_url is None
    assert art.blob_path == f"{TENANT}/{RUN}/requirements/brd.docx"
    assert db.added == [art]


@pytest.mark.unit
async def test_an_upload_failure_degrades_rather_than_failing_the_run():
    db = _FakeSession()
    art = await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="requirements",
        filename="brd.docx", data=b"hello", blob_client=_FakeBlob(fail=True),
    )
    assert art.blob_url is None          # the failure is legible…
    assert art.blob_path is not None     # …and the intended location is recorded


@pytest.mark.unit
@pytest.mark.parametrize("tenant,run", [("", RUN), (TENANT, ""), ("", "")])
async def test_a_missing_tenant_or_run_is_refused_outright(tenant, run):
    """An empty segment collapses the path — `//run/type/file` puts two tenants'
    documents under one prefix. Refusing beats writing there."""
    with pytest.raises(ValueError):
        await store_artifact(
            _FakeSession(), tenant_id=tenant, run_id=run,
            artifact_type="requirements", filename="brd.docx", data=b"x",
            blob_client=_FakeBlob(),
        )


# ── telling a blob path from a legacy local path ─────────────────────────────
#
# Before this change, register_generated_file recorded `blob_path` = an on-disk path
# and `blob_url` = a `/generated/...` static-mount URL. Those rows still exist. The
# download route must not hand either to Azure, which would answer a 502 that reads
# like a storage outage rather than "this artifact predates blob storage".


@pytest.mark.unit
def test_a_path_written_by_store_artifact_is_recognised():
    assert is_blob_path(blob_path_for(TENANT, RUN, "requirements", "brd.docx"), TENANT)


@pytest.mark.unit
@pytest.mark.parametrize(
    "legacy",
    [
        r"C:\pwc_work\frontend\backend\files\usr\requirements_agent\s1\output\brd.docx",
        "/var/app/files/usr/requirements_agent/s1/output/brd.docx",
        "files/usr/requirements_agent/s1/output/brd.docx",
        "outputs/brd.docx",
        "https://host/generated/usr/requirements_agent/s1/output/brd.docx",
    ],
)
def test_legacy_local_paths_are_not_mistaken_for_blob_paths(legacy):
    assert is_blob_path(legacy, TENANT) is False


@pytest.mark.unit
def test_another_tenants_blob_path_is_not_mine():
    """The prefix test is per-tenant, so a genuine blob path belonging to somebody else
    is rejected too — the route resolves the row by tenant first, but this keeps the
    check honest rather than merely "looks blob-shaped"."""
    theirs = blob_path_for(OTHER_TENANT, RUN, "requirements", "brd.docx")
    assert is_blob_path(theirs, TENANT) is False
    assert is_blob_path(theirs, OTHER_TENANT) is True


@pytest.mark.unit
def test_missing_values_are_not_blob_paths():
    assert is_blob_path(None, TENANT) is False
    assert is_blob_path("", TENANT) is False
    assert is_blob_path(blob_path_for(TENANT, RUN, "t", "f.docx"), "") is False
