"""What an Orchestrator agent produced, and how it is stored and read back.

A DELIVERABLE IS NOT AN ARTIFACT. The `artifacts` table carries `approval_status`
because a STANDALONE agent wrote the row and a human accepts it. The Orchestrator's
agents share the standalone agents' names and capability and are not the same thing:
the runner is a Project Admin who already owns all nine, and there are no gates
(spec D5). So Orchestrator output is a separate concept in a separate table with no
approval column, and nothing here ever writes to `artifacts`.

This module is split the way `shared/services/orchestrator/artifacts_view.py` is —
a PURE core (`render`, `derive_title`, `pointers_for_run`) with no IO, and a thin
persistence shell around it — so the part that decides what a turn produced is
testable without a database.

POINTERS ARE NOT STORED. The Development code tree, a per-agent file tree and the
pull-request link are not documents; they are references to state that already lives
elsewhere (the run workspace, disk, Azure DevOps). Storing one per turn would stack a
duplicate row on every turn the agent ran, and their ids must stay STABLE anyway
because the panel de-dupes the Development tree on the literal id `dev-code`. They
are synthesized on read instead — see `pointers_for_run`.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from agents_orchestrator.orchestrator2.registry import AGENT_IDS
from shared.services.orchestrator.artifacts_view import parse_design_markdown

logger = logging.getLogger(__name__)

#: Below this, a reply is conversation rather than a document. An agent asking
#: "which service did you mean?" must not create a deliverable; a PRD must. The
#: value is the Copilot's, kept so behaviour does not shift under the rename.
MIN_DELIVERABLE_CHARS = 200

#: User-facing agent names. `plan` is the PROJECT MANAGER agent and is never
#: called "Plan agent" or "PM agent" anywhere a user can read it.
DISPLAY_NAME: dict[str, str] = {
    "requirements": "Requirements",
    "design": "Design",
    "plan": "Project Manager",
    "development": "Development",
    "code_review": "Code Review",
    "security": "Security",
    "testing": "Testing",
    "deployment": "Deployment",
    "documentation": "Documentation",
}

# Every agent must have a display name, or a deliverable renders under a blank
# heading. Checked at import rather than at the first turn that hits the gap.
_missing = set(AGENT_IDS) - set(DISPLAY_NAME)
if _missing:  # pragma: no cover - import-time guard
    raise RuntimeError(f"DISPLAY_NAME is missing agents: {sorted(_missing)}")

_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$")


class DeliverableWriteError(Exception):
    """A deliverable could not be persisted.

    Raised rather than swallowed. The CALLER decides that a failed capture must not
    fail the turn — the agent has done its work and the user has read the reply — but
    that decision is made once, visibly, at the call site, not hidden here behind a
    bare `except`. A silent swallow is how the old engine's missing agents went
    unnoticed for so long.
    """


# ── pure core ────────────────────────────────────────────────────────────────


def derive_title(agent_id: str, body: str) -> str:
    """A distinguishing title for one deliverable.

    The document's own first heading when it has one — several versions of the same
    agent's output sit under one heading in the panel, so "Requirements Report" three
    times over tells the reader nothing about which is which.
    """
    match = _HEADING_RE.search(body or "")
    if match and match.group(1).strip():
        return match.group(1).strip()
    return f"{DISPLAY_NAME.get(agent_id, agent_id)} Report"


def render(agent_id: str, reply_text: str) -> list[dict]:
    """Turn one agent turn into deliverable rows. PURE — no IO, no database.

    Returns `[]` when the turn produced conversation rather than a document. Rows
    carry no `id`: the persistence layer assigns the row uuid, so two versions of
    the same document can never collide on a slug.
    """
    body = (reply_text or "").strip()
    if len(body) < MIN_DELIVERABLE_CHARS:
        return []

    if agent_id == "design":
        sections, _persist = parse_design_markdown(body)
        if sections:
            # `parse_design_markdown` speaks the panel's `stage` vocabulary; this
            # module speaks `agent`. Translate once, here, rather than teaching the
            # rest of the pipeline to accept both.
            return [
                {
                    "agent": agent_id,
                    "kind": section.get("kind") or "markdown",
                    "title": section.get("title") or derive_title(agent_id, body),
                    "content": section.get("content") or "",
                }
                for section in sections
            ]
        # Falls through deliberately: a design turn that does not parse is still a
        # document, and dropping it would lose the whole reply behind an empty panel,
        # which reads as an agent that said nothing.

    return [{
        "agent": agent_id,
        "kind": "markdown",
        "title": derive_title(agent_id, body),
        "content": body,
    }]


def pointers_for_run(
    dev_artifacts: dict | None,
    stages_with_files: set[str] | None = None,
) -> list[dict]:
    """Reference rows synthesized on read. PURE — no IO.

    These are not documents and are not stored: the code tree lives in the run
    workspace, the generated files live on disk, and the pull request lives in Azure
    DevOps. Storing one per turn would stack a duplicate on every turn. Their ids are
    STABLE (`dev-code`, `dev-pr`, `<agent>-files`) because the panel de-dupes the
    Development tree on the literal id `dev-code`.

    `stages_with_files` is computed by the CALLER, which is the only layer that can
    look at the disk. Passing it in keeps this function pure, and means a caller that
    cannot check the disk shows no file trees rather than guessing that some exist —
    an empty tree reads as a pull that failed, not as a stage with no files.
    """
    out: list[dict] = []
    if isinstance(dev_artifacts, dict) and dev_artifacts:
        if dev_artifacts.get("repo_url"):
            out.append({
                "id": "dev-code", "agent": "development", "kind": "code-tree",
                "title": "Repository code", "content": "", "url": None,
                "language": None, "source": "development", "created_at": None,
            })
        pr_url = dev_artifacts.get("pr_url") or dev_artifacts.get("pull_request_url")
        if pr_url:
            out.append({
                "id": "dev-pr", "agent": "development", "kind": "link",
                "title": "Pull request", "content": "", "url": pr_url,
                "language": None, "source": None, "created_at": None,
            })
    for agent_id in sorted(stages_with_files or ()):
        # `development` already has its code tree above; a second tree over the same
        # clone would be the same files twice under one heading.
        if agent_id == "development":
            continue
        out.append({
            "id": f"{agent_id}-files", "agent": agent_id, "kind": "file-tree",
            "title": "Generated files", "content": "", "url": None,
            "language": None, "source": agent_id, "created_at": None,
        })
    return out


# ── persistence ──────────────────────────────────────────────────────────────


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _to_wire(row: Any) -> dict:
    """One ORM row in the panel's shape.

    `agent_id` on the column, `agent` on the wire — the mapping happens here and
    nowhere else, so neither side has to know about the other's vocabulary.
    """
    created = getattr(row, "created_at", None)
    return {
        "id": str(row.id),
        "agent": row.agent_id,
        "kind": row.kind,
        "title": row.title,
        "content": row.content or "",
        "url": row.url,
        "language": row.language,
        "source": row.source,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
    }


async def capture(
    agent_id: str,
    reply_text: str,
    *,
    run_id: str,
    tenant_id: str,
    project_id: str | None,
) -> list[dict]:
    """Persist what this turn produced and return it in the panel's shape.

    `project_id` comes from the VERIFIED `runs` row, never from a client frame, and is
    keyword-required with no default for the same reason `run_agent`'s is: a default
    would let a future call site drop project scoping silently, which is exactly the
    bug that scoping was added to fix.
    """
    rows = render(agent_id, reply_text)
    if not rows:
        return []

    run_uuid, tenant_uuid = _as_uuid(run_id), _as_uuid(tenant_id)
    if run_uuid is None or tenant_uuid is None:
        raise DeliverableWriteError(
            f"run_id/tenant_id is not a uuid: {run_id!r}/{tenant_id!r}"
        )

    import shared.db as shared_db
    from shared.models.orm import OrchestratorDeliverable

    written: list[Any] = []
    try:
        async with shared_db.get_db_session_for_tenant(tenant_id) as session:
            for row in rows:
                record = OrchestratorDeliverable(
                    id=uuid.uuid4(),
                    run_id=run_uuid,
                    tenant_id=tenant_uuid,
                    project_id=_as_uuid(project_id) if project_id else None,
                    agent_id=row["agent"],
                    kind=row["kind"],
                    title=row["title"],
                    content=row.get("content") or "",
                    language=row.get("language"),
                )
                session.add(record)
                written.append(record)
            await session.commit()
        return [_to_wire(record) for record in written]
    except Exception as exc:  # noqa: BLE001 — re-raised typed, never dropped
        raise DeliverableWriteError(str(exc)) from exc


async def _load(run_id: str, tenant_id: str) -> list[dict]:
    run_uuid, tenant_uuid = _as_uuid(run_id), _as_uuid(tenant_id)
    if run_uuid is None or tenant_uuid is None:
        return []

    from sqlalchemy import select

    import shared.db as shared_db
    from shared.models.orm import OrchestratorDeliverable

    async with shared_db.get_db_session_for_tenant(tenant_id) as session:
        # BOTH predicates on purpose. `get_db_session_for_tenant` sets the tenant GUC,
        # so row-level security applies — but this deployment connects as a
        # rolbypassrls superuser, for whom policies do not apply at all, so the
        # explicit tenant_id below is what actually isolates tenants today. Neither is
        # left standing as the only one.
        stmt = (
            select(OrchestratorDeliverable)
            .where(
                OrchestratorDeliverable.run_id == run_uuid,
                OrchestratorDeliverable.tenant_id == tenant_uuid,
            )
            .order_by(OrchestratorDeliverable.created_at.desc())
        )
        result = await session.execute(stmt)
        return [_to_wire(row) for row in result.scalars().all()]


async def deliverables_for_run(run_id: str, tenant_id: str) -> list[dict]:
    """Every version this run holds, newest first. Tenant-scoped."""
    return await _load(run_id, tenant_id)


async def latest_per_agent(run_id: str, tenant_id: str) -> dict[str, Any]:
    """The newest deliverable per agent, for an agent's hand-off context.

    Every version stays VISIBLE in the panel; only the newest is FED to an agent.
    Handing a downstream agent two versions of the same PRD makes it guess which one
    is current, and it will sometimes guess wrong.
    """
    latest: dict[str, Any] = {}
    for row in await _load(run_id, tenant_id):  # already newest-first
        latest.setdefault(row["agent"], row)
    return latest
