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
OTHER_RUN = "44444444-4444-4444-4444-444444444444"
WORKSPACE = "55555555-5555-5555-5555-555555555555"
PROJECT = "66666666-6666-6666-6666-666666666666"


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
def test_the_path_mirrors_the_product_hierarchy_in_that_order():
    """tenant / business unit / project / agent / run / type / filename."""
    path = blob_path_for(
        TENANT, RUN, "requirements", "brd.docx",
        workspace_id=WORKSPACE, project_id=PROJECT, agent="requirements",
    )
    assert path == f"{TENANT}/{WORKSPACE}/{PROJECT}/requirements/{RUN}/requirements/brd.docx"


@pytest.mark.unit
def test_the_tenant_is_always_the_first_segment():
    """Blob storage has no RLS. This prefix is the whole isolation boundary, and
    `is_blob_path` tests exactly it — so no reordering may bury it."""
    path = blob_path_for(
        TENANT, RUN, "requirements", "brd.docx",
        workspace_id=WORKSPACE, project_id=PROJECT, agent="design",
    )
    assert path.split("/")[0] == TENANT
    assert is_blob_path(path, TENANT)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "_no-business-unit/_no-project/_no-agent"),
        ({"agent": "design"}, "_no-business-unit/_no-project/design"),
        ({"project_id": PROJECT, "agent": "design"}, f"_no-business-unit/{PROJECT}/design"),
    ],
)
def test_a_missing_segment_becomes_a_placeholder_not_a_gap(kwargs, expected):
    """Run.project_id is nullable, and without a project there is no business unit.
    Collapsing the segment would shift every level below it, so "the fourth segment is
    the agent" would stop being true and the layout would be unreadable exactly where
    it is least obvious."""
    path = blob_path_for(TENANT, RUN, "requirements", "brd.docx", **kwargs)
    assert path == f"{TENANT}/{expected}/{RUN}/requirements/brd.docx"
    assert len(path.split("/")) == 7


@pytest.mark.unit
def test_the_depth_is_constant_whatever_is_known():
    everything = blob_path_for(
        TENANT, RUN, "requirements", "brd.docx",
        workspace_id=WORKSPACE, project_id=PROJECT, agent="design",
    )
    nothing = blob_path_for(TENANT, RUN, "requirements", "brd.docx")
    assert len(everything.split("/")) == len(nothing.split("/")) == 7


@pytest.mark.unit
def test_one_agents_output_is_separable_from_anothers_in_the_same_project():
    """The point of the agent segment: everything Design produced for this project sits
    under one prefix, without resolving every run first."""
    design = blob_path_for(
        TENANT, RUN, "document", "hld.docx",
        workspace_id=WORKSPACE, project_id=PROJECT, agent="design",
    )
    reqs = blob_path_for(
        TENANT, RUN, "document", "brd.docx",
        workspace_id=WORKSPACE, project_id=PROJECT, agent="requirements",
    )
    prefix = f"{TENANT}/{WORKSPACE}/{PROJECT}"
    assert design.startswith(f"{prefix}/design/")
    assert reqs.startswith(f"{prefix}/requirements/")


@pytest.mark.unit
def test_two_runs_of_one_agent_do_not_overwrite_each_other():
    """upload_bytes overwrites by default, and two runs of the same agent routinely
    produce the same filename — so the run segment has to stay below the agent."""
    common = dict(workspace_id=WORKSPACE, project_id=PROJECT, agent="design")
    first = blob_path_for(TENANT, RUN, "document", "hld.docx", **common)
    second = blob_path_for(TENANT, OTHER_RUN, "document", "hld.docx", **common)
    assert first != second


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
    path = blob_path_for(
        TENANT, RUN, "../../evil", "brd.docx",
        workspace_id=WORKSPACE, project_id=PROJECT, agent="../../escape",
    )
    assert ".." not in path
    assert path.startswith(f"{TENANT}/{WORKSPACE}/{PROJECT}/")
    assert len(path.split("/")) == 7


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
    # No project_id passed, so the middle segments are placeholders.
    assert name == f"{TENANT}/_no-business-unit/_no-project/_no-agent/{RUN}/requirements/brd.docx"
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
    assert name.startswith(f"{TENANT}/")
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
    assert art.blob_path == (
        f"{TENANT}/_no-business-unit/_no-project/_no-agent/{RUN}/requirements/brd.docx"
    )
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


# ── the business unit is derived, not passed ─────────────────────────────────


class _ScopeSession(_FakeSession):
    """A session whose execute() answers the workspace lookup."""

    def __init__(self, workspace=WORKSPACE, raises=False):
        super().__init__()
        self._workspace = workspace
        self._raises = raises
        self.queries = 0

    async def execute(self, *_a, **_kw):
        self.queries += 1
        if self._raises:
            raise RuntimeError("database is unreachable")
        ws = self._workspace

        class _R:
            @staticmethod
            def scalar_one_or_none():
                return ws

        return _R()


@pytest.mark.unit
async def test_the_business_unit_is_looked_up_from_the_project():
    """The caller passes the project it already knows; passing the workspace TOO would
    let the two disagree and file the artifact under a unit that does not own it."""
    blob, db = _FakeBlob(), _ScopeSession()
    await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="document",
        filename="hld.docx", data=b"x", project_id=PROJECT, agent="design",
        blob_client=blob,
    )
    assert blob.uploads[0][0] == (
        f"{TENANT}/{WORKSPACE}/{PROJECT}/design/{RUN}/document/hld.docx"
    )
    assert db.queries == 1


@pytest.mark.unit
async def test_no_lookup_happens_without_a_project():
    """A run need not belong to one, and a query that cannot succeed should not run."""
    blob, db = _FakeBlob(), _ScopeSession()
    await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="document",
        filename="hld.docx", data=b"x", agent="design", blob_client=blob,
    )
    assert db.queries == 0
    assert "_no-business-unit" in blob.uploads[0][0]


@pytest.mark.unit
async def test_an_unresolvable_project_still_stores_the_artifact():
    """A path SEGMENT is not worth failing a generated document over."""
    blob, db = _FakeBlob(), _ScopeSession(raises=True)
    art = await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="document",
        filename="hld.docx", data=b"x", project_id=PROJECT, agent="design",
        blob_client=blob,
    )
    assert art.blob_url is not None
    assert blob.uploads[0][0].startswith(f"{TENANT}/_no-business-unit/{PROJECT}/design/")


@pytest.mark.unit
async def test_a_project_in_another_tenant_resolves_to_no_workspace():
    """The lookup runs under the caller's session, so RLS hides it — and a hidden
    project must not silently borrow the placeholder of a real one."""
    blob, db = _FakeBlob(), _ScopeSession(workspace=None)
    await store_artifact(
        db, tenant_id=TENANT, run_id=RUN, artifact_type="document",
        filename="hld.docx", data=b"x", project_id=PROJECT, agent="design",
        blob_client=blob,
    )
    assert blob.uploads[0][0].startswith(f"{TENANT}/_no-business-unit/{PROJECT}/design/")


@pytest.mark.unit
async def test_the_old_four_segment_paths_still_validate_as_this_tenants_blobs():
    """Rows written before this layout keep their stored path, and downloads resolve
    through it — so is_blob_path must keep accepting them. It tests the tenant prefix,
    which did not move."""
    legacy = f"{TENANT}/{RUN}/document/brd.docx"
    assert is_blob_path(legacy, TENANT)
    assert not is_blob_path(legacy, OTHER_TENANT)
