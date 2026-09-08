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
#:
#: `status` IS NOT CARRIED. `DevelopmentArtifacts.status` defaults to `"not_started"`
#: and the graph does not maintain it, so copying it stored `status: "not_started"`
#: beside a real pull request URL — a statement in the database that contradicts the
#: row it sits in. Nothing reads it (`pointers_for_run` uses `repo_url` and `pr_url`),
#: so the honest move is to omit it rather than persist a value that is wrong.
_FIELDS = ("repo_url", "branch_name", "pr_url", "pr_title")


def _dev_session(run_id: str) -> Any:
    """The Development agent's in-memory session for this run.

    Imported INSIDE the function, not at module scope. `orchestrator2` is loaded by
    the socket on every connection, and importing the development agent's session
    store at import time would pull that whole package — and its tool imports — into
    every turn of every other agent.

    The session id IS the run id: `dispatch.run_agent` sets that contextvar before
    running the graph, which is what makes the clone land under `<run_id>/project`
    in the first place.

    THE MODULE PATH IS `config.session_state`, NOT `session`. An earlier version
    imported the latter, which does not exist; the ImportError was caught by
    `persist`'s "no session for this run is the normal case" guard, so
    `runs.development_artifacts` was never written on any run — and the code tree the
    user saw was only the synthetic one the panel draws while Development is the
    ACTIVE agent, which vanished the moment another agent started. Every test
    monkeypatched this function, so none of them imported what it imports.

    `get_session` CREATES on a miss rather than raising, so a turn for any other agent
    gets an empty state and `_collect` reads nothing from it.
    """
    from agents_orchestrator.development_agent.config.session_state import get_session

    return get_session(run_id)


#: What makes a session worth recording at all. `pointers_for_run` builds `dev-code`
#: from `repo_url` and `dev-pr` from `pr_url`, and nothing else there produces a
#: pointer — so a mapping with neither cannot put anything on the panel.
#:
#: THIS GUARD IS LOAD-BEARING. `DevelopmentArtifacts.status` defaults to
#: `"not_started"`, which is truthy, so collecting "any non-empty field" returned
#: `{"status": "not_started"}` for EVERY agent's turn — a truthy dict, which
#: `pointers_for_run` renders as a code tree over a clone that does not exist. An empty
#: tree reads as a pull that failed, which is worse than no tree at all.
_EVIDENCE_OF_WORK = ("repo_url", "pr_url")


def _collect(session: Any) -> dict:
    """The artifact fields this session knows about, or `{}` if it did no work."""
    artifacts = getattr(session, "dev_artifacts", None)
    out: dict = {}
    for name in _FIELDS:
        value = getattr(artifacts, name, None) or getattr(session, name, None)
        if value:
            out[name] = value
    if not any(out.get(name) for name in _EVIDENCE_OF_WORK):
        return {}
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
