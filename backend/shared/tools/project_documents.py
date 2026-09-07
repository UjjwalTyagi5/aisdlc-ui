"""Agent tools for reading a project's approved documents.

WHAT AN AGENT COULD NOT DO BEFORE. Every "artifact" an agent read was a JSONB stage
payload. The `artifacts` table — the DOCUMENTS, the uploaded PDFs and generated DOCX —
was entirely UI-facing, so the PM agent could not see a Requirements document whether or
not anybody had approved it.

TWO TOOLS, AND THE SPLIT IS DELIBERATE:

    list_project_documents()   metadata: name, scope, who approved it, when
    read_document(id)          the extracted text of ONE document

Inlining text into the listing would make an agent pay for every attached document on
every turn, including the turns where it only wanted to know what exists. A 200-page PDF
is around 400k characters — the whole context window, spent on something the model may
have needed one section of.

THE RULE IS ENFORCED IN THE SERVICE, NOT HERE. `read_document_for_agent` re-resolves the
id and re-applies it, so the metadata this hands over is not a capability: a document
whose approval is withdrawn between the two calls stops being readable. These tools only
translate a refusal into a sentence the model can act on.

    approved, either scope                 every agent
    pending or rejected                    nobody

BOUND TO A STAGE BY A FACTORY. `consumer_stage` is recorded on every read, so the
evidence trail says which agent read what. It comes
from the agent that registers the tool, never from the model — a tool argument would let
a prompt claim to be a different agent.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def make_document_tools(consumer_stage: str) -> list[Any]:
    """The two document tools, bound to the agent registering them.

    Returns them as a list to be spread into the agent's tool list, matching how the
    MCP tools are wired.
    """

    @tool
    async def list_project_documents() -> str:
        """List the approved documents this agent may read.

        Covers documents published alongside another stage's signed-off work, plus any
        project-wide document (a policy, a standard). Returns names and who approved
        them — NOT their contents. Call `read_document` with an id to read one.

        Returns a JSON array, or a sentence explaining why it is empty.
        """
        from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

        project_id, tenant_id = get_project_id(), get_tenant_id()
        if not project_id or not tenant_id:
            return (
                "This conversation is not attached to a project, so there are no "
                "stored documents to read."
            )

        from shared.db import get_db_session_for_tenant  # noqa: PLC0415
        from shared.services.artifact_versions import (  # noqa: PLC0415
            readable_documents,
        )

        try:
            async with get_db_session_for_tenant(tenant_id) as db:
                # No `covered_ids`: approval is the whole gate now, so walking every
                # stage's published version to collect `covers` bought one query per
                # stage and changed nothing about the answer.
                docs = await readable_documents(db, project_id)
        except Exception as exc:  # noqa: BLE001 — degrade, do not kill the turn
            logger.warning(
                "list_project_documents failed for %s: %s",
                project_id, type(exc).__name__, exc_info=True,
            )
            return f"The document list could not be read ({type(exc).__name__})."

        if not docs:
            # A REAL ANSWER, not an error. Saying "none" without saying why sends the
            # model looking for a bug that is not there.
            return (
                "No documents are available to this agent yet. A document becomes "
                "readable once its owner approves it."
            )
        return json.dumps(docs, indent=2)[:12000]

    @tool
    async def read_document(document_id: str) -> str:
        """Read the text of ONE approved document, by id from `list_project_documents`.

        Long documents are truncated with an explicit marker — if you see it, the
        document continues beyond what you were given.

        Returns the text, or a sentence explaining why it cannot be read.
        """
        from config.ws_helper import (  # noqa: PLC0415
            get_project_id, get_session_id, get_tenant_id,
        )
        from shared.services.artifact_consumption import (  # noqa: PLC0415
            read_document_for_agent,
        )

        project_id, tenant_id = get_project_id(), get_tenant_id()
        text, note = await read_document_for_agent(
            tenant_id=tenant_id or "",
            project_id=project_id or "",
            artifact_id=(document_id or "").strip(),
            consumer_stage=consumer_stage,
            # The session id IS the run id in pipeline mode, which is what makes the
            # read attributable to a run in the evidence view.
            consumer_run_id=get_session_id() or None,
        )
        if text is None:
            return f"That document could not be read: {note}"
        return text

    return [list_project_documents, read_document]
