"""The project's approved documents, rendered for the Orchestrator's prompts.

`approved_documents_context(project_id, tenant_id)` returns a markdown block listing
every APPROVED document in the project — grouped by the agent that produced it, with
the id an agent needs to read one — and `""` when the project holds none.

WHY THIS EXISTS. The document artifact system and this engine were built side by side.
A document a standalone agent produced and a person approved is listed on that agent's
page under Documents, and the standalone agents read it through the shared
`list_project_documents` / `read_document` tools (`shared/tools/project_documents.py`).
The Orchestrator was told none of it. Its router answers questions about existing work
ITSELF — that is the direct-reply path in `router.route` — and the only context that
call had was the conversation and the agent roster. So a project with an approved BRD
on its Requirements page was described, in the same project's Orchestrator, as "a fresh
or empty project context". Honest, from nothing.

Two readers, one block:

  · the ROUTER, whose prompt gets it appended so "what artifacts exist?" is answered
    from the record and "give me a PDF of the BRD" routes to the agent whose stage
    produced the BRD;
  · the DISPATCHED AGENT, whose context gets it so it knows what exists and which id to
    `read_document` without having to guess that the tool is worth calling.

METADATA ONLY, NEVER CONTENTS — the same split `shared/tools/project_documents.py`
draws, for the same reason: a 200-page PDF inlined into every turn is the whole window
spent on something the model may have needed one section of. Eight of the nine agents
bind `read_document`; the block says so, so the model knows the text is one call away.

WHY IT IS NOT PART OF `context.handoff_context`
------------------------------------------------
`handoff_context` answers "what has this RUN produced" — keyed by run and tenant, read
from `orchestrator_deliverables`, no approval concept. This answers "what has the
PROJECT approved", keyed by project, read from `artifacts`, where approval is the whole
gate. Different key, different table, different provenance, so it is composed beside
the other blocks in `ws.py` — exactly as `attachments.py` is — and `handoff_context`'s
signature stays alone.

A FAILED READ IS NOT AN EMPTY PROJECT
--------------------------------------
The split `context.py` and `attachments.py` draw, drawn again:

  · RETURNS `""` — the run has no project, or the project holds no approved document.
    The honest answer to "what has been approved?" is "nothing", and no read failed
    to produce it.

  · RAISES `ProjectDocumentsUnavailableError` — the read itself failed. Returning `""`
    there would have the Orchestrator tell the user their approved BRD does not exist,
    and the user would read that answer as informed. `ws.py` renders the raised error
    as a visible refusal of the turn instead.

APPROVAL IS ENFORCED BY THE QUERY, NOT HERE. `_load_approved_documents` goes through
`readable_documents`, whose one gate is `approval_status == "approved"`, so a pending or
rejected document cannot reach this block by any path. A direct `select(Artifact)` here
would be a second, unguarded way in.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from agents_orchestrator.orchestrator2.deliverables import DISPLAY_NAME
from agents_orchestrator.orchestrator2.registry import AGENT_IDS
from shared.db import get_db_session_for_tenant
from shared.services.artifact_versions import readable_documents

logger = logging.getLogger(__name__)


class ProjectDocumentsUnavailableError(Exception):
    """The project's documents could not be read, so what it holds is UNKNOWN.

    Deliberately distinct from an empty result, exactly as
    `context.ContextUnavailableError` is. Callers must not catch this and substitute
    `""` — that would report an outage to the Orchestrator as "nothing has been
    approved", and it would say so to the user.
    """


# ── the size budget ──────────────────────────────────────────────────────────
#
# THIS BLOCK'S OWN BOUND, separate from `context.MAX_CONTEXT_CHARS` (deliverables and
# transcript) and `attachments.MAX_ATTACHMENTS_CHARS`, because the three compete for
# the same window and a shared cap would let one evict another.
#
# 12,000 characters — the same cap `list_project_documents` puts on its own answer —
# is roughly 3,000 tokens and about eighty rows. A project with more approved documents
# than that is rare; when it happens the block keeps the newest-approved rows and says
# how many it left out, rather than silently showing a slice as the whole.
MAX_CONTEXT_CHARS: int = 12_000

HEADER_LINE = "--- APPROVED DOCUMENTS IN THIS PROJECT ---"
FOOTER_LINE = "--- END APPROVED DOCUMENTS IN THIS PROJECT ---"

_HEADER = (
    f"{HEADER_LINE}\n\n"
    "These documents have been produced in this project — by its agents or uploaded by "
    "a person — and APPROVED into the project's record. They exist whether or not this "
    "conversation mentions them. This is metadata only: to read one, call "
    "`read_document` with its id. Do not tell the user the project has no documents "
    "while this list is non-empty.\n\n"
)
_FOOTER = f"\n{FOOTER_LINE}\n"

_PROJECT_WIDE = "Project-wide"

#: Announced when rows were cut to fit. Reserved in the arithmetic so the total can
#: never exceed `MAX_CONTEXT_CHARS` whatever the count.
_OMITTED_TEMPLATE = "\n_(and {n:,} more approved document(s) not listed here)_\n"
_OMITTED_RESERVE = 80


def _as_uuid(value: Any) -> uuid.UUID | None:
    """The value as a UUID, or None when it is not one — `context._as_uuid`'s rule."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _group_name(stage: Any) -> str:
    """The heading a document is filed under: the producing agent's display name.

    `plan` is the Project Manager agent, and that is what the reader must see; an
    id the engine cannot name is shown as-is rather than dropped — the record holds it,
    so the block must too.
    """
    if not stage:
        return _PROJECT_WIDE
    return DISPLAY_NAME.get(str(stage), str(stage))


def _group_order(stage: Any) -> tuple[int, str]:
    """Agents in the engine's stable order, then unknown stages, then project-wide."""
    if not stage:
        return (2, "")
    stage = str(stage)
    if stage in AGENT_IDS:
        return (0, f"{AGENT_IDS.index(stage):03d}")
    return (1, stage)


def _human_size(size: Any) -> str | None:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _row(doc: dict[str, Any]) -> str:
    title = str(doc.get("title") or doc.get("type") or "document")
    parts = [f"- **{title}** — id `{doc.get('id')}`"]
    kind = doc.get("type")
    if kind and kind != "document":
        parts.append(f"type {kind}")
    size = _human_size(doc.get("sizeBytes"))
    if size:
        parts.append(size)
    approver = doc.get("approvedBy")
    approved_at = str(doc.get("approvedAt") or "")[:10]
    if approver and approved_at:
        parts.append(f"approved by {approver} on {approved_at}")
    elif approver:
        parts.append(f"approved by {approver}")
    elif approved_at:
        parts.append(f"approved on {approved_at}")
    return "; ".join(parts) + "\n"


def render(docs: list[dict[str, Any]]) -> str:
    """The block for these documents, or `""` for none. Pure; no I/O.

    Rows are filed under the producing agent, in the engine's agent order, with
    project-wide documents last. Within a group the newest approval comes first, so a
    re-approved document reads as current rather than as one of several.

    Stays inside `MAX_CONTEXT_CHARS`: rows are added in order until the next one would
    not fit alongside the footer and the omission note, and the note then says how many
    were left out. A silently shortened list is the defect this module exists to remove
    in another costume.
    """
    if not docs:
        return ""

    groups: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        groups.setdefault(_group_name(doc.get("stage")), []).append(doc)
    ordered = sorted(groups.items(), key=lambda kv: _group_order(kv[1][0].get("stage")))

    budget = MAX_CONTEXT_CHARS - len(_HEADER) - len(_FOOTER) - _OMITTED_RESERVE
    parts: list[str] = [_HEADER]
    used = 0
    shown = 0
    total = len(docs)
    cut = False
    for name, items in ordered:
        heading = f"### {name}\n"
        if used + len(heading) > budget:
            cut = True
            break
        parts.append(heading)
        used += len(heading)
        for doc in sorted(items, key=lambda d: str(d.get("approvedAt") or ""), reverse=True):
            row = _row(doc)
            if used + len(row) > budget:
                cut = True
                break
            parts.append(row)
            used += len(row)
            shown += 1
        if cut:
            break
        parts.append("\n")
        used += 1

    if cut and shown < total:
        parts.append(_OMITTED_TEMPLATE.format(n=total - shown))
        logger.info(
            "orchestrator2 project documents block listed %d of %d approved documents",
            shown, total,
        )
    parts.append(_FOOTER)
    return "".join(parts)


async def _load_approved_documents(project_id: str, tenant_id: str) -> list[dict[str, Any]]:
    """Every approved document in the project, as `readable_documents` describes them.

    Read inside the tenant's session so row-level security applies, and through the
    one query whose gate is approval. Raises whatever the read raised; the caller turns
    that into `ProjectDocumentsUnavailableError`.
    """
    async with get_db_session_for_tenant(tenant_id) as db:
        return await readable_documents(db, project_id)


async def approved_documents_context(project_id: str | None, tenant_id: str) -> str:
    """The project's approved documents, rendered for a prompt — or `""` for none.

    `""` never means the read failed — that raises `ProjectDocumentsUnavailableError`,
    with the cause chained.
    """
    if not project_id:
        # A run with no project (webhook runs carry a provider key, not a project) has
        # no record to list. "Nothing" is the honest answer, and no read failed.
        return ""
    if _as_uuid(project_id) is None or _as_uuid(tenant_id) is None:
        # An id that cannot address a row — the same answer `context.py` gives, and
        # logged for the same reason: it can only be a caller bug, but "nothing" is
        # still well-defined, and pushing caller text into a UUID comparison is not.
        logger.warning(
            "orchestrator2 project documents asked for an unusable id: project=%r "
            "tenant=%r", project_id, tenant_id,
        )
        return ""
    try:
        docs = await _load_approved_documents(str(project_id), str(tenant_id))
    except Exception as exc:  # noqa: BLE001 — unknown != empty; see the class docstring
        logger.warning(
            "orchestrator2 could not read approved documents for project=%s tenant=%s: "
            "%s — raising rather than reporting an empty project",
            project_id, tenant_id, exc,
        )
        raise ProjectDocumentsUnavailableError(
            "the project's approved documents could not be read"
        ) from exc
    return render(docs)
