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

#: A floor, not the test. Nothing shorter than this can be a document, but passing it
#: proves nothing on its own — see `looks_like_a_document`.
#:
#: This USED to be the whole rule, inherited from the Copilot and never re-examined.
#: It produced eight "Development Report" rows in one session, every one an ordinary
#: chat message: listing the branches in a repo is well over 200 characters.
MIN_DELIVERABLE_CHARS = 400

#: A document announces its own structure. Two or more markdown headings is the signal
#: that survives across agents — a PRD, a design doc, a security review and a test plan
#: all have them, and none of "here are the branches", "shall I go ahead?" or "created
#: and switched to X" does.
_HEADING_LINE_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")
_MIN_HEADINGS = 2

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


#: How much of the reply the refusal check reads. An agent that cannot do the work
#: says so before it says anything else — all three refusals observed live declared it
#: inside the first 250 characters. Reading the WHOLE body instead would reject a real
#: report that happens to say "I cannot cover the sandbox here" in its last section,
#: and rejecting real documents is the failure this area keeps producing from the
#: other direction.
_REFUSAL_WINDOW_CHARS = 700

#: FIRST PERSON, deliberately. A security review is MADE of sentences about what
#: cannot be done — "the endpoint cannot validate the total", "sessions are unable to
#: survive a refresh" — and those are its findings. What disqualifies a document is the
#: AGENT saying it did not do the work, not the subject matter saying something is
#: broken. Matching "cannot" without the pronoun would make Security unable to file a
#: review at all, which is a worse bug than the one this fixes.
_REFUSAL_OPENING_RE = re.compile(
    r"(?i)\bI\s+(?:"
    r"can(?:no|')?t\b"
    r"|can\s+not\b"
    r"|(?:a|')m\s+unable\b"
    r"|am\s+not\s+able\b"
    r"|do(?:\s+not|n't)\s+have\s+access\b"
    r"|need\s+to\s+stop\s+here\b"
    r")"
)

#: A section title that announces the work did not happen. Narrow on purpose: only
#: phrases that cannot be a heading in a document that DID get produced. "What I Can
#: Do" is deliberately absent — it is a plausible heading in a real plan, and every
#: refusal that used it also declared itself in the first person above.
_REFUSAL_HEADING_RE = re.compile(
    r"(?im)^\s{0,3}#{1,6}\s+.*\b("
    r"not\s+possible"
    r"|cannot\s+proceed"
    r"|can't\s+proceed"
    r"|unable\s+to\s+proceed"
    r"|missing\s+prerequisites"
    r")\b"
)


#: A link into the platform's own generated-artifact mount. Agents that export a
#: document write it to `{FILES}/<user>/<segment>/<run>/output/` and hand back a
#: `/generated/...` URL over it, so a reply carrying one of these is a message ABOUT a
#: document, not the document.
#:
#: Requires the URL FORM, not the bare word: a design document is free to discuss a
#: `/generated/` directory in prose without announcing its own location. The scheme
#: and host are left open because `AGENTIC_BASE_URL` differs per deployment.
_GENERATED_LINK_RE = re.compile(r"(?i)https?://[^\s)]*/generated/[^\s)]+")


def announces_a_saved_file(body: str) -> bool:
    """Is this reply telling the user where a document went, rather than being one?

    FOUND BY RUNNING ALL NINE AGENTS LIVE. The other three of the six captured
    deliverables were exactly this: "📄 Download Your PRD" over a link to
    `coffee_ordering_app_prd.docx`, and "Plan Summary" over a link to
    `Coffee_Ordering_App_Delivery_Plan.pdf`. Between these and the refusals, NOT ONE of
    the nine agents filed an actual document.

    The file itself reaches the panel through `pointers_for_run`, which synthesises a
    file tree for every stage that wrote something — so declining to file the
    announcement beside it loses nothing, and the link stays in the chat where a link
    belongs.
    """
    return bool(_GENERATED_LINK_RE.search(body))


def looks_like_a_refusal(body: str) -> bool:
    """Is this the agent explaining that it could NOT do the work?

    FOUND BY RUNNING ALL NINE AGENTS LIVE. Three of six captured deliverables were
    refusals: "⚠️ Security Review Not Possible", "Cannot Proceed — Missing
    Prerequisites", "What I Can Do". Every one of them clears the structure rule —
    real headings, well over 400 characters — because a good refusal is structured.
    That is the door the structure rule cannot close.

    It is also the worst thing to file. A user opening Deliverables sees a Security
    Review filed under Security whose content is the opposite of what its title
    promises, and the tab stops meaning anything.
    """
    return bool(
        _REFUSAL_OPENING_RE.search(body[:_REFUSAL_WINDOW_CHARS])
        or _REFUSAL_HEADING_RE.search(body)
    )


def looks_like_a_document(body: str) -> bool:
    """Is this a produced DOCUMENT, or is it conversation?

    "a deliverable is the document created — a proper document created and saved, for
    eg design docs for design agent — not normal chats."

    Length alone cannot answer that, and using it produced a Deliverables tab holding
    eight chat messages. Two things do:

      · STRUCTURE. A document carries markdown headings; a chat reply does not. A PRD, a
        design doc, a security review and a test plan all have them. "Here are the
        branches", "Shall I go ahead?" and "Created and switched to X" do not.
    A "does it end in a question" check was tried and REMOVED: every conversational
    reply it would have caught already fails the structure test, so it was a branch no
    test could kill. And it would have been wrong at the edges anyway — a design
    document that closes with "let me know if you'd like changes" is still a document,
    and punishing an agent for being conversational about its own output would empty
    the tab for the opposite reason.

      · COMPLETION. A refusal is structured too — see `looks_like_a_refusal`. Three of
        the six deliverables captured in the first all-nine live run were agents
        explaining, in good markdown, that they could not do the work.

      · FIRST-HAND-NESS. So is a summary of a document saved elsewhere — see
        `announces_a_saved_file`. That was the other three, which together meant not
        one of the nine agents filed an actual document.
    """
    if len(body) < MIN_DELIVERABLE_CHARS:
        return False
    if len(_HEADING_LINE_RE.findall(body)) < _MIN_HEADINGS:
        return False
    return not looks_like_a_refusal(body) and not announces_a_saved_file(body)


#: The receipt an exporter prepends to the document it just wrote — see
#: `design_architecture_agent/agents/architecture.py::_with_save_receipt`. It names the
#: saved file so the model can quote the link rather than inventing one.
#:
#: It has to come off before the document is judged. `announces_a_saved_file` rejects a
#: body carrying a `/generated/` link, which is right for a CHAT reply that points at a
#: document stored elsewhere and wrong for a tool result that IS the document with a
#: receipt stapled on. Stripping keeps one rule instead of carving an exception into it.
#: MATCHED TO THE WHOLE LINE, not to a filename. An earlier version required the line
#: to END at the URL (`SAVED:\s*\S+$`) — a shape the exporter never produces. Both real
#: receipts carry trailing prose:
#:
#:     SAVED: High_Level_Design.docx — download: http://…/generated/…/x.docx
#:     SAVED: High_Level_Design.docx (in this run's output folder; no download link…)
#:
#: and a second line follows: "(Written automatically — do NOT call save_architecture
#: …)". So the receipt survived stripping, its `/generated/` link made
#: `announces_a_saved_file` reject the whole document as a mere announcement, and the
#: panel went on showing "Binary file. This file can't be displayed as text."
#:
#: The pattern was written against a fixture invented for the test rather than against
#: `_with_save_receipt`, which is exactly why the tests passed and the product did not.
#: The tests now build their input from that function.
_SAVE_RECEIPT_RE = re.compile(
    r"(?im)^[ \t]*(?:SAVED:.*|\(Written automatically[^\n]*\))[ \t]*$"
)


def strip_save_receipt(tool_output: Any) -> str:
    """A tool's output with the exporter's `SAVED: <url>` receipt removed.

    REPORTED: the Design agent wrote a complete architecture document — the user
    downloaded the .docx and every table and diagram was there — and the panel showed
    "Binary file. This file can't be displayed as text." The document lived in a tool
    result, which `dispatch` reports as activity and whose content it drops, so
    `capture` (which reads the streamed reply) had nothing to store and the only trace
    was the file itself.

    THIS DOES NOT STREAM ANYTHING. The existing reasoning stands: forwarding a tool
    result as a `stream_chunk` puts it in the transcript as if the agent had said it.
    The document becomes a deliverable the panel renders — mermaid and all — while the
    chat keeps the short summary the agent actually wrote.

    Judged by exactly the same rule as a streamed reply, deliberately: most tool
    results are status lines, ids and JSON, and capturing those refills the tab with
    the noise the document rule exists to keep out. A refusal returned by a tool is
    still not a deliverable.
    """
    return _SAVE_RECEIPT_RE.sub("", str(tool_output or "")).strip()


def render(agent_id: str, reply_text: str) -> list[dict]:
    """Turn one agent turn into deliverable rows. PURE — no IO, no database.

    Returns `[]` when the turn produced conversation rather than a document. Rows
    carry no `id`: the persistence layer assigns the row uuid, so two versions of
    the same document can never collide on a slug.
    """
    body = (reply_text or "").strip()
    if not looks_like_a_document(body):
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



async def capture_tool_document(
    agent_id: str,
    tool_output: Any,
    *,
    run_id: str,
    tenant_id: str,
    project_id: str | None,
) -> list[dict]:
    """Persist a document an agent produced through a tool.

    Delegates to `capture` with the receipt removed, rather than reimplementing the
    write: one persistence path means a tool-produced document and a streamed one land
    as the same shape of row, and a change to how deliverables are stored cannot apply
    to only one of them.

    Returns `[]` — writing nothing — when the tool output was not a document, which is
    most of the time.
    """
    body = strip_save_receipt(tool_output)
    if not body:
        return []
    return await capture(
        agent_id, body, run_id=run_id, tenant_id=tenant_id, project_id=project_id,
    )
