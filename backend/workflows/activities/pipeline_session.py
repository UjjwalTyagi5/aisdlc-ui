"""Pipeline Session Bridge — makes the standalone, session-coupled agents work inside
stage runs, rebuilt idempotently every attempt (retry-safe).

The standalone agents assume a WebSocket session: a `session_id` contextvar, an
agent-session row their tools read, and (for repo agents) a cloned `work_dir`. A
A bare stage call sets up none of that. `pipeline_session` is the ONE context manager
every session-coupled activity (Tasks 5-12) wraps around its `graph.ainvoke(...)`:

  1. binds `session_id = run_id` (contextvar; ALWAYS reset in finally),
  2. mirrors the run's canonical upstream artifacts (the `runs` row) into the run-keyed
     `agent_sessions` row the agents' tools read (best-effort — swallows DB errors),
  3. optionally clones the repo into a run-scoped workspace,
  4. exposes `ps.work_dir` / `ps._upstream` / `ps._workspace` and `ps.captured_artifact()`.

Hard rule (retry-safety): everything is rebuilt from durable sources every call and every
rebuild step is idempotent — nothing relies on in-memory carryover from a prior attempt.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy import select

from config.ws_helper import set_session_id, reset_session_id
from shared.db import get_db_session_superuser
from shared.models.orm import Run
from shared.services.agent_session_store import upsert_agent_session
from shared.services.run_workspace import RunWorkspace, prepare_run_workspace

logger = logging.getLogger(__name__)

_MIRROR_FIELDS = (
    "requirements_payload", "design_artifacts", "development_artifacts",
    "testing_artifacts", "code_review_artifacts", "security_artifacts",
    "deployment_artifacts", "documentation_artifacts",
)


def _as_run_id(run_id: str):
    """Cast to UUID for the typed `runs.id` column; pass through on bad input
    so a malformed id surfaces as 'no upstream' rather than a crash."""
    try:
        return uuid.UUID(str(run_id))
    except (ValueError, TypeError, AttributeError):
        return run_id


async def _read_run_upstream(run_id: str) -> dict:
    """Read the canonical upstream artifacts off the `runs` row (durable source).

    Superuser session: this is a system pipeline operation keyed by run_id, not a
    tenant request path. Returns {} on miss or any DB error so the Bridge degrades
    gracefully (needs_repo=False callers still work with zero DB)."""
    try:
        async with get_db_session_superuser() as s:
            run = (
                await s.execute(select(Run).where(Run.id == _as_run_id(run_id)))
            ).scalar_one_or_none()
            if run is None:
                return {}
            return {f: getattr(run, f, None) for f in _MIRROR_FIELDS}
    except Exception as exc:  # never break the activity on a read
        logger.warning("_read_run_upstream(%s) failed: %s", run_id, exc)
        return {}


@dataclass
class PipelineSession:
    run_id: str
    work_dir: str | None = None
    _upstream: dict = field(default_factory=dict)
    _workspace: RunWorkspace | None = None

    def captured_artifact(self, get_session_state_fn: Callable[[str], Any]) -> Any:
        """Read this run's structured artifact out of the agent's per-session state.

        `get_session_state_fn` is the agent's own session-state getter (each standalone
        agent has one keyed by session_id). Prefer this over the final-message summary.
        Returns None if the agent left no structured state."""
        try:
            return get_session_state_fn(self.run_id)
        except Exception as exc:
            logger.warning("captured_artifact(%s) failed: %s", self.run_id, exc)
            return None


@asynccontextmanager
async def pipeline_session(
    input: Any, agent_id: str, *, needs_repo: bool = False,
    repo_ref: Optional[dict] = None,
):
    """Enter: bind session_id=run_id, mirror run upstream into the run-keyed session,
    optionally clone the repo workspace. Exit: clear the contextvar (always)."""
    run_id = str(input.run_id)
    tenant_id = getattr(input, "tenant_id", None)
    token = set_session_id(run_id)
    ps = PipelineSession(run_id=run_id)
    try:
        upstream = await _read_run_upstream(run_id)
        ps._upstream = upstream

        # Mirror canonical runs artifacts into the run-keyed session the agents' tools
        # read. Best-effort (upsert swallows DB errors); idempotent on retry.
        present = {k: v for k, v in upstream.items() if v is not None}
        await upsert_agent_session(
            run_id, agent_type=agent_id, tenant_id=tenant_id,
            current_stage=agent_id, **present,
        )

        if needs_repo and repo_ref and repo_ref.get("repo_url"):
            try:
                ws: RunWorkspace = await prepare_run_workspace(
                    run_id,
                    repo_ref["repo_url"],
                    repo_ref.get("ref") or "main",
                    base=repo_ref.get("base"),
                    pat=repo_ref.get("pat"),
                )
                ps.work_dir = ws.work_dir
                ps._workspace = ws  # caller reads ps._workspace to seed agent session_state
            except Exception as exc:  # noqa: BLE001 — repo prep must never crash the turn;
                # the agent still runs, just without a cloned workspace (ps.work_dir=None).
                logger.warning(
                    "pipeline_session repo prep failed (run=%s, agent=%s): %s",
                    run_id, agent_id, exc,
                )

        yield ps
    finally:
        reset_session_id(token)
