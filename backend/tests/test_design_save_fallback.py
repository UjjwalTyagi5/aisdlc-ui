"""Saving a generated architecture must find the content it just generated.

From a live session: the user watched the Design agent produce a full architecture
document, asked "yes save this as a pdf", and got

    Error: nothing to save. Generate the architecture first
    (generate_architecture_from_context), or pass the content explicitly.

The document was right there on screen. The save tools I added fell back to
`shared.output_file` when the model passed no `content` — and output_file holds a
FILENAME (`os.path.basename(docx_path)`), not the document. The correct attribute is
`shared.last_architecture`, which generate_architecture_from_context stashes for
exactly this reason: a weaker model often cannot echo a large document back into a
tool argument. `save_architecture` already used it; the tools I added copied the
wrong one.

The failure was maximally confusing: an error telling the user to generate the
architecture first, immediately after they had.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DOC = "# URL Shortener\n\n## HLD\n\nSome prose.\n\n| Component | Value |\n|---|---|\n| DB | Postgres |\n"


@pytest.fixture
def design(tmp_path):
    """The design agent with a session context and a generated architecture."""
    from agents_orchestrator.design_architecture_agent.agents import architecture as a
    from agents_orchestrator.design_architecture_agent.config import shared

    original_dir = a._FILES_DIR
    a._FILES_DIR = str(tmp_path)
    # output_file holds a FILENAME in the real code — set it to prove the tools do not
    # read it, since that is the bug.
    shared.output_file = "architecture.docx"
    shared.last_architecture = DOC
    try:
        with patch.object(a, "get_user_id", lambda: "u1"), \
                patch.object(a, "get_session_id", lambda: "s1"), \
                patch.object(a, "_design_broadcast_file", AsyncMock(return_value="http://x/f")):
            yield a, tmp_path / "u1" / "orchestrator" / "s1" / "output"
    finally:
        a._FILES_DIR = original_dir
        shared.last_architecture = ""
        shared.output_file = ""


@pytest.mark.unit
async def test_saving_a_pdf_with_no_content_uses_the_generated_architecture(design):
    a, out_dir = design
    result = await a.save_architecture_pdf.ainvoke({})

    assert "nothing to save" not in result
    written = out_dir / "architecture.pdf"
    assert written.exists() and written.stat().st_size > 0
    with open(written, "rb") as fh:
        assert fh.read(5) == b"%PDF-"


@pytest.mark.unit
async def test_export_document_with_no_content_does_the_same(design):
    a, out_dir = design
    result = await a.export_document.ainvoke({"filename": "arch.xlsx"})

    assert "nothing to export" not in result
    assert (out_dir / "arch.xlsx").stat().st_size > 0


@pytest.mark.unit
async def test_the_filename_attribute_is_not_mistaken_for_content(design):
    """THE BUG. output_file is "architecture.docx" — a filename. Rendering it would
    produce a one-line document containing the word "architecture.docx"."""
    from agents_orchestrator.design_architecture_agent.config import shared

    a, out_dir = design
    shared.last_architecture = ""          # only the filename is available
    result = await a.save_architecture_pdf.ainvoke({})

    # With no real content it must REFUSE, not render the filename as a document.
    assert "nothing to save" in result
    assert not (out_dir / "architecture.pdf").exists()


@pytest.mark.unit
async def test_explicit_content_still_wins(design):
    a, out_dir = design
    await a.save_architecture_pdf.ainvoke(
        {"content": "# Explicit\n\nPassed directly.", "filename": "explicit.pdf"}
    )
    assert (out_dir / "explicit.pdf").stat().st_size > 0


@pytest.mark.unit
def test_neither_save_tool_reads_output_file():
    """A source guard: output_file is the wrong attribute and looks plausible, so the
    next person editing these tools could reach for it again."""
    import inspect

    from agents_orchestrator.design_architecture_agent.agents import architecture

    src = inspect.getsource(architecture)
    # The only legitimate uses are setting it and reading the URL companion.
    for line in src.splitlines():
        if 'getattr(shared, "output_file"' in line:
            pytest.fail(f"output_file read as content: {line.strip()}")


@pytest.mark.unit
def test_the_save_tools_use_the_same_source_as_save_architecture():
    """save_architecture had the right fallback all along; the new tools must match it
    rather than inventing a second answer to the same question."""
    import inspect

    from agents_orchestrator.design_architecture_agent.agents import architecture

    src = inspect.getsource(architecture)
    assert src.count('getattr(shared, "last_architecture", "")') >= 3, (
        "save_architecture, save_architecture_pdf and export_document should all "
        "fall back to last_architecture"
    )
