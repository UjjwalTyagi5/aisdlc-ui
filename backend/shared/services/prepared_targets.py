"""The prepared target outlives the process that prepared it.

THE FAILURE THIS FIXES. Preparing a target clones a repository and records where it
landed — in a module-level dict. The clone survives a restart; the record does not. So
after any restart the Deployment page says "No deployment yet" and the agent answers
"the workspace is not prepared (inspect_repo failed)", while a complete checkout of the
right branch sits on disk a directory away. Nothing is logged, because from the
process's point of view nothing went wrong: it was simply never told.

In development the restart is not rare — it is caused by the clone. `uvicorn --reload`
watches the backend tree, the workspace lives inside it, and cloning a repository that
contains `.py` files reloads the server that just cloned it. In production the same
record is invisible to every other worker, so which pod answers the chat decides
whether the agent can see the repo.

NEVER THE CREDENTIAL. `pat` and the PAT-injected `repo_url` are dropped before writing.
The whole point of the per-user-per-project token is that there is exactly one copy of
it, in the credential store; a second copy in a JSON file beside the checkout would
undo that, and would outlive the revocation of the first. The token is re-resolved at
bind time from the store, as the person the target was prepared for — `owner_id` is
recorded for exactly that, and it is an id, not a secret.

The file sits beside the clone it describes, so deleting a project's workspace deletes
its records too, and a record can never outlive the checkout it points at.
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

from config.env import DEV_WORKSPACE_ROOT

logger = logging.getLogger(__name__)

#: Dropped before writing. `repo_url` because the clone helpers inject the token into
#: it — it reads as a URL and carries a credential.
_SECRET_KEYS = ("pat", "repo_url")

_ROOT = pathlib.Path(DEV_WORKSPACE_ROOT)


def _path(agent: str, tenant_id: str, project_id: str) -> pathlib.Path:
    # `.name` on each id: they are uuids, but a path built from request data should not
    # be able to climb out of the workspace even if that ever stops being true.
    safe = [pathlib.Path(str(p or "unknown")).name for p in (tenant_id, project_id)]
    return _ROOT / safe[0] / safe[1] / f"{pathlib.Path(agent).name}.prepared.json"


def save(
    agent: str, tenant_id: str, project_id: str, record: dict, *, owner_id: str = ""
) -> None:
    """Record what was prepared, so a later process can find it again.

    Best-effort: a target that cannot be written down still works in this process, and
    failing the prepare because of it would trade a recoverable problem for an
    immediate one.
    """
    stored = {k: v for k, v in record.items() if k not in _SECRET_KEYS}
    stored["owner_id"] = str(owner_id or "")
    path = _path(agent, tenant_id, project_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written whole, then moved: a reader arriving mid-write would otherwise get a
        # truncated file and treat a prepared target as absent.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stored, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prepared target not persisted for %s: %s", agent, exc)


def load(agent: str, tenant_id: str, project_id: str) -> Optional[dict]:
    """What was prepared, without the credential — or None.

    A record whose checkout has since been deleted returns None. Binding to a work_dir
    that is no longer there would put the agent back in the state this module exists to
    prevent, except with the page insisting a target IS prepared.
    """
    path = _path(agent, tenant_id, project_id)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("prepared target unreadable for %s: %s", agent, exc)
        return None
    if not isinstance(data, dict):
        return None
    work_dir = data.get("work_dir")
    if not work_dir or not pathlib.Path(work_dir).is_dir():
        return None
    return data


def forget(agent: str, tenant_id: str, project_id: str) -> None:
    """Drop the record — for tests, and for a target deliberately torn down."""
    try:
        _path(agent, tenant_id, project_id).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


async def resolve_secret(
    *, tenant_id: str, project_id: str, owner_id: str, provider: str = ""
) -> str:
    """The token to act with, resolved fresh for the person the target belongs to.

    Returns "" rather than raising: reading a checked-out repository needs no token at
    all, so a failure here must not stop the agent inspecting the code. Only the write
    paths need it, and they say so themselves when it is missing.
    """
    if not owner_id:
        # Prepared before owner_id was recorded, or by something with no person behind
        # it. Borrowing another token here is exactly what the per-user rule forbids.
        return ""
    try:
        from shared.services import repo_source  # noqa: PLC0415

        _chosen, _base, secret = await repo_source.resolve(
            tenant_id, project_id=project_id, owner_id=owner_id,
            provider=provider or None,
        )
        return secret or ""
    except Exception as exc:  # noqa: BLE001
        logger.info("could not re-resolve the source credential: %s", exc)
        return ""
