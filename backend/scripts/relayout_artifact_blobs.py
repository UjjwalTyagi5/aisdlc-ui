"""Move artifacts written under the old 4-segment layout to the new hierarchy.

    old: {tenant}/{run}/{type}/{filename}
    new: {tenant}/{business_unit}/{project}/{agent}/{run}/{type}/{filename}

Nothing REQUIRES this — blob_path is stored per row and downloads resolve through it, so
old rows keep working. It is done so the container has one layout rather than two.

SAFE ORDER: copy, verify the bytes match, update the row, and only then delete the old
blob. A failure at any point leaves the artifact reachable through its existing path.
"""
import asyncio
import os
import sys

sys.path.insert(0, ".")

from sqlalchemy import text

from shared.db import get_db_session_superuser
from shared.services.artifact_store import blob_path_for, is_blob_path
from shared.storage.azure_blob import BlobStorageClient

# The tenant whose artifacts to relayout. Required: the RLS GUC is per-tenant, so
# there is no "all tenants" mode here by design — run it once per organisation.
TENANT = os.environ.get("RELAYOUT_TENANT_ID", "")
APPLY = "--apply" in sys.argv

if not TENANT:
    raise SystemExit(
        "Set RELAYOUT_TENANT_ID to the organisation whose artifacts should be moved. "
        "Runs as a dry run unless --apply is passed."
    )


async def main():
    blob = BlobStorageClient()
    container = blob._client.get_container_client(blob._container)
    moved = skipped = 0
    try:
        async with get_db_session_superuser() as db:
            await db.execute(
                text("select set_config('app.current_tenant_id', :t, false)").bindparams(t=TENANT)
            )
            rows = list(await db.execute(text("""
                select a.id::text, a.blob_path, a.content_type, a.artifact_type,
                       r.id::text, r.stage, p.id::text, w.id::text
                from artifacts a
                join runs r on r.id = a.run_id
                left join projects p on p.id = r.project_id
                left join workspaces w on w.id = p.workspace_id
                where a.blob_path is not null and a.blob_url is not null
            """)))

            for art_id, old_path, ctype, atype, run_id, stage, project_id, ws_id in rows:
                if not is_blob_path(old_path, TENANT):
                    print(f"skip {art_id}: not a blob path for this tenant")
                    skipped += 1
                    continue
                if len(old_path.split("/")) != 4:
                    print(f"skip {art_id}: already {len(old_path.split('/'))} segments")
                    skipped += 1
                    continue

                filename = old_path.rsplit("/", 1)[-1]
                new_path = blob_path_for(
                    TENANT, run_id, atype, filename,
                    workspace_id=ws_id, project_id=project_id, agent=stage,
                )
                print(f"\nartifact {art_id}")
                print(f"  from: {old_path}")
                print(f"  to  : {new_path}")

                if not APPLY:
                    print("  DRY RUN")
                    continue

                data = await blob.download_bytes(old_path)
                # The content type comes from the ROW. Guessing it, or dropping it,
                # would re-download as application/octet-stream and the browser would
                # offer a save dialog for a PDF it used to render.
                new_url = await blob.upload_bytes(
                    data, new_path, content_type=ctype or "application/octet-stream"
                )
                await db.execute(text("""
                    update artifacts set blob_path = :p, blob_url = :u
                    where id = CAST(:i AS uuid)
                """).bindparams(p=new_path, u=new_url, i=art_id))

                check = await blob.download_bytes(new_path)
                if check != data:
                    print("  ABORT: copy does not match source — old blob left in place")
                    await db.rollback()
                    skipped += 1
                    continue

                await db.commit()
                await container.get_blob_client(old_path).delete_blob()
                print(f"  moved ({len(data)} bytes), old blob deleted")
                moved += 1

        print(f"\n{moved} moved, {skipped} skipped")
    finally:
        await blob.close()


asyncio.run(main())
