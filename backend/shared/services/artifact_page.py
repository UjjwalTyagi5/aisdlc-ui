"""A document's PAGE COPY — the markdown the app renders — kept with the document.

THE PAGE COULD NEVER READ IT. An agent writes a document's markdown beside its Word file
under the backend's public `/generated/` mount, and the app fetched it from there by the
URL in the chat message. Two things were wrong with that. The app's Content Security
Policy (`connect-src 'self'`) refuses a fetch to the backend's origin, so every such read
failed in the browser with "Failed to fetch" — the Word download worked, because a
download is a navigation, which `connect-src` does not govern. And the URL lived only in
the chat: after a reload the Documents panel listed the document with no way to open it.

So the page copy is stored WITH the document, in the artifact store, at the document's
own blob path plus `.page.md` — under the pending prefix while the document waits, moved
on approval, deleted on rejection — and served same-origin by `GET /artifacts/{id}/page`.
Whatever happens to the document happens to its page copy.

Every helper here is best-effort: the page copy is a convenience, the document is the
record, and a storage hiccup on the copy must never fail a generation or an approval.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

PAGE_SUFFIX = ".page.md"


def page_blob_path(blob_path: str) -> str:
    """`…/document/QuickLink_BRD.docx` -> `…/document/QuickLink_BRD.docx.page.md`.

    The document's full name stays in the path — a hand-uploaded `QuickLink_BRD.md` is a
    different document and must not share the copy's name."""
    return f"{blob_path}{PAGE_SUFFIX}"


def page_location(blob_path: str, approval_status: Optional[str]) -> str:
    """Where the copy is right now: beside the pending bytes until approval, then beside
    the approved ones."""
    from shared.services.artifact_store import pending_blob_path  # noqa: PLC0415

    final = page_blob_path(blob_path)
    return final if (approval_status or "draft") == "approved" else pending_blob_path(final)


def sibling_markdown_path(file_path: str) -> Optional[str]:
    """The `.md` an agent writes beside a generated `.docx`, if it is there."""
    if not file_path:
        return None
    candidate = os.path.splitext(file_path)[0] + ".md"
    return candidate if candidate != file_path and os.path.isfile(candidate) else None


async def store_page_copy(client: Any, blob_path: str, markdown: str) -> bool:
    """Put the page copy beside a document's pending bytes. False when it could not be."""
    from shared.services.artifact_store import pending_blob_path  # noqa: PLC0415

    if client is None or not blob_path or not markdown:
        return False
    try:
        await client.upload_bytes(
            markdown.encode("utf-8"), pending_blob_path(page_blob_path(blob_path)),
            content_type="text/markdown; charset=utf-8",
        )
        return True
    except Exception as exc:  # noqa: BLE001 — a missing page view is not a lost document
        logger.warning("page copy not stored for %s: %s", blob_path, type(exc).__name__)
        return False


async def read_page_copy(client: Any, blob_path: str, approval_status: Optional[str]) -> Optional[str]:
    """The page copy's markdown, or None when there is none."""
    if client is None or not blob_path:
        return None
    try:
        data = await client.download_bytes(page_location(blob_path, approval_status))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("page copy unreadable for %s: %s", blob_path, type(exc).__name__)
        return None
    return data.decode("utf-8", errors="replace") if data else None


async def promote_page_copy(client: Any, blob_path: str) -> None:
    """On approval: the copy follows the bytes out of the pending area."""
    from shared.services.artifact_store import pending_blob_path  # noqa: PLC0415

    if client is None or not blob_path:
        return
    page = page_blob_path(blob_path)
    try:
        await client.move_blob(pending_blob_path(page), page, content_type="text/markdown; charset=utf-8")
    except FileNotFoundError:
        pass  # a document from before page copies existed
    except Exception as exc:  # noqa: BLE001
        logger.warning("page copy not promoted for %s: %s", blob_path, type(exc).__name__)


async def discard_page_copy(client: Any, blob_path: str, approval_status: Optional[str]) -> None:
    """On rejection or deletion: the copy goes with the bytes."""
    if client is None or not blob_path:
        return
    try:
        await client.delete_blob(page_location(blob_path, approval_status))
    except Exception as exc:  # noqa: BLE001
        logger.warning("page copy not deleted for %s: %s", blob_path, type(exc).__name__)
