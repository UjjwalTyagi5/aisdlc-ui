"""Files the user attaches reach the agent that answers the turn.

WHY THIS EXISTS. The Orchestrator's composer had a textarea and a Send button and
nothing else, so the only way to give an agent a BRD was to paste it. The standalone
agents have had an attach control — and server-side extraction behind it — since
`shared/tools/document_tools.py` was written.

The failure this file is written against is NOT "the button is missing". It is the one
that looks like success: a file that uploads, appears as a chip, and is never read. The
user then believes the agent saw the document and reads its answer as informed. That is
strictly worse than no attach button at all, so every test below is about the CONTENT
reaching the prompt, and about the block being honest when it could not.

Three decisions are pinned here rather than left to the implementation:

  1. WHICH TURN a file belongs to — every turn on the run, not the one it was attached
     to (`test_a_file_attached_earlier_is_still_offered_to_a_later_turn`). This engine
     has no agent order: `context.py` includes every deliverable whatever produced it,
     for exactly this reason, and a BRD attached while Requirements was answering is
     precisely what Design needs three turns later. The transcript carries the WORDS of
     a turn, never the file's content, so a file scoped to its own turn would be
     unrecoverable afterwards.

  2. WHAT CANNOT BE READ is named and declared unreadable, never silently stored
     (`test_an_image_is_named_and_declared_unreadable`). There is no vision path in
     this engine — `dispatch.run_agent` builds text-only messages — so an image is a
     file the agent genuinely cannot see, and saying so is the only honest option.

  3. THE BUDGET is this block's own, and a file that exceeds it is truncated WITH A
     MARKER rather than refused at upload
     (`test_an_oversized_attachment_is_truncated_and_says_so`). A silently shortened
     document is `context.py`'s defect 3 in another costume.

THE EXTRACTION IS EXERCISED FOR REAL. `test_a_real_docx_is_extracted_through_the_real_
extractor` builds an actual .docx with python-docx and reads it back through the real
`extract_file_text`. Two defects on this branch got through green suites because every
test used a dict or a monkeypatched function in place of the real object; a fake
extractor here would pass whether or not python-docx is installed, which is the whole
question that test is asking.
"""
from __future__ import annotations

import pytest

from agents_orchestrator.orchestrator2 import attachments
from shared.services import attachment_store

_RUN = "8f1d6c4e-2b7a-4a1e-9c33-0d5a1f2e7b40"
_USER = "af57932d-f4f2-4673-aef7-d33b75c022f4"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The REAL attachment store, rooted in a temp directory.

    The store itself is not faked: `save_attachment` does the validation, the path
    scheme and the write, exactly as the upload route calls it. Only the root moves,
    so the test does not scribble into `backend/files`.
    """
    monkeypatch.setattr(attachment_store, "_FILES_ROOT", tmp_path / "files")
    return attachment_store


def _attach(store, name: str, data: bytes) -> None:
    store.save_attachment(_USER, _RUN, name, data)


# ── the point of the feature ────────────────────────────────────────────────


async def test_an_attached_document_reaches_the_agents_context(store):
    """The content, not merely the file name.

    A block naming `brd.md` without its text is the exact failure this feature is
    written against: the user sees the chip and believes the agent read it.
    """
    _attach(store, "brd.md", b"# BRD\n\nThe checkout flow must support Apple Pay.")
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "brd.md" in out
    assert "Apple Pay" in out, "the file's CONTENT must reach the prompt, not just its name"


async def test_a_run_with_no_attachments_yields_nothing(store):
    """`""` has to keep meaning "nothing was attached", or a caller cannot tell an
    empty run from a failed read — see the raising test below."""
    assert await attachments.attachment_context(_RUN, user_id=_USER) == ""


async def test_a_file_attached_earlier_is_still_offered_to_a_later_turn(store):
    """DECISION 1. Attachments are RUN-scoped, not turn-scoped.

    There is no agent order in this engine, so a document attached while one agent was
    answering is exactly what the next one needs. Nothing here filters by when a file
    arrived, and this test fails if a future edit adds such a filter.
    """
    _attach(store, "first.md", b"the earliest requirement")
    _attach(store, "second.md", b"the latest requirement")
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "the earliest requirement" in out
    assert "the latest requirement" in out


# ── what cannot be read ─────────────────────────────────────────────────────


async def test_an_image_is_named_and_declared_unreadable(store):
    """DECISION 2. There is no vision path, so an image is named and disclaimed.

    The two things that must NOT happen: the file vanishing (the user believes it was
    read), and `extract_file_text`'s placeholder being passed off as content. That
    placeholder — "[Binary file: shot.png]" — is a readable sentence, which is why
    `extraction_succeeded` exists and why a truthiness check is not good enough.
    """
    _attach(store, "shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    out = await attachments.attachment_context(_RUN, user_id=_USER)

    assert "shot.png" in out, "a file the agent cannot read must still be named"
    assert "[Binary file:" not in out, (
        "the extractor's placeholder must never be rendered as though it were the "
        "file's content"
    )
    assert "cannot" in out.lower(), (
        "the block must SAY the file could not be read; an unexplained file name "
        "invites the agent to claim it looked at it"
    )


async def test_an_unreadable_file_does_not_suppress_the_readable_ones(store):
    """One image must not cost the user the BRD they attached alongside it."""
    _attach(store, "brd.md", b"# BRD\n\nApple Pay is required.")
    _attach(store, "shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "Apple Pay" in out
    assert "shot.png" in out


async def test_a_real_docx_is_extracted_through_the_real_extractor(store):
    """FAKES LIE. A real .docx, built by python-docx, read back by the real extractor.

    Every other test here could pass with a monkeypatched extractor that returns a
    string. This one cannot: it asks whether the binary path this feature promises for
    .doc/.docx actually works in this environment.
    """
    import io

    from docx import Document

    document = Document()
    document.add_paragraph("The vendor must support SSO via SAML 2.0.")
    buffer = io.BytesIO()
    document.save(buffer)

    _attach(store, "spec.docx", buffer.getvalue())
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "SAML 2.0" in out, "a real .docx must be extracted, not reported as binary"


# ── a failed read is not an empty run ───────────────────────────────────────


async def test_a_failed_listing_raises_rather_than_reporting_no_attachments(
    store, monkeypatch
):
    """The posture `context.ContextUnavailableError` establishes, kept here.

    A disk error that returned `""` would tell the agent "the user attached nothing",
    and the agent would then ask for a document the user had already given it — a
    failure disguised as normal operation, which is the bug class this engine exists
    to remove.
    """
    def _boom(*_args, **_kwargs):
        raise OSError("the attachments directory could not be read")

    monkeypatch.setattr(attachment_store, "list_attachments", _boom)
    with pytest.raises(attachments.AttachmentsUnavailableError):
        await attachments.attachment_context(_RUN, user_id=_USER)


async def test_a_file_that_disappears_between_listing_and_reading_is_reported(
    store, monkeypatch
):
    """An extraction that fails is an unreadable file, not an absent one.

    Distinct from the test above: the LISTING succeeded, so what the run holds is
    known. One file of several failing must not raise away the rest — it must be
    named as unreadable, the same as an image.
    """
    _attach(store, "brd.md", b"# BRD\n\nApple Pay is required.")
    _attach(store, "gone.md", b"deleted before it could be read")

    real = attachments.extract_file_text

    def _fail_one(path: str) -> str:
        if path.endswith("gone.md"):
            raise OSError("file vanished")
        return real(path)

    monkeypatch.setattr(attachments, "extract_file_text", _fail_one)
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "Apple Pay" in out, "one failed file must not cost the others"
    assert "gone.md" in out, "a file that could not be read must still be named"


# ── the budget ──────────────────────────────────────────────────────────────


async def test_an_oversized_attachment_is_truncated_and_says_so(store):
    """DECISION 3. Truncated with a marker — never silently, never refused.

    Refusing at upload would mean a 300-page PDF cannot be attached at all; the 10 MB
    store limit is already the hard refusal. Truncating lets the agent use most of the
    document, and the marker is what stops it answering confidently from a document it
    only half received.
    """
    oversized = b"A" * (attachments.MAX_ATTACHMENT_CONTEXT_CHARS * 2)
    _attach(store, "huge.md", oversized)
    out = await attachments.attachment_context(_RUN, user_id=_USER)

    assert len(out) <= attachments.MAX_ATTACHMENT_CONTEXT_CHARS
    assert "truncat" in out.lower(), (
        "a shortened document must say it was shortened — a silent cut is how an "
        "agent answers confidently from half a file"
    )


async def test_many_large_attachments_stay_inside_the_block_budget(store):
    """The cap is on the WHOLE block, not per file.

    `document_tools.attachment_message_contents` caps each file at 20,000 characters
    with no total, so ten attachments are 200,000. This block has its own bound and
    divides it, so one oversized upload cannot starve the others out of the prompt.
    """
    for i in range(8):
        _attach(store, f"doc{i}.md", b"B" * 40_000)
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert len(out) <= attachments.MAX_ATTACHMENT_CONTEXT_CHARS
    assert attachments._HARD_TRIM_NOTE not in out, (
        "the SHARE ARITHMETIC must keep the block in budget. The hard trim is a "
        "backstop for an arithmetic bug, and a test that passes only because the "
        "backstop fired is not testing the arithmetic at all"
    )


async def test_the_budget_is_shared_so_one_huge_file_cannot_starve_the_others(store):
    """Equal shares, like `context._render`, and for the same reason: a 200KB payload
    from one file must not push every other file out of the block.

    THE SMALL FILE SORTS LAST on purpose. `list_attachments` returns names in
    alphabetical order, so a budget spent first-come-first-served gives the whole
    allowance to `a_huge_1.md` and the hard trim then cuts everything after it —
    including a file small enough to have cost almost nothing. Found by mutation:
    with one huge file and one small one the two behaved the same, because the trim
    happened to land after the small file's only sentence.
    """
    for i in range(3):
        _attach(store, f"a_huge_{i}.md", b"H" * 200_000)
    _attach(store, "z_small.md", b"the small file's only sentence")
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "the small file's only sentence" in out, (
        "a small attachment must survive alongside huge ones that sort before it"
    )


async def test_the_head_and_the_tail_of_a_long_document_both_survive(store):
    """Truncation keeps the START and the END, as `context._shorten` does.

    The reasoning is `context.py`'s and applies unchanged: requirements, acceptance
    criteria and decisions live at the END of a specification, so head-only truncation
    hands the agent an introduction and invites it to infer the rest.
    """
    body = b"OPENING-MARKER\n" + b"x" * 200_000 + b"\nCLOSING-MARKER"
    _attach(store, "long.md", body)
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "OPENING-MARKER" in out
    assert "CLOSING-MARKER" in out, (
        "the end of a specification carries its decisions and must survive truncation"
    )


# ── provenance ──────────────────────────────────────────────────────────────


async def test_attachments_are_labelled_as_the_users_input_not_the_runs_output(store):
    """An attachment must not read as something this run produced.

    `context.handoff_context` renders deliverables under "WORK ALREADY ON THIS RUN".
    A user-supplied file rendered the same way would have the agent citing the user's
    draft back to them as an agent's completed artifact.
    """
    _attach(store, "brd.md", b"# BRD\n\nApple Pay is required.")
    out = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "attach" in out.lower(), "the block must say these files were attached"
    assert "WORK ALREADY ON THIS RUN" not in out, (
        "an attachment is the user's input, never the run's own output"
    )


async def test_one_users_attachments_do_not_leak_into_anothers_turn(store):
    """The store is keyed by (user, run) and this read must keep that key.

    Reading by run alone would hand one Project Admin's uploads to another's turn on
    a shared run — a scoping change disguised as a convenience.
    """
    _attach(store, "mine.md", b"belongs to the uploader")
    out = await attachments.attachment_context(_RUN, user_id="00000000-0000-0000-0000-000000000000")
    assert out == "", "another user's attachments must not be read into this turn"
