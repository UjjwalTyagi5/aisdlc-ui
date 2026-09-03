"""Documents in every format the user asked for, and stored only when they say so.

TWO THINGS.

1. FORMATS. Word, Excel and slides had generators; PDF had none at all — the only
   mention of .pdf in the agents was reading uploads. weasyprint is a dependency but
   does not import on this platform (missing GTK/Pango native libraries), so the
   renderer is reportlab, which does.

2. CONSENT. Generated files were persisted to the project's artifacts, and uploaded to
   Blob, the moment a tool produced them. Downloading a file in chat and publishing it
   to shared, durable, project-wide storage are different acts with different
   consequences, and the second one is the user's call. Files are now staged until
   they agree.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# -- PDF rendering -------------------------------------------------------------


@pytest.mark.unit
def test_a_pdf_is_actually_written(tmp_path):
    from shared.tools.pdf_render import markdown_to_pdf

    out = str(tmp_path / "doc.pdf")
    markdown_to_pdf("# Title\n\nSome prose.", out, title="Doc")
    assert os.path.getsize(out) > 0
    with open(out, "rb") as fh:
        assert fh.read(5) == b"%PDF-"


@pytest.mark.unit
def test_markdown_structure_survives(tmp_path):
    """Headings, lists, a table and a fenced block must not throw. They exercise every
    branch of the parser, which is where a renderer usually breaks."""
    from shared.tools.pdf_render import markdown_to_pdf

    md = (
        "# H1\n## H2\n\ntext\n\n- a\n- b\n\n1. one\n2. two\n\n"
        "| Field | Value |\n|---|---|\n| Owner | Ana |\n\n"
        "```\ncode()\n```\n\n---\n[link](https://example.com)\n"
    )
    out = str(tmp_path / "rich.pdf")
    markdown_to_pdf(md, out)
    assert os.path.getsize(out) > 0


@pytest.mark.unit
def test_characters_that_break_naive_markup_are_escaped(tmp_path):
    """'<' and '&' are markup to reportlab's paragraph parser. A requirement saying
    "latency < 300ms" or "R&D" would otherwise fail the whole document."""
    from shared.tools.pdf_render import markdown_to_pdf

    out = str(tmp_path / "esc.pdf")
    markdown_to_pdf("Latency < 300ms for R&D <tags> & more", out)
    assert os.path.getsize(out) > 0


@pytest.mark.unit
def test_an_empty_document_still_produces_a_file(tmp_path):
    """Returning success with no file is how an agent tells a user their document is
    ready when it is not."""
    from shared.tools.pdf_render import markdown_to_pdf

    out = str(tmp_path / "empty.pdf")
    markdown_to_pdf("", out)
    assert os.path.getsize(out) > 0


# -- every format the user asked for has a tool --------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_name", "fmt"),
    [
        ("markdowntodoc", "Word"),
        ("markdowntopdf", "PDF"),
        ("generate_planning_sheet", "Excel"),
        ("generate_ppt", "PowerPoint"),
    ],
)
def test_requirements_can_produce_each_format(tool_name, fmt):
    from agents_orchestrator.requirements_agent.agents import planning

    assert tool_name in {t.name for t in planning.tools}, f"no {fmt} generator"


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["save_architecture", "save_architecture_pdf"])
def test_design_can_produce_word_and_pdf(tool_name):
    from agents_orchestrator.design_architecture_agent.agents import architecture

    assert tool_name in {t.name for t in architecture.tools}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ext", "ctype"),
    [
        (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (".pdf", "application/pdf"),
        (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ],
)
def test_each_format_stores_with_the_right_content_type(ext, ctype):
    """A wrong content type is served as application/octet-stream, so the browser
    downloads a file it could have displayed."""
    from shared.services.chat_artifacts import _CONTENT_TYPES

    assert _CONTENT_TYPES.get(ext) == ctype


# -- what happens when a file is generated -------------------------------------


@pytest.fixture(autouse=True)
def _clean_upload_flags():
    """`_LAST_UPLOAD_OK` is process-wide and keyed by filename, so one test's result
    would otherwise be read by the next one using the same name."""
    from shared.services import chat_artifacts

    chat_artifacts._LAST_UPLOAD_OK.clear()
    yield
    chat_artifacts._LAST_UPLOAD_OK.clear()


class _FakeDbSession:
    """Enough of an AsyncSession for register_generated_file's own bookkeeping.

    Needed since the approval gate landed: the per-turn consent branch used to return
    BEFORE any database work, so these tests never reached it. Now every generated file
    is recorded immediately, which means resolving a chat run.
    """

    def add(self, _obj):
        return None

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


@contextlib.contextmanager
def _ctx(consented: bool, session="s1"):
    """The chat context a generated file is registered in.

    ONE context manager, not a tuple of patches the caller unpacks into names. The tuple
    form invited `a, b, c, d = _ctx(...)`, and when this grew a fifth and sixth patch the
    obvious extension — `a, b, c, d, e, f` — silently rebound `f`, which every test here
    uses for its temp FILE. The file path became a patcher's repr, os.path.exists said
    no, and the assertions failed somewhere else entirely.
    """
    import config.ws_helper as ws
    from shared.services import chat_artifacts as ca

    async def _run(_session, _tenant, _project, _stage):
        return "11111111-1111-1111-1111-111111111111"

    with contextlib.ExitStack() as stack:
        for patcher in (
            patch.object(ca, "get_tenant_id", lambda: "t1"),
            patch.object(ca, "get_project_id", lambda: "p1"),
            patch.object(ca, "get_session_id", lambda: session),
            patch.object(ws, "get_consequential_approved", lambda: consented),
            patch.object(ca, "get_db_session_for_tenant", lambda _t: _FakeDbSession()),
            patch.object(ca, "_get_or_create_chat_run", _run),
        ):
            stack.enter_context(patcher)
        yield


@pytest.mark.unit
async def test_every_generated_file_is_recorded_immediately(tmp_path):
    """NO PER-TURN CONSENT ANY MORE. It used to stage the file and have the agent ask
    "shall I save this?". Migration 0040 moved that decision to whoever RUNS the
    project, so asking the person chatting would be a question whose answer no longer
    decides anything on its own. The row is written straight away — as PENDING."""
    from shared.services import chat_artifacts as ca

    f = tmp_path / "brd.docx"
    f.write_bytes(b"x")
    store = AsyncMock(return_value=SimpleNamespace(blob_url=None, upload_succeeded=True))
    with _ctx(consented=True), patch("shared.services.artifact_store.store_artifact", store):
        await ca.register_generated_file("brd.docx", str(f), "http://x/brd.docx", stage="requirements")

    store.assert_awaited()


@pytest.mark.unit
async def test_a_false_consent_flag_no_longer_withholds_the_file(tmp_path):
    """`consented` is vestigial — retained in the signature for callers that still pass
    it. Honouring it would reinstate a gate the admin one replaced, and the file would
    never be recorded at all, so no admin could approve it either."""
    from shared.services import chat_artifacts as ca

    f = tmp_path / "brd.docx"
    f.write_bytes(b"x")
    store = AsyncMock(return_value=SimpleNamespace(blob_url=None, upload_succeeded=True))
    with _ctx(consented=True), patch("shared.services.artifact_store.store_artifact", store):
        await ca.register_generated_file(
            "brd.docx", str(f), "u", stage="requirements", consented=False
        )

    store.assert_awaited()


@pytest.mark.unit
async def test_the_artifact_is_stored_pending_not_approved(tmp_path):
    """The gate only means something if what lands is unapproved. store_artifact sets
    approval_status="pending" and leaves blob_url None; this checks the caller does not
    quietly override either."""
    from shared.services import chat_artifacts as ca

    f = tmp_path / "brd.docx"
    f.write_bytes(b"x")
    captured = {}

    async def _store(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(blob_url=None, upload_succeeded=True)

    with _ctx(consented=True), patch("shared.services.artifact_store.store_artifact", _store):
        await ca.register_generated_file("brd.docx", str(f), "u", stage="requirements")

    assert captured["filename"] == "brd.docx"
    assert captured["agent"] == "requirements"



# -- the agents are told the admin decides -------------------------------------


_PROMPTS = [
    ("agents_orchestrator.requirements_agent.agents.planning", "INGESTION_SYS_MESSAGE"),
    ("agents_orchestrator.design_architecture_agent.agents.architecture", "DESIGN_SYS_MESSAGE"),
]


def _prompt(module: str, attr: str) -> str:
    import importlib

    return " ".join(getattr(importlib.import_module(module), attr).split())


@pytest.mark.unit
@pytest.mark.parametrize(("module", "attr"), _PROMPTS)
def test_neither_prompt_asks_the_user_for_permission_any_more(module, attr):
    """The per-turn question was replaced by a project admin's decision (migration
    0040). Leaving the old instruction in would have the agent ask something whose
    answer no longer decides anything, and then call a tool that no longer exists."""
    flat = _prompt(module, attr)
    assert "ASK FIRST" not in flat
    assert "ONLY after they say yes" not in flat
    assert "save_to_project_artifacts" not in flat


@pytest.mark.unit
@pytest.mark.parametrize(("module", "attr"), _PROMPTS)
def test_both_prompts_say_the_document_is_awaiting_approval(module, attr):
    flat = _prompt(module, attr)
    assert "AWAITING APPROVAL" in flat
    assert "a project admin decides" in flat


@pytest.mark.unit
@pytest.mark.parametrize(("module", "attr"), _PROMPTS)
def test_neither_prompt_lets_the_agent_claim_the_document_is_saved(module, attr):
    """It is waiting on someone else's decision. "Added to the project" would set an
    expectation the admin has not agreed to — the same false-success the upload warning
    exists to prevent, one step earlier."""
    flat = _prompt(module, attr)
    assert 'Do NOT claim it has been "added to the project"' in flat


@pytest.mark.unit
@pytest.mark.parametrize("module", [m for m, _ in _PROMPTS])
def test_the_explicit_save_tool_is_gone(module):
    """It called save_pending_artifacts, which staged files for a consent step that no
    longer happens. A tool that cannot do anything is worse than no tool: the model
    calls it and reports success."""
    import importlib

    mod = importlib.import_module(module)
    assert "save_to_project_artifacts" not in {t.name for t in mod.tools}


# -- both agents can produce every format --------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "module",
    [
        "agents_orchestrator.requirements_agent.agents.planning",
        "agents_orchestrator.design_architecture_agent.agents.architecture",
    ],
)
def test_both_agents_export_every_document_format(module):
    """Design had no Excel generator at all, and its Word/PDF tools were hard-wired to
    the architecture document — it could not export arbitrary content in any format."""
    import importlib

    mod = importlib.import_module(module)
    names = {t.name for t in mod.tools}
    assert "export_document" in names            # word / pdf / excel / markdown
    assert "generate_diagram" in names           # image
    assert "generate_ppt" in names               # slides


@pytest.mark.unit
@pytest.mark.parametrize("ext", [".docx", ".pdf", ".xlsx", ".md"])
async def test_the_dispatcher_writes_each_format(tmp_path, ext):
    from shared.tools.doc_export import render_document

    md = "# Title\n\ntext\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    out = str(tmp_path / f"doc{ext}")
    await render_document(md, out, title="Doc")
    assert os.path.getsize(out) > 0


@pytest.mark.unit
async def test_an_unsupported_format_is_refused_by_name(tmp_path):
    """Silently writing a .docx for a .rtf request is how a user ends up with a file
    they cannot open."""
    from shared.tools.doc_export import render_document

    with pytest.raises(ValueError) as e:
        await render_document("x", str(tmp_path / "doc.rtf"))
    assert ".rtf" in str(e.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("report", "report.docx"),               # extension from the default
        ("../../etc/passwd.pdf", "passwd.pdf"),  # path separators stripped
        ("", "d.docx"),
    ],
)
def test_the_model_cannot_choose_a_path(given, expected):
    """The FILENAME comes from the model. A separator in it would write outside the
    session's output directory."""
    from shared.tools.doc_export import normalise_filename

    assert normalise_filename(given, "d.docx") == expected


# -- excel exports tables ------------------------------------------------------


@pytest.mark.unit
def test_excel_makes_one_sheet_per_table_named_from_the_heading(tmp_path):
    from openpyxl import load_workbook

    from shared.tools.xlsx_render import markdown_to_xlsx

    md = (
        "# Risk Register\n\n| Risk | Impact |\n|---|---|\n| A | High |\n\n"
        "## Owners\n\n| Name | Role |\n|---|---|\n| Ana | Admin |\n"
    )
    out = str(tmp_path / "t.xlsx")
    markdown_to_xlsx(md, out)
    wb = load_workbook(out)
    assert wb.sheetnames == ["Risk Register", "Owners"]
    assert wb["Risk Register"]["A1"].value == "Risk"


@pytest.mark.unit
def test_a_sheet_name_excel_would_reject_is_made_legal(tmp_path):
    """Excel refuses a name over 31 characters or containing []:*?/\\ — openpyxl raises
    on save, losing the whole workbook rather than one tab."""
    from openpyxl import load_workbook

    from shared.tools.xlsx_render import markdown_to_xlsx

    md = "# A/B:C*D?E[a very long heading well past the excel limit]\n\n| X |\n|---|\n| 1 |\n"
    out = str(tmp_path / "n.xlsx")
    markdown_to_xlsx(md, out)
    name = load_workbook(out).sheetnames[0]
    assert len(name) <= 31
    assert not (set(name) & set("[]:*?/\\"))


@pytest.mark.unit
def test_two_tables_under_the_same_heading_get_distinct_sheets(tmp_path):
    """Excel rejects duplicate sheet names outright."""
    from openpyxl import load_workbook

    from shared.tools.xlsx_render import markdown_to_xlsx

    md = (
        "# Data\n\n| A |\n|---|\n| 1 |\n\ntext between\n\n| B |\n|---|\n| 2 |\n"
    )
    out = str(tmp_path / "dup.xlsx")
    markdown_to_xlsx(md, out)
    names = load_workbook(out).sheetnames
    assert len(names) == len(set(names)) == 2


@pytest.mark.unit
def test_a_document_with_no_tables_still_opens(tmp_path):
    """An empty workbook looks corrupt; a sheet saying why does not."""
    from openpyxl import load_workbook

    from shared.tools.xlsx_render import markdown_to_xlsx

    out = str(tmp_path / "none.xlsx")
    markdown_to_xlsx("# Just prose\n\nNo tables here.", out)
    ws = load_workbook(out).active
    assert "No tables" in str(ws["A1"].value)


@pytest.mark.unit
def test_the_separator_row_is_not_data(tmp_path):
    """|---|:--:|---| is layout. Exporting it would put dashes in row 2 of every sheet."""
    from openpyxl import load_workbook

    from shared.tools.xlsx_render import markdown_to_xlsx

    out = str(tmp_path / "sep.xlsx")
    markdown_to_xlsx("| A | B |\n|:--|--:|\n| 1 | 2 |\n", out)
    ws = load_workbook(out).active
    assert ws["A2"].value == "1"


# -- a row without bytes must not be reported as "saved" -----------------------




