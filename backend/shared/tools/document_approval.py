"""Agent tool: raise one of the project's draft documents for approval.

THE QUESTION THE AGENT COULD NOT ANSWER. "Okay, can you send it for approval?" got
"I cannot send it for approval myself — that step must be performed by an owner or
project admin". Both halves were wrong: the agent had no tool, and raising is NOT an
owner's act — it is `run:create`, the same permission as running the agent (see
`shared/routers/artifacts.py::submit_artifact`). The user asking could do it with a click;
the agent refused on their behalf with an invented reason.

WHAT IT DOES. Exactly what the Documents panel's "Raise for approval" button does —
through `shared.services.artifact_approval`, the one implementation both use — with the
signed-in user's permission, checked here against this project. It never approves:
approving stays a project admin's decision in Requests & Approvals.

BOUND TO A STAGE BY A FACTORY. An agent raises its own stage's documents and project-wide
ones, the same reach publishing has; a Design document is raised from the Design screen.
The stage comes from the agent registering the tool, never from a tool argument.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _leaf(blob_path: Optional[str]) -> str:
    return (blob_path or "").replace("\\", "/").rsplit("/", 1)[-1]


async def _documents_named(db, project_id: str, filename: str) -> list:
    """This project's documents whose stored file is `filename`, newest first."""
    from sqlalchemy import select  # noqa: PLC0415

    from shared.models.orm import Artifact  # noqa: PLC0415

    rows = (await db.execute(
        select(Artifact).where(Artifact.project_id == project_id).order_by(Artifact.created_at.desc())
    )).scalars().all()
    return [a for a in rows if a.artifact_type != "story" and _leaf(a.blob_path) == filename]


async def raise_for_approval(filename: str, *, stage: str) -> str:
    """The tool's body, callable without LangChain for tests."""
    from config.ws_helper import get_project_id, get_tenant_id, get_user_id  # noqa: PLC0415
    from shared.authz.can_perform import can_perform  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.services.artifact_approval import AlreadyDecided, submit_for_approval  # noqa: PLC0415

    name = _leaf((filename or "").strip())
    if not name:
        return "Error: name the document to raise, exactly as it appears in the project's Documents."
    project_id, tenant_id, user_id = get_project_id(), get_tenant_id(), get_user_id()
    if not (project_id and tenant_id and user_id):
        return "Error: this conversation is not attached to a project, so there is nothing to raise."

    async with get_db_session_for_tenant(tenant_id) as db:
        allowed = await can_perform(
            db, user_id=str(user_id), permission="run:create", tenant_id=str(tenant_id),
            resource_kind="project", resource_id=str(project_id),
        )
        if not allowed:
            return (
                "Error: you do not have permission to raise documents for approval on this "
                "project. Nothing was changed."
            )

        named = await _documents_named(db, str(project_id), name)
        if not named:
            return (
                f"Error: there is no document named '{name}' in this project's Documents. "
                "Nothing was changed — check the exact file name."
            )
        mine = [a for a in named if a.stage is None or a.stage == stage]
        if not mine:
            return (
                f"Error: '{name}' belongs to the {named[0].stage} stage, not this one. Raise it "
                "from that agent's screen. Nothing was changed."
            )
        # A regenerated document keeps its name only with a version suffix, but an upload
        # can repeat one: the newest draft is the one being asked about.
        drafts = [a for a in mine if (a.approval_status or "draft") == "draft"]
        target = drafts[0] if drafts else mine[0]

        try:
            moved = await submit_for_approval(db, target, tenant_id=str(tenant_id), actor_id=str(user_id))
        except AlreadyDecided as decided:
            return (
                f"Error: '{name}' is already {decided.status}. A decided document cannot be "
                "raised again. Nothing was changed."
            )
        target_id = str(target.id)

    logger.info("document %s raised for approval from the %s agent by %s", target_id, stage, user_id)
    if not moved:
        return (
            f"'{name}' is already awaiting approval — nothing more to do. A project admin "
            "decides it in Requests & Approvals."
        )
    return (
        f"Raised '{name}' for approval. It is now PENDING in Requests & Approvals, where a "
        "project admin approves or rejects it. It is not approved yet — do not say it is."
    )


def make_approval_tools(stage: str) -> list[Any]:
    """The raise-for-approval tool, bound to the agent registering it."""

    @tool
    async def raise_document_for_approval(filename: str) -> str:
        """Raise one of this project's DRAFT documents for approval — the same act as the
        "Raise for approval" button in the Documents panel, done with the signed-in user's
        permission. Call it when the user asks to send, submit or raise a document for
        approval. It does NOT approve anything: a project admin decides afterwards.

        Args:
            filename: the document's file name exactly as it appears in the project's
                Documents, e.g. "TEST_Project_BRD.docx".
        """
        return await raise_for_approval(filename, stage=stage)

    return [raise_document_for_approval]
