"""An attachment that cannot be read must SAY so, not send the agent chasing a path.

From a live Requirements chat. The user attached
"Screenshot 2026-09-02 004837.png" and the agent replied:

    Error: local file not found

The upload had worked perfectly. What failed is subtler and worse than a missing file:
`extract_file_text` returns a readable PLACEHOLDER when it cannot parse something —
"[Binary file: shot.png]" — and the caller tested it with `if _txt and _txt.strip()`.
A placeholder is a non-empty string, so the screenshot was announced to the agent as
"the user attached this, use its content directly" with the content being the words
"[Binary file: shot.png]". The agent then did the reasonable thing and tried to open
the file itself, and `upload_file` answered "local file not found" about a path it had
guessed out of prose.

So the user saw a file-not-found error for a file that uploaded fine, about a file type
the agent could never have read anyway. Every part of that message was misleading.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── the sentinel ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "placeholder",
    [
        "[Binary file: Screenshot 2026-09-02 004837.png]",
        "[Binary file: diagram.svg]",
        "[Error reading notes.pdf: PdfReadError]",
    ],
)
def test_a_placeholder_is_not_content(placeholder):
    """The whole bug in one assertion: these are all truthy."""
    from shared.tools.document_tools import extraction_succeeded

    assert bool(placeholder.strip()) is True   # why `if text:` passed
    assert extraction_succeeded(placeholder) is False


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "   ", "\n\n", None])
def test_empty_extraction_is_not_content_either(text):
    from shared.tools.document_tools import extraction_succeeded

    assert extraction_succeeded(text) is False


@pytest.mark.unit
def test_real_text_is_content():
    from shared.tools.document_tools import extraction_succeeded

    assert extraction_succeeded("As a user, I want to log in so that ...") is True


@pytest.mark.unit
def test_text_merely_mentioning_a_binary_file_is_still_content():
    """The check is a PREFIX on the whole string, not a substring search — a document
    that happens to discuss binary files must not be discarded as unreadable."""
    from shared.tools.document_tools import extraction_succeeded

    assert extraction_succeeded("The build emits a [Binary file: app.exe] artifact.") is True


# ── extraction really does return a placeholder for an image ─────────────────


@pytest.mark.unit
def test_a_png_extracts_to_a_placeholder_not_to_text(tmp_path):
    """Pins the actual behaviour the callers now depend on, rather than assuming it."""
    from shared.tools.document_tools import extract_file_text, extraction_succeeded

    png = tmp_path / "Screenshot 2026-09-02 004837.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    out = extract_file_text(str(png))
    assert out.strip() != ""              # non-empty, which is what fooled the caller
    assert extraction_succeeded(out) is False


# ── both chat paths route it to a message that helps ─────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "module",
    [
        "agents_orchestrator.requirements_agent.requirements_agent_api",
        "agents_orchestrator.design_architecture_agent.design_architecture_agent_api",
    ],
)
def test_neither_chat_path_treats_a_placeholder_as_content(module):
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    assert "_extracted_ok(_txt)" in src, "still using truthiness on extracted text"
    assert "if _txt and _txt.strip():" not in src


@pytest.mark.unit
@pytest.mark.parametrize(
    "module",
    [
        "agents_orchestrator.requirements_agent.requirements_agent_api",
        "agents_orchestrator.design_architecture_agent.design_architecture_agent_api",
    ],
)
def test_an_unreadable_attachment_names_the_limit_instead_of_a_path(module):
    """The old hint was "please use the following files <path>", which pointed the agent
    at a file tool that cannot read an image either. The replacement has to (a) stop the
    agent opening it, (b) stop it pretending it looked, and (c) say what WOULD work."""
    import importlib
    import inspect

    import re

    src = inspect.getsource(importlib.import_module(module))
    # The message is written across several adjacent string literals, so compare on a
    # whitespace- and quote-normalised form rather than guessing at the line breaks.
    flat = re.sub(r'"\s*\n\s*"', "", src)
    flat = re.sub(r"\s+", " ", flat)

    assert "could not be read as text" in flat
    assert "do not call a file tool on it" in flat
    assert "do not claim to have looked at it" in flat
    # And it must offer the formats that actually work.
    for fmt in (".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"):
        assert fmt in src, f"{fmt} not offered as an alternative"
    # The old dead-end hint is gone.
    assert "please use the following files {', '.join(_unread)}" not in src
