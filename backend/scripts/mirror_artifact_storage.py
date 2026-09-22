"""Copy every stored artifact between Azure Blob Storage and the local artifact root.

WHY THIS EXISTS. The two backends hold the same namespace — `tenant/.../document/file`, with
unapproved documents under the tenant's `_pending/` prefix — but they do not share bytes.
Switching `build_blob_client` from one to the other therefore hides every file the other one
holds: downloads 404, previews say the file is missing, and an agent asking for an approved
document is told it "could not be retrieved". That happened on both earlier switches. Run this
BEFORE switching, and again before switching back, so the destination holds everything.

    # see what would be copied (default: nothing is written)
    python scripts/mirror_artifact_storage.py --direction azure-to-local
    # do it
    python scripts/mirror_artifact_storage.py --direction azure-to-local --apply
    # and the way back, after a spell on local storage
    python scripts/mirror_artifact_storage.py --direction local-to-azure --apply

WHAT IT GUARANTEES.
  * Names are preserved exactly, `_pending/` included, so `blob_path` keeps resolving.
  * A file already at the destination with identical bytes is skipped (idempotent, so it is
    safe to re-run), and one with DIFFERENT bytes is never overwritten — it is reported as a
    conflict and left alone, because the copy cannot tell which version is wanted.
  * Every copy is read back and compared before it counts as done.
  * Nothing is deleted at the source. Both sides keep their copy.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.env import ARTIFACT_STORAGE_ROOT, AZURE_BLOB_ACCOUNT_URL, BACKEND_ROOT  # noqa: E402
from shared.storage.azure_blob import BlobStorageClient  # noqa: E402
from shared.storage.local_blob import LocalBlobStorageClient  # noqa: E402


def local_root() -> Path:
    if not ARTIFACT_STORAGE_ROOT:
        raise SystemExit("ARTIFACT_STORAGE_ROOT is not set in backend/.env — nowhere to mirror to.")
    root = Path(ARTIFACT_STORAGE_ROOT)
    return root if root.is_absolute() else BACKEND_ROOT / root


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


async def azure_names(client: BlobStorageClient) -> list[str]:
    from azure.storage.blob.aio import BlobServiceClient

    service = BlobServiceClient(client._account_url, credential=client._credential)
    async with service:
        container = service.get_container_client(client._container)
        return [blob.name async for blob in container.list_blobs()]


def local_names(root: Path) -> list[str]:
    return [str(p.relative_to(root)).replace(os.sep, "/") for p in root.rglob("*")
            if p.is_file() and not p.name.startswith((".upload-", ".probe-"))]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--direction", required=True, choices=("azure-to-local", "local-to-azure"))
    ap.add_argument("--apply", action="store_true", help="write; without it, only report")
    ap.add_argument("--prefix", default="", help="limit to names starting with this (e.g. one tenant id)")
    args = ap.parse_args()

    if not AZURE_BLOB_ACCOUNT_URL:
        raise SystemExit("AZURE_BLOB_ACCOUNT_URL is not set — there is no Azure side to mirror.")
    root = local_root()
    root.mkdir(parents=True, exist_ok=True)
    azure = BlobStorageClient()
    local = LocalBlobStorageClient(root)
    print(f"{'COPYING' if args.apply else 'DRY RUN — nothing is written'}: {args.direction}")
    print(f"local root: {root}")

    try:
        if args.direction == "azure-to-local":
            names, src, dst = await azure_names(azure), azure, local
        else:
            names, src, dst = local_names(root), local, azure
        names = sorted(n for n in names if n.startswith(args.prefix))
        print(f"source holds {len(names)} files\n")

        copied = skipped = conflicts = failed = 0
        for name in names:
            try:
                data = await src.download_bytes(name)
            except Exception as exc:  # noqa: BLE001 — report and carry on; one bad file is not the run
                print(f"  READ FAILED  {name}  ({type(exc).__name__})")
                failed += 1
                continue
            try:
                existing = await dst.download_bytes(name)
            except Exception:
                existing = None
            if existing is not None:
                if existing == data:
                    skipped += 1
                else:
                    print(f"  CONFLICT     {name}\n               source {digest(data)} vs destination "
                          f"{digest(existing)} — left alone")
                    conflicts += 1
                continue
            if not args.apply:
                print(f"  would copy   {name}")
                copied += 1
                continue
            try:
                await dst.upload_bytes(data, name)
                if await dst.download_bytes(name) != data:
                    raise RuntimeError("verification read did not match")
                copied += 1
                print(f"  copied       {name}")
            except Exception as exc:  # noqa: BLE001
                print(f"  WRITE FAILED {name}  ({type(exc).__name__}: {exc})")
                failed += 1

        print(f"\n{'copied' if args.apply else 'to copy'}: {copied}   already identical: {skipped}   "
              f"conflicts: {conflicts}   failed: {failed}")
        if conflicts:
            print("Conflicts are files that exist on both sides with different bytes. Nothing was "
                  "overwritten; decide which one is wanted and copy that one by hand.")
        return 1 if failed else 0
    finally:
        await azure.close()
        await local.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
