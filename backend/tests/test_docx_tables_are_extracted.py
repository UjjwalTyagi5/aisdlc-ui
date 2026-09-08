"""A .docx's tables are part of its text.

`extract_file_text` read `doc.paragraphs`, which in python-docx is only the body's
top-level paragraphs — table cells are not among them. So every table in an attached
document was silently dropped.

That is not an edge case for the documents this platform is given. A BRD or PRD puts its
requirements in a table almost by convention: the functional requirements with their
IDs, the non-functional ones, the data model, the risk register, the glossary. What
reached the agent was the narrative prose with every requirement removed — and nothing
said so, because prose extracts fine and `extraction_succeeded` was true. The agent then
invented requirements that were never in the document, which reads as a hallucinating
model rather than a lossy reader.

Found by extracting a real generated BRD and counting: 5,444 characters out of ~12,000,
with FR-01 through FR-16, NFR-01 through NFR-09, the data fields, the risks and the
glossary all absent.

This is the shared utility — `requirements_agent`, `design_architecture_agent`,
`documentation_agent` and `orchestrator2/attachments.py` all read through it.
"""
from __future__ import annotations

import pytest

from shared.tools.document_tools import extract_file_text, extraction_succeeded

docx = pytest.importorskip("docx")


@pytest.fixture
def brd(tmp_path):
    """A document shaped like the ones this platform is actually given: prose, then the
    requirements in a table."""
    doc = docx.Document()
    doc.add_heading("Business Requirements Document", level=1)
    doc.add_paragraph("QuickLink turns a long URL into a short one and counts the clicks.")

    doc.add_heading("Functional Requirements", level=2)
    table = doc.add_table(rows=1, cols=3)
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Ref", "Requirement", "Priority"
    for ref, req, pri in [
        ("FR-01", "A user can submit a long URL and receive a short link.", "Must"),
        ("FR-02", "The system generates a unique 6-8 character short code.", "Must"),
        ("FR-03", "A user may supply a custom alias instead.", "Should"),
    ]:
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = ref, req, pri

    doc.add_paragraph("Out of scope: single sign-on.")

    path = tmp_path / "brd.docx"
    doc.save(str(path))
    return str(path)


def test_the_requirements_in_the_table_are_extracted(brd):
    """THE BUG. Every one of these lived only in a table cell."""
    text = extract_file_text(brd)

    assert "FR-01" in text
    assert "FR-02" in text
    assert "FR-03" in text
    assert "A user can submit a long URL and receive a short link." in text


def test_the_header_row_is_extracted_too(brd):
    """Without "Ref | Requirement | Priority" the rows are three unlabelled columns, and
    "Must" reads as part of the requirement rather than its priority."""
    text = extract_file_text(brd)

    assert "Requirement" in text
    assert "Priority" in text
    assert "Must" in text


def test_the_prose_still_comes_through(brd):
    """The paragraphs were never broken; this pins that adding tables did not cost them."""
    text = extract_file_text(brd)

    assert "Business Requirements Document" in text
    assert "QuickLink turns a long URL into a short one and counts the clicks." in text
    assert "Out of scope: single sign-on." in text


def test_the_document_reads_in_its_own_order(brd):
    """A table is where the author put it. Appending every table after all the prose
    would file the functional requirements under "Out of scope"."""
    text = extract_file_text(brd)

    assert text.index("Functional Requirements") < text.index("FR-01")
    assert text.index("FR-03") < text.index("Out of scope: single sign-on.")


def test_a_row_stays_on_one_line(brd):
    """A requirement and its ID belong together. One cell per line would leave "FR-01"
    and its text as separate, unrelated lines."""
    text = extract_file_text(brd)

    line = next(ln for ln in text.splitlines() if "FR-01" in ln)
    assert "A user can submit a long URL and receive a short link." in line
    assert "Must" in line


def test_extraction_still_reports_success(brd):
    assert extraction_succeeded(extract_file_text(brd))


def test_a_document_of_nothing_but_tables_is_not_empty(tmp_path):
    """A data dictionary or a traceability matrix is often exactly this, and it used to
    extract to the empty string — which `extraction_succeeded` calls a success."""
    doc = docx.Document()
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Field"
    table.rows[0].cells[1].text = "Type"
    table.rows[1].cells[0].text = "short_code"
    table.rows[1].cells[1].text = "Text"
    path = tmp_path / "data.docx"
    doc.save(str(path))

    text = extract_file_text(str(path))

    assert "short_code" in text
    assert "Type" in text


def test_an_empty_row_does_not_become_a_line_of_separators(tmp_path):
    """A spacer row is layout, not content. `" | | "` in the middle of a requirements
    list is noise the model has to interpret."""
    doc = docx.Document()
    table = doc.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = "FR-01"
    path = tmp_path / "spacer.docx"
    doc.save(str(path))

    text = extract_file_text(str(path))

    assert "FR-01" in text
    assert not any(set(ln.strip()) <= {"|", " "} and ln.strip() for ln in text.splitlines())


def test_a_cell_holding_a_paragraph_break_stays_on_its_row(tmp_path):
    """Authors press Enter inside a cell. Left alone, that newline splits the row and
    the tail of the requirement becomes an orphan line."""
    doc = docx.Document()
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "FR-09"
    cell = table.rows[0].cells[1]
    cell.text = "The user can filter by campaign."
    cell.add_paragraph("Deferred from Release 1.")
    path = tmp_path / "multiline.docx"
    doc.save(str(path))

    text = extract_file_text(str(path))

    line = next(ln for ln in text.splitlines() if "FR-09" in ln)
    assert "The user can filter by campaign." in line
    assert "Deferred from Release 1." in line


def test_an_unreadable_file_still_reports_the_failure(tmp_path):
    """The error path is what `extraction_succeeded` keys on, and it must survive."""
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a docx at all")

    text = extract_file_text(str(path))

    assert not extraction_succeeded(text)
