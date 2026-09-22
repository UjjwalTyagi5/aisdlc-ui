"""Prove the whole document lifecycle works against whichever storage backend is configured.

Run it after switching backends (and after `mirror_artifact_storage.py`), because the failure
a switch causes is silent until somebody opens a document: the rows are all still there, and
only the bytes are missing. This exercises the real code paths against the real database —
the Documents list, preview, download of an approved document and of one still pending, the
two tools an agent reads documents with, a file-and-approve round trip (the step that moves
bytes out of the tenant's `_pending/` prefix), and the isolation rules.

    cd backend
    .venv\\Scripts\\python.exe scripts\\verify_artifact_storage.py [--project <uuid>]

It creates ONE document and removes it (row and files) at the end. Everything else is read-only.
Exit code 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402

from shared.db import get_db_session_for_tenant, get_db_session_superuser  # noqa: E402
from shared.models.orm import Artifact, Project  # noqa: E402

_results: list[tuple[bool, str, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    _results.append((passed, name, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}{('  — ' + detail) if detail else ''}")


def _request(tenant, user, perms=(), client=None):
    return SimpleNamespace(
        state=SimpleNamespace(tenant_id=str(tenant), user_id=str(user), permissions=list(perms)),
        app=SimpleNamespace(state=SimpleNamespace(blob_client=client)),
    )


async def _pick(project_id: str | None):
    """An approved document to read, a pending one to download, and the project's admin."""
    from shared.authz.read_scope import active_binding

    async with get_db_session_superuser() as db:
        q = select(Artifact).where(Artifact.approval_status == "approved", Artifact.blob_path.isnot(None))
        if project_id:
            q = q.where(Artifact.project_id == uuid.UUID(project_id))
        approved = (await db.execute(q.order_by(Artifact.created_at.desc()))).scalars().first()
        if approved is None:
            raise SystemExit("No approved document with a stored file — nothing to verify against.")
        project = (await db.execute(select(Project).where(Project.id == approved.project_id))).scalar_one()
        unapproved = (await db.execute(select(Artifact).where(
            Artifact.project_id == project.id, Artifact.approval_status.in_(("draft", "pending")),
            Artifact.blob_path.isnot(None)).order_by(Artifact.created_at.desc()))).scalars().first()
        admin = (await db.execute(text(
            f"SELECT rb.user_id FROM role_bindings rb WHERE {active_binding()} AND rb.role_name='project_admin' "
            "AND rb.scope_kind='project' AND rb.scope_id = CAST(:p AS uuid) LIMIT 1"),
            {"p": str(project.id), "now": datetime.now(tz=timezone.utc)})).scalar()
    return approved, unapproved, project, (str(admin) if admin else None)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=None, help="verify against this project id (default: any)")
    args = ap.parse_args()

    import json

    from config.ws_helper import set_project_id, set_tenant_id, set_user_id
    from shared.routers import artifacts as ar
    from shared.services.artifact_store import get_blob_client, is_blob_path, pending_blob_path, store_artifact
    from shared.storage import build_blob_client
    from shared.tools.project_documents import make_document_tools

    client = build_blob_client()
    if client is None:
        raise SystemExit("No storage backend is configured (see docs/local-setup.md).")
    print(f"backend in use: {type(client).__name__}  {getattr(client, 'root', '')}\n")
    check("the health probe can write to the backend", (await client.probe()) == "ok")

    approved, unapproved, project, admin = await _pick(args.project)
    tenant, pid = str(approved.tenant_id), str(project.id)
    actor = admin or "verify-script"
    req = _request(tenant, actor, [] if admin else ["admin:*"], client)
    print(f"project: {project.display_name}  ({pid})\n")

    print("Documents, preview and download")
    async with get_db_session_for_tenant(tenant) as db:
        listed = await ar.list_artifacts_for_project(pid, req, db=db)
    check("the Documents list loads", len(listed) > 0, f"{len(listed)} documents")
    check("an approved document offers a download link",
          any(getattr(i, "downloadUrl", None) and i.status == "approved" for i in listed))

    async with get_db_session_for_tenant(tenant) as db:
        page = await ar.artifact_preview(str(approved.id), req, db=db)
    check("an approved document opens in the page's viewer", page.get("kind") in ("markdown", "sheets", "html", "file"),
          f"{approved.blob_path.rsplit('/', 1)[-1]} -> {page.get('kind')}")

    async with get_db_session_for_tenant(tenant) as db:
        got = await ar.download_artifact(str(approved.id), req, db=db)
    check("an approved document downloads", len(got.body) > 0, f"{len(got.body)} bytes")

    if unapproved is not None:
        async with get_db_session_for_tenant(tenant) as db:
            pending_bytes = await ar.download_artifact(str(unapproved.id), req, db=db)
        check("a document awaiting approval downloads from the _pending area", len(pending_bytes.body) > 0,
              f"{unapproved.blob_path.rsplit('/', 1)[-1]} ({len(pending_bytes.body)} bytes)")

    print("\nWhat the agents themselves read")
    set_tenant_id(tenant)
    set_project_id(pid)
    set_user_id(actor)
    listing_tool, read_tool = make_document_tools("design")
    listing = await listing_tool.ainvoke({})
    try:
        docs = json.loads(listing)
    except json.JSONDecodeError:
        docs = []
    check("list_project_documents shows the approved documents", bool(docs),
          f"{len(docs)} documents" if docs else listing[:80])
    if docs:
        body = await read_tool.ainvoke({"document_id": str(docs[0].get("id") or docs[0].get("document_id"))})
        check("read_document returns a document's text", len(body) > 100 and "could not" not in body[:200].lower(),
              f"{len(body)} chars")

    print("\nFiling and approving (the step that moves the bytes)")
    made_id = blob_path = None
    try:
        data = b"storage-verification-" + uuid.uuid4().hex.encode()
        async with get_db_session_for_tenant(tenant) as db:
            made = await store_artifact(
                db, tenant_id=tenant, project_id=pid, run_id=None, agent="design",
                filename=f"storage-check-{uuid.uuid4().hex[:8]}.txt", data=data, content_type="text/plain",
                artifact_type="document", stage="design", approval_status="draft", uploaded_by=actor,
                blob_client=get_blob_client(),
            )
            made_id, blob_path = str(made.id), made.blob_path
        pending = pending_blob_path(blob_path)
        check("a new document lands under the tenant's _pending prefix",
              (await get_blob_client().download_bytes(pending)) == data)

        admin_req = _request(tenant, actor, ["admin:*"], client)
        async with get_db_session_for_tenant(tenant) as db:
            await ar.submit_artifact(made_id, req, db=db)
        async with get_db_session_for_tenant(tenant) as db:
            decided = await ar.approve_artifact(made_id, admin_req, db=db)
        check("approval records the document as approved", getattr(decided, "status", None) == "approved")
        check("approval moved the bytes to the final path",
              (await get_blob_client().download_bytes(blob_path)) == data)
        moved = False
        try:
            await get_blob_client().download_bytes(pending)
        except Exception:
            moved = True
        check("the pending copy is gone", moved)
        async with get_db_session_for_tenant(tenant) as db:
            after = await ar.download_artifact(made_id, admin_req, db=db)
        check("the approved document downloads", after.body == data)
    finally:
        if made_id:
            for name in (blob_path, pending_blob_path(blob_path)):
                try:
                    await get_blob_client().delete_blob(name)
                except Exception:
                    pass
            async with get_db_session_for_tenant(tenant) as db:
                await db.execute(delete(Artifact).where(Artifact.id == uuid.UUID(made_id)))
            print("  (the verification document and its files were removed)")

    print("\nSegregation")
    stranger_tenant = uuid.uuid4()
    for name, label in (
        (f"{stranger_tenant}/../../outside.bin", "a name that walks out of the storage root"),
        (f"{tenant}/../{stranger_tenant}/design/run/document/x.docx", "a name that walks into another tenant's tree"),
    ):
        refused = False
        try:
            await client.download_bytes(name)
        except ValueError:
            refused = True
        except Exception:
            refused = False
        check(f"{label} is refused", refused)
    check("another tenant's prefix is not read as this tenant's",
          not is_blob_path(f"{stranger_tenant}/unit/project/design/run/document/x.docx", tenant))
    hidden = False
    try:
        async with get_db_session_for_tenant(tenant) as db:
            await ar.artifact_preview(str(approved.id), _request(tenant, uuid.uuid4(), [], client), db=db)
    except HTTPException as exc:
        hidden = exc.status_code == 404
    check("someone who cannot see the project cannot read its documents", hidden)

    failed = [r for r in _results if not r[0]]
    print("\n" + "=" * 70)
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    for _, name, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
