"""Files the user attached to a run, rendered for the answering agent's prompt.

`attachment_context(run_id, user_id=...)` returns a markdown block carrying the TEXT of
every file the user uploaded to this run, and `""` when they uploaded none.

WHY THIS EXISTS. The Orchestrator's composer was a textarea and a Send button. The only
way to give an agent a BRD was to paste it, while every standalone agent has had an
attach control — and server-side extraction behind it — since
`shared/tools/document_tools.py` was written.

The failure being designed against is not the missing button. It is the one that looks
like success: a file that uploads, shows as a chip, and is never read. The user then
believes the agent saw the document and reads its answer as informed. That is worse
than having no attach button, so this module's whole job is getting the CONTENT into
the prompt — and being loud when it could not.

WHY IT IS NOT PART OF `context.handoff_context`
------------------------------------------------
`handoff_context(run_id, tenant_id, target_agent)` answers "what has this RUN
produced" — deliverables and the conversation, both written by the engine, both keyed
by run and tenant. Attachments answer a different question, "what did this PERSON give
it", and are keyed by (user, run): `attachment_store` writes them under
`files/{user_id}/attachments/{session_id}/`. Two different scope keys and two different
provenances, so they are composed side by side in `ws.py` rather than merged here. That
also keeps `handoff_context`'s signature — and its forty call sites — alone.

The two blocks are labelled differently on purpose. A deliverable renders under "WORK
ALREADY ON THIS RUN"; rendering a user's own draft the same way would have the agent
citing it back to them as a completed artifact.

WHICH TURN AN ATTACHMENT BELONGS TO — EVERY TURN ON THE RUN
------------------------------------------------------------
Not the turn it was attached to. This engine has no agent order: the router picks any
of the nine from the conversation, which is the same reason `context.py` includes every
deliverable whatever produced it and whenever. A BRD attached while Requirements was
answering is precisely what Design needs three turns later, and the transcript is no
substitute — it carries the WORDS of a turn, never the file's content, so a file scoped
to its own turn would be unrecoverable the moment that turn ended.

The cost of run-scoping is that the block grows with every upload, which is what the
budget below exists to bound.

WHAT CANNOT BE READ IS NAMED AND DISCLAIMED, NEVER SILENTLY DROPPED
--------------------------------------------------------------------
`extract_file_text` handles .pdf, .docx, .doc, .txt, .md, .csv, .xlsx and .xls. It does
NOT handle images, and neither does this engine: `dispatch.run_agent` builds text-only
`SystemMessage`/`HumanMessage` content, so there is no vision path for an image to
travel down even when the resolved model has one. An attached .png is therefore a file
the agent genuinely cannot see.

Both halves of the honest answer are rendered: the file is NAMED, so the user's chip
corresponds to something in the prompt, and the block states outright that it could not
be read and must not be claimed as seen. Saying nothing would let the agent infer from
the file name; passing the extractor's own placeholder through would be worse still —
"[Binary file: shot.png]" is a readable sentence, which is exactly why
`extraction_succeeded` exists and why a truthiness check on the extractor's return
value is not good enough. That mistake has already been made once on this platform and
produced an agent that announced a screenshot as document content and then dead-ended
on "local file not found".

A FAILED READ IS NOT AN EMPTY RUN
----------------------------------
The split `context.py` draws, drawn again here for the same reason:

  · RETURNS `""` — the user attached nothing, or named an id that cannot address a
    directory. The honest answer to "what did the user attach?" is "nothing", and no
    read failed to produce it.

  · RAISES `AttachmentsUnavailableError` — the LISTING itself failed. Returning `""`
    there would tell the agent the user attached nothing, and the agent would then ask
    for a document the user had already given it.

An individual file that cannot be EXTRACTED is neither: the listing succeeded, so what
the run holds is known, and one bad file must not raise away the others. It is reported
in the unreadable section alongside the images.
"""
from __future__ import annotations

import asyncio
import logging

# The shortening rule, borrowed rather than forked. `context._shorten` keeps the START
# and the END of a document and drops the middle, because requirements, acceptance
# criteria and decisions live at the END of a specification — head-only truncation
# hands the agent an introduction and invites it to infer the rest. That reasoning is
# identical for an attached spec, and a second copy here would be a second thing to fix.
# The import is one-way: `context.py` does not import this module (see the docstring
# above), so there is no cycle.
from agents_orchestrator.orchestrator2.context import _shorten
from shared.services import attachment_store
from shared.tools.document_tools import extract_file_text, extraction_succeeded

logger = logging.getLogger(__name__)


class AttachmentsUnavailableError(Exception):
    """The run's attachments could not be listed, so what the user gave it is UNKNOWN.

    Deliberately distinct from an empty result, exactly as
    `context.ContextUnavailableError` is. Callers must not catch this and substitute
    `""` — that would report a disk failure to the agent as "the user attached
    nothing", and the agent would re-ask for a document it had already been given.
    """


# ── the size budget ──────────────────────────────────────────────────────────
#
# THIS BLOCK'S OWN BOUND, separate from `context.MAX_CONTEXT_CHARS` (72,000, deliverables
# and transcript) because attachments compete with those for the same window and a
# shared cap would let a big upload evict the run's own work.
#
# 48,000 characters is roughly 12,000 tokens. Big enough for a real specification to
# survive whole — a 20-page BRD runs around 40,000 characters — and small enough that
# the three context blocks together stay a slice of a 200k window rather than most of
# it. A judgement, not a calculation, and the same shape of judgement as
# `MAX_CONTEXT_CHARS`.
#
# WHAT HAPPENS TO A FILE THAT EXCEEDS IT: truncated, with a marker, never refused. The
# 10 MB limit in `attachment_store.MAX_ATTACHMENT_BYTES` is already the hard refusal;
# refusing again here would mean a 300-page PDF cannot be attached at all. Truncating
# lets the agent use most of the document, and the marker is what stops it answering
# confidently from a document it only half received — a silently shortened document is
# `context.py`'s defect 3 in another costume.
MAX_ATTACHMENT_CONTEXT_CHARS: int = 48_000

_HEADER = (
    "--- FILES THE USER ATTACHED TO THIS RUN ---\n\n"
    "The user uploaded these files to this run. They are the user's own INPUT, not work\n"
    "this run produced. They stay available on every turn, so a file attached while\n"
    "another agent was answering is still context for you now.\n\n"
)
_FOOTER = "\n--- END FILES THE USER ATTACHED TO THIS RUN ---\n"

# Delimiters rather than markdown fences. An attached .md file very often CONTAINS
# ``` fences, which would close a fenced block early and let the rest of the document
# read as prose addressed to the agent.
_BEGIN = "--- BEGIN attached file: {name} ---\n"
_END = "\n--- END attached file: {name} ---\n\n"

# Announced whenever a file is shortened. `_NOTE_RESERVE` is the space held back per
# file for it: the arithmetic subtracts the RESERVE and the note actually emitted is
# never longer, so the total cannot exceed the cap by way of these notes.
_NOTE_TEMPLATE = "_(truncated: showing the first {shown:,} of {total:,} characters)_\n\n"
_NOTE_FALLBACK = "_(truncated: only part of this file is shown)_\n\n"
_NOTE_RESERVE = 120

_UNREADABLE_HEADING = "### Attached, but NOT readable\n"
_UNREADABLE_NOTE = (
    "You CANNOT open the file(s) listed above: this engine passes text only, so an\n"
    "image or an unsupported format never reaches you. Do not call a file tool on\n"
    "them, and do not claim to have looked at them. Tell the user you cannot read\n"
    "that file type and ask them to paste the relevant text, or to re-upload as\n"
    ".pdf, .docx, .txt, .md, .csv or .xlsx.\n\n"
)

_OMITTED_HEADING = "### Attached, but not included here\n"
_OMITTED_NOTE = (
    "These files were attached and could not fit in this turn's file budget. You have\n"
    "NOT been shown their content. Say so if they matter, rather than guessing from\n"
    "the names.\n\n"
)

# Backstop, so the cap holds on every path and not merely by intention. The arithmetic
# in `_plan` already keeps the block inside it; this fires only if a future edit breaks
# that, and it still says it truncated.
_HARD_TRIM_NOTE = "\n… [attachment context truncated to fit the size cap]\n"

# Smallest per-file share worth rendering. Below this a "file" is a heading, a
# delimiter and a sentence fragment — scaffolding that reads like content. Files that
# cannot be given this much are declared omitted instead.
_MIN_SHARE_CHARS = 500


def _read_one(name: str, path: str) -> tuple[str, str | None]:
    """One attachment's text, or `(name, None)` when it could not be read.

    `extraction_succeeded`, NOT truthiness. Both of `extract_file_text`'s failure modes
    return a non-empty human-readable sentence — "[Binary file: shot.png]", "[Error
    reading x: …]" — which is the right thing to show a person and the wrong thing to
    hand a model as though it were the document.
    """
    if not path:
        return name, None
    try:
        text = extract_file_text(path)
    except Exception:  # noqa: BLE001 — one bad file is reported, never fatal
        logger.warning("orchestrator2 could not extract text from %s", name, exc_info=True)
        return name, None
    if not extraction_succeeded(text):
        return name, None
    return name, text.strip()


def _read_all(refs: list) -> tuple[list[tuple[str, str]], list[str]]:
    """Every attachment, split into what could be read and what could not.

    Runs in a worker thread: PDF and .docx extraction is synchronous and can take
    seconds on a large file, and this is called on the socket's own event loop.
    """
    readable: list[tuple[str, str]] = []
    unreadable: list[str] = []
    for ref in refs:
        name = str((ref or {}).get("name") or "attachment")
        name, text = _read_one(name, str((ref or {}).get("path") or ""))
        if text:
            readable.append((name, text))
        else:
            unreadable.append(name)
    return readable, unreadable


def _scaffold_chars(
    kept: list[tuple[str, str]], unreadable: list[str], omitted: list[str]
) -> int:
    """Everything in the block that is not file content.

    Charged BEFORE the share is divided, and it includes `_NOTE_RESERVE` for every kept
    file whether or not that file truncates — which is what makes the total provably
    bounded rather than bounded on the paths that happen to be tested.
    """
    total = len(_HEADER) + len(_FOOTER)
    for name, _ in kept:
        total += len(_BEGIN.format(name=name)) + len(_END.format(name=name)) + _NOTE_RESERVE
    if unreadable:
        total += len(_UNREADABLE_HEADING) + len(_UNREADABLE_NOTE)
        total += sum(len(name) + 3 for name in unreadable)  # "- name\n"
    if omitted:
        total += len(_OMITTED_HEADING) + len(_OMITTED_NOTE)
        total += sum(len(name) + 3 for name in omitted)
    return total


def _plan(
    readable: list[tuple[str, str]], unreadable: list[str]
) -> tuple[list[tuple[str, str]], list[str], int]:
    """How many characters each readable file gets, and which are dropped entirely.

    The budget is split EQUALLY among the files present rather than spent
    first-come-first-served, so one 200KB upload cannot push every other attachment out
    of the block — the rule `context._render` uses, for the same reason.

    Unlike `context.py`, the count here is NOT bounded by nine: a user can attach many
    files, and past some number the equal share stops being enough to carry a sentence.
    Rather than emit a page of delimiters wrapped around fragments, the tail of the
    list is DROPPED and declared. Deterministic because `list_attachments` sorts by
    name; which files lose out is therefore stable across turns, though it is
    alphabetical rather than by importance — a real limit, and the reason the omitted
    section names them instead of staying quiet.
    """
    kept = list(readable)
    omitted: list[str] = []
    while kept:
        scaffold = _scaffold_chars(kept, unreadable, omitted)
        share = (MAX_ATTACHMENT_CONTEXT_CHARS - scaffold) // len(kept)
        if share >= _MIN_SHARE_CHARS:
            return kept, omitted, share
        omitted.insert(0, kept.pop()[0])
    return [], omitted, 0


def _render(
    kept: list[tuple[str, str]], unreadable: list[str], omitted: list[str], share: int
) -> str:
    """Assemble the block, keeping the total inside `MAX_ATTACHMENT_CONTEXT_CHARS`."""
    parts: list[str] = [_HEADER]

    for name, body in kept:
        parts.append(_BEGIN.format(name=name))
        if len(body) > share:
            note = _NOTE_TEMPLATE.format(shown=share, total=len(body))
            if len(note) > _NOTE_RESERVE:
                note = _NOTE_FALLBACK
            parts.append(_shorten(body, share))
            parts.append(_END.format(name=name))
            parts.append(note)
            logger.info(
                "orchestrator2 shortened attachment %s from %d to %d chars",
                name, len(body), share,
            )
        else:
            parts.append(body)
            parts.append(_END.format(name=name))

    if unreadable:
        parts.append(_UNREADABLE_HEADING)
        parts.extend(f"- {name}\n" for name in unreadable)
        parts.append(_UNREADABLE_NOTE)

    if omitted:
        parts.append(_OMITTED_HEADING)
        parts.extend(f"- {name}\n" for name in omitted)
        parts.append(_OMITTED_NOTE)

    parts.append(_FOOTER)

    block = "".join(parts)
    if len(block) > MAX_ATTACHMENT_CONTEXT_CHARS:
        logger.warning(
            "orchestrator2 attachment context exceeded its own cap (%d > %d) — "
            "hard-trimmed; the per-file share arithmetic is wrong",
            len(block), MAX_ATTACHMENT_CONTEXT_CHARS,
        )
        block = (
            block[: MAX_ATTACHMENT_CONTEXT_CHARS - len(_HARD_TRIM_NOTE)]
            + _HARD_TRIM_NOTE
        )
    return block


async def attachment_context(run_id: str, *, user_id: str) -> str:
    """Everything the user attached to this run, rendered for the answering agent.

    Returns `""` when the user attached nothing. `""` never means the read failed —
    that raises `AttachmentsUnavailableError`.

    `user_id` is KEYWORD-REQUIRED WITH NO DEFAULT, the same posture `dispatch.run_agent`
    takes with `project_id` and `context`. It is half of the storage key, so a default
    would let a call site silently read the wrong person's uploads or none at all, and
    an agent handed no attachments behaves exactly like an agent on a run where none
    were attached — the failure this module exists to prevent, reachable by forgetting
    an argument.

    IT IS THE TURN'S AUTHENTICATED USER, never a value off the wire. `attachment_store`
    keys uploads by uploader, so reading by run alone would hand one Project Admin's
    files to another's turn on a shared run — a scoping change wearing a convenience's
    clothes. The consequence is real and is not a bug: on a run driven by two people,
    each turn sees only its own driver's uploads.

    The result is at most `MAX_ATTACHMENT_CONTEXT_CHARS` characters, and any file
    shortened or dropped to fit says so in the text.
    """
    if not run_id or not user_id:
        # Well-defined, not a failure: no directory can be addressed, so nothing was
        # attached. Logged because the only way to get here is a caller bug — the same
        # line `context._load_run_artifacts` and `transcript.load_run_transcript` draw
        # for an unusable id.
        logger.warning(
            "orchestrator2 attachment read got an unusable id (run=%r user=%r)",
            run_id, user_id,
        )
        return ""

    try:
        refs = await asyncio.to_thread(
            attachment_store.list_attachments, str(user_id), str(run_id)
        )
    except Exception as exc:  # noqa: BLE001 — unknown != empty; see the class docstring
        logger.warning(
            "orchestrator2 could not list attachments for run=%s user=%s: %s — "
            "raising rather than reporting that nothing was attached",
            run_id, user_id, exc,
        )
        raise AttachmentsUnavailableError(
            "the files attached to this run could not be read"
        ) from exc

    if not refs:
        return ""

    readable, unreadable = await asyncio.to_thread(_read_all, list(refs))
    if not readable and not unreadable:
        return ""

    kept, omitted, share = _plan(readable, unreadable)
    return _render(kept, unreadable, omitted, share)
