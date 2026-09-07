"""Record the Development agent's clone and pull request on the run row.

WHY THIS MODULE EXISTS. Found in the live UI: the Development agent cloned a repo
successfully — `.git` and the working tree were on disk under
`files/<user>/orchestrator/<run_id>/project/` — and the run row's
`development_artifacts` column stayed `None`. A grep for that column name across
`orchestrator2/` returned nothing at all.

The standalone wrapper this engine replaced did write it
(`development_agent_api._persist_pr_to_run`). So this is the fourth instance of one
recurring shape on this branch: state the standalone `*_agent_api.py` wrappers
provided, which `orchestrator2` skips because it loads the GRAPH and not the API
around it. The first three were the project connector, the MCP tool bindings, and the
per-run contextvars.

WHAT IT COSTS WHEN MISSING. `deliverables.pointers_for_run` synthesises the
"Repository code" (`dev-code`) and "Pull request" (`dev-pr`) rows from exactly this
column. With it `None`, neither can ever appear for an orchestrator2 run, however
well the clone went.

MERGED, NOT REPLACED. A conversation clones on one turn and raises a PR several turns
later, and the agent's session is not guaranteed to still carry the earlier fields.
Overwriting the column each turn would drop `repo_url` the moment a turn knew only
about the PR — and the code tree would vanish from the panel mid-conversation.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

import shared.db as shared_db
from shared.models.orm import Run

logger = logging.getLogger(__name__)

#: The fields worth carrying onto the run. Deliberately a short list rather than the
#: session's whole `model_dump()`: this column is read by the panel, and everything
#: put here is something a client eventually sees.
_FIELDS = ("repo_url", "branch_name", "pr_url", "pr_title", "status")


def _dev_session(run_id: str) -> Any:
    """The Development agent's in-memory session for this run.

    Imported INSIDE the function, not at module scope. `orchestrator2` is loaded by
    the socket on every connection, and importing the development agent's session
    store at import time would pull that whole package — and its tool imports — into
    every turn of every other agent.

    The session id IS the run id: `dispatch.run_agent` sets that contextvar before
    running the graph, which is what makes the clone land under `<run_id>/project`
    in the first place.
    """
    from agents_orchestrator.development_agent.session import get_session

    return get_session(run_id)


def _collect(session: Any) -> dict:
    """The non-empty artifact fields this session knows about."""
    artifacts = getattr(session, "dev_artifacts", None)
    out: dict = {}
    for name in _FIELDS:
        value = getattr(artifacts, name, None) or getattr(session, name, None)
        if value:
            out[name] = value
    return out


async def persist(run_id: str, *, tenant_id: str) -> dict | None:
    """Merge what the Development agent produced into `runs.development_artifacts`.

    Returns the merged mapping, or `None` when there was nothing to record.

    NEVER RAISES. Every agent's turn calls this and only Development has a session to
    read, so a missing one is the ordinary case, not an error — and the eight other
    agents must not lose a completed turn to it. Failures are logged, not surfaced:
    unlike a lost deliverable, which is the user's document, a missing pointer costs
    a link to something still reachable from the chat.

    NOTHING IS WRITTEN when the agent pulled nothing. An empty mapping is not the same
    as absent: `pointers_for_run` treats any truthy dict as reason to render a code
    tree, and an empty tree reads as a pull that FAILED rather than as a turn that
    never pulled.
    """
    try:
        found = _collect(_dev_session(run_id))
    except Exception:  # noqa: BLE001 — no session for this run is the normal case
        return None
    if not found:
        return None

    try:
        async with shared_db.get_db_session_for_tenant(tenant_id) as db:
            run = (
                await db.execute(select(Run).where(Run.id == run_id))
            ).scalars().first()
            if run is None:
                return None
            # Merged over whatever is already there — see the module docstring.
            merged = dict(run.development_artifacts or {})
            merged.update(found)
            if merged == (run.development_artifacts or {}):
                return merged
            run.development_artifacts = merged
            await db.commit()
            return merged
    except Exception:  # noqa: BLE001 — a lost pointer must not fail the turn
        logger.exception(
            "orchestrator2 could not record development artifacts (run=%s)", run_id,
        )
        return None
