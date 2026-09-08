"""The Design agent must SAVE the document it generates, not offer to.

FOUND BY RUNNING ALL NINE AGENTS LIVE, TWICE. The Design agent's entire reply
through the Orchestrator was 104 characters:

    "The architecture document has been generated. Would you like me to save it
    as a .docx file for download?"

Nothing reached disk. The Project Manager had written
`Coffee_Ordering_App_Delivery_Plan.pdf` into `files/<user>/orchestrator/<run>/output/`
and Requirements had written `coffee_ordering_app_prd.docx` under its own stage
directory; the Design agent's Deliverables heading was empty, because there was no
file and the reply was far too short to be a document.

THE CAUSE WAS ONE PARAGRAPH OF `DESIGN_SYS_MESSAGE` (POST-TOOL RESPONSE), which told
the model to OFFER a save rather than to have saved:

    Respond with 1-2 sentences ONLY — e.g. "Architecture document generated. Would
    you like me to save it as a .docx file?"

An offer is a question, and an Orchestrator turn ends when the agent stops talking.
Nobody answers it. So the document existed only as the tokens the generating tool had
streamed — and on the Orchestrator surface not even that: `_llm_generate_async`
streams through `config.connection_manager.manager`, which has no socket registered
under an Orchestrator run id, and `orchestrator2/dispatch.py` deliberately does not
forward a tool RESULT as `stream_chunk`. The only channel that carries the document to
BOTH surfaces is the tool's own return value and the file it leaves on disk.

So the fix is not a better instruction. Generating a document and writing it down are
one act: `generate_architecture` and `generate_architecture_from_context` now write the
.docx themselves, and the prompt says so instead of asking.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


#: A document with the shape the panel parses — a title and two `##` sections. No
#: mermaid fence and no image: the docx converter would try to render/fetch those over
#: the network, and this suite must not.
DOC = (
    "# Coffee Ordering App — Architecture\n"
    "\n"
    "## High-Level Design (HLD)\n"
    "\n"
    "The storefront talks to an order service backed by Postgres.\n"
    "\n"
    "## Database Schema\n"
    "\n"
    "| Table | Purpose |\n"
    "|---|---|\n"
    "| orders | one row per order |\n"
)

RUN_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = "af57932d-f4f2-4673-aef7-d33b75c022f4"


@pytest.fixture
def design(tmp_path):
    """The design agent mid-run: a session, a user, and a stubbed model.

    The generation call itself is stubbed — this suite is about what happens to the
    document AFTER the model produces it, and a real call costs money. The websocket
    and the artifact registration are stubbed for the same reason the rest of this
    directory stubs them: neither exists in a unit test.
    """
    from agents_orchestrator.design_architecture_agent.agents import architecture as a
    from agents_orchestrator.design_architecture_agent.config import shared
    from shared.services import chat_artifacts

    original_dir = a._FILES_DIR
    a._FILES_DIR = str(tmp_path)
    shared.last_architecture = ""
    shared.output_file = ""
    shared.output_file_url = ""

    fake_manager = MagicMock()
    fake_manager.broadcast = AsyncMock()

    try:
        with patch.object(a, "get_user_id", lambda: USER_ID), \
                patch.object(a, "get_session_id", lambda: RUN_ID), \
                patch.object(a, "manager", fake_manager), \
                patch.object(a, "_llm_generate_async", AsyncMock(return_value=DOC)), \
                patch.object(chat_artifacts, "register_generated_file", AsyncMock()):
            yield a, tmp_path / USER_ID / "orchestrator" / RUN_ID / "output", fake_manager
    finally:
        a._FILES_DIR = original_dir
        shared.last_architecture = ""
        shared.output_file = ""
        shared.output_file_url = ""


def _saved_files(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("*.docx")) if out_dir.exists() else []


# ── the document reaches disk ────────────────────────────────────────────────


@pytest.mark.unit
async def test_generating_from_context_writes_the_docx_with_no_save_call(design):
    """THE BUG. The live run called this tool and nothing else, and produced no file."""
    a, out_dir, _ = design

    await a.generate_architecture_from_context.ainvoke({"context": "coffee ordering app"})

    written = _saved_files(out_dir)
    assert written, f"no .docx under {out_dir} — the document was generated and lost"
    assert written[0].stat().st_size > 0


@pytest.mark.unit
async def test_generating_from_a_document_writes_the_docx_too(design):
    """The file-upload entry point produces the same artifact and must not be the one
    path that still needs to be asked."""
    a, out_dir, _ = design

    await a.generate_architecture.ainvoke({"document_text": "some uploaded requirements"})

    assert _saved_files(out_dir), f"no .docx under {out_dir}"


@pytest.mark.unit
async def test_the_file_lands_where_the_deliverables_panel_looks(design):
    """`files/<user>/orchestrator/<run>/output/` — the exact path the Orchestrator's
    file-tree pointer is synthesised from, and the one the other agents already write
    to. `session_id` IS the run id on this path (dispatch sets it), so a document saved
    under the session id is a document filed under the run."""
    a, out_dir, _ = design

    await a.generate_architecture_from_context.ainvoke({"context": "coffee ordering app"})

    written = _saved_files(out_dir)
    assert written
    parts = written[0].relative_to(Path(a._FILES_DIR)).parts
    assert parts[0] == USER_ID
    assert parts[1] == "orchestrator"
    assert parts[2] == RUN_ID
    assert parts[3] == "output"


@pytest.mark.unit
async def test_the_saved_docx_holds_the_document_not_a_stub(design):
    """A zero-byte or placeholder file in the right place is the same empty
    Deliverables tab with an extra step."""
    from docx import Document

    a, out_dir, _ = design

    await a.generate_architecture_from_context.ainvoke({"context": "coffee ordering app"})

    text = "\n".join(p.text for p in Document(str(_saved_files(out_dir)[0])).paragraphs)
    assert "Coffee Ordering App" in text
    assert "High-Level Design" in text


@pytest.mark.unit
async def test_a_file_generated_event_announces_the_save(design):
    """The standalone Design page shows a download card off this event. Writing the
    file without announcing it is a document the user never learns exists."""
    a, _, fake_manager = design

    await a.generate_architecture_from_context.ainvoke({"context": "coffee ordering app"})

    kinds = [c.args[0].get("type") for c in fake_manager.broadcast.await_args_list if c.args]
    assert "file_generated" in kinds


# ── the model is handed a real link, not asked for permission ────────────────


@pytest.mark.unit
async def test_the_tool_result_hands_the_model_a_real_download_url(design):
    """The model can only quote a link it was given. Without one it either offers to
    save (the live failure) or invents a URL, and an invented `/generated/` link 404s."""
    a, _, _ = design

    result = await a.generate_architecture_from_context.ainvoke({"context": "coffee app"})

    assert f"/generated/{USER_ID}/orchestrator/{RUN_ID}/output/" in result
    assert ".docx" in result


@pytest.mark.unit
async def test_the_document_reaches_the_caller_through_the_return_value(design):
    """THE ONLY CHANNEL BOTH SURFACES SHARE.

    The standalone WS loop accumulates this string into `final_content`, which is what
    `_persist_design_artifacts` parses the eight sections out of. Trimming the tool's
    return to a receipt would silently stop design artifacts being saved — a visible
    bug traded for an invisible one.
    """
    a, _, _ = design

    result = await a.generate_architecture_from_context.ainvoke({"context": "coffee app"})

    assert "## High-Level Design (HLD)" in result
    assert "| orders | one row per order |" in result


@pytest.mark.unit
async def test_the_receipt_never_lands_inside_a_rendered_section(design):
    """THE RECEIPT GOES IN FRONT, and that is not a style choice.

    `parse_design_markdown` splits the document on its `##` headers and discards
    everything before the first one, so a receipt at the front disappears from the
    panel. A receipt appended at the end — the obvious alternative — would be rendered
    inside the document's LAST section, putting "SAVED: … download: http://…" in the
    middle of the Database Schema.
    """
    from shared.services.orchestrator.artifacts_view import parse_design_markdown

    a, _, _ = design

    result = await a.generate_architecture_from_context.ainvoke({"context": "coffee app"})

    sections, _persist = parse_design_markdown(result)
    assert sections, "the document no longer parses into panel sections at all"
    for section in sections:
        assert "SAVED:" not in section["content"], section["title"]
        assert "/generated/" not in section["content"], section["title"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("user_id", "session_id"),
    [(None, None), (USER_ID, None), (None, RUN_ID), ("", ""), (USER_ID, "")],
)
async def test_a_half_set_run_context_writes_no_stray_file(tmp_path, user_id, session_id):
    """BOTH halves of the path are required, not either one.

    The output path is `files/<user>/orchestrator/<run>/output/`. With one half missing
    the directory is still created and the file is still written — under the literal
    string "None" — behind a `/generated/None/…` URL that resolves for nobody. The
    check therefore has to be an OR over the two, and the difference between `or` and
    `and` here is a real file in a directory no user owns.
    """
    from agents_orchestrator.design_architecture_agent.agents import architecture as a

    original_dir = a._FILES_DIR
    a._FILES_DIR = str(tmp_path)
    fake_manager = MagicMock()
    fake_manager.broadcast = AsyncMock()
    try:
        with patch.object(a, "get_user_id", lambda: user_id), \
                patch.object(a, "get_session_id", lambda: session_id), \
                patch.object(a, "manager", fake_manager), \
                patch.object(a, "_llm_generate_async", AsyncMock(return_value=DOC)):
            result = await a.generate_architecture_from_context.ainvoke({"context": "x"})
    finally:
        a._FILES_DIR = original_dir

    assert "## High-Level Design (HLD)" in result
    assert not list(Path(tmp_path).rglob("*.docx"))


@pytest.mark.unit
async def test_the_stashed_copy_stays_clean_markdown(design):
    """`shared.last_architecture` is what every save tool falls back to when a weaker
    model cannot echo the document into a tool argument. A receipt line stashed with it
    would be rendered INTO the next .docx or .pdf the user asks for."""
    from agents_orchestrator.design_architecture_agent.config import shared

    a, _, _ = design

    await a.generate_architecture_from_context.ainvoke({"context": "coffee app"})

    assert shared.last_architecture == DOC


@pytest.mark.unit
async def test_the_filename_comes_from_the_documents_own_title(design):
    """Nine agents write into one `output/` directory. `architecture.docx` from every
    run of every project is how a Deliverables file tree stops being readable — the
    Project Manager already names its file `Coffee_Ordering_App_Delivery_Plan.pdf`."""
    a, out_dir, _ = design

    await a.generate_architecture_from_context.ainvoke({"context": "coffee app"})

    name = _saved_files(out_dir)[0].name.lower()
    assert "coffee" in name and name.endswith(".docx")


# ── failure must never cost the document ─────────────────────────────────────


@pytest.mark.unit
async def test_a_failed_write_never_costs_the_document(design):
    """The document is minutes of work and real tokens. If the .docx write fails, the
    markdown must still come back so the user can be shown it and `save_architecture`
    can retry — losing the generation to a disk error would be the more expensive bug."""
    a, out_dir, _ = design

    with patch.object(a, "_markdown_to_docx", AsyncMock(side_effect=OSError("disk full"))):
        result = await a.generate_architecture_from_context.ainvoke({"context": "coffee app"})

    assert "## High-Level Design (HLD)" in result
    assert not _saved_files(out_dir)


# ── where the streamed document actually goes ────────────────────────────────


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _FakeWS:
    """Enough of a Starlette WebSocket for ConnectionManager to send to."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def streaming(monkeypatch):
    """`_llm_generate_async` with a stubbed provider and a REAL ConnectionManager.

    The manager is real on purpose: the behaviour under test is its own routing rule,
    and a mock of it would assert what we already believe rather than what it does.
    """
    from types import SimpleNamespace

    from agents_orchestrator.design_architecture_agent.agents import architecture as a
    from config.connection_manager import ConnectionManager
    from shared.services import model_resolver

    async def _fake_acompletion(**_kwargs):
        async def _gen():
            for piece in ("## High-Level Design (HLD)\n", "the tenant's architecture"):
                yield _Chunk(piece)

        return _gen()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", _fake_acompletion)
    monkeypatch.setattr(
        model_resolver, "get_resolved_model",
        lambda: SimpleNamespace(model="gpt-4o", litellm_provider="openai",
                                api_key="k", base_url=None, alias="a"),
    )
    cm = ConnectionManager()
    monkeypatch.setattr(a, "manager", cm)
    monkeypatch.setattr(a, "get_session_id", lambda: RUN_ID)
    return a, cm


@pytest.mark.unit
async def test_the_document_never_fans_out_to_an_unrelated_socket(streaming):
    """THE PROMPT'S CLAIM, AND WHAT IT COSTS WHEN IT IS FALSE.

    `DESIGN_SYS_MESSAGE` said "the full document has already been streamed to the
    user in real time". That holds on the Design page, whose socket registers itself
    under the session id. It does NOT hold through `orchestrator2/dispatch.py`: that
    engine's socket layer never touches this ConnectionManager, so an Orchestrator run
    id has no registered connection.

    `ConnectionManager.broadcast` does not simply drop such a frame. It falls back to
    EVERY active connection, so on a process with any other legacy agent socket open,
    one tenant's architecture document was streamed, token by token, onto somebody
    else's socket. `broadcast_to_session` is the method that exists for exactly this
    and never falls back.
    """
    a, cm = streaming
    stranger = _FakeWS()
    cm.active_connections.append(stranger)
    cm.register_session(stranger, "a-completely-different-session")

    text = await a._llm_generate_async("design something")

    assert "the tenant's architecture" in text, "the caller must still get the document"
    assert stranger.sent == [], f"leaked {len(stranger.sent)} frame(s) to another session"


@pytest.mark.unit
async def test_the_design_page_still_receives_the_document_live(streaming):
    """The regression the fix above could cause. On the standalone page the socket IS
    registered under the session id before the graph runs
    (`design_architecture_agent_api.py` calls `register_session` on every frame), and
    watching the document build in real time is that page's whole behaviour."""
    a, cm = streaming
    watcher = _FakeWS()
    cm.active_connections.append(watcher)
    cm.register_session(watcher, RUN_ID)

    await a._llm_generate_async("design something")

    assert len(watcher.sent) == 2
    assert "the tenant's architecture" in watcher.sent[-1]


# ── the prompt must stop asking ──────────────────────────────────────────────


def _prompt() -> str:
    from agents_orchestrator.design_architecture_agent.agents.architecture import (
        DESIGN_SYS_MESSAGE,
    )

    return " ".join(DESIGN_SYS_MESSAGE.split())


@pytest.mark.unit
def test_the_prompt_no_longer_offers_to_save_the_document():
    """THE EXACT SENTENCE THE LIVE RUN ECHOED BACK. The model's 104-character reply was
    almost a verbatim quote of the example in its own prompt."""
    flat = _prompt()
    assert "Would you like me to save it as a .docx file?" not in flat
    assert "Would you like me to save" not in flat


@pytest.mark.unit
def test_the_prompt_tells_the_model_the_document_is_already_saved():
    """Removing the offer is not enough. Without a positive instruction the model is
    left guessing what to do after a tool call, and its prior is to be helpful by
    asking — which is the failure."""
    flat = _prompt()
    assert "already saved" in flat.lower()
    assert "NEVER offer to save it" in flat


@pytest.mark.unit
def test_the_prompt_makes_the_model_quote_the_link_it_was_given():
    """An invented `/generated/` URL looks exactly like a real one and 404s."""
    flat = _prompt()
    assert "SAVED:" in flat
    assert "never invent" in flat.lower()


@pytest.mark.unit
def test_the_streaming_claim_names_the_surface_it_is_true_on():
    """THE CLAIM WAS ONLY EVER HALF TRUE.

    "the full document has already been streamed to the user in real time" holds on the
    standalone Design page, where `_llm_generate_async` broadcasts to a socket
    registered under the session id. Through `orchestrator2/dispatch.py` it does not:
    no legacy socket is registered under a run id, and the tool result that carries the
    document is deliberately not forwarded as a `stream_chunk`. Left unqualified, the
    sentence justified a reply that says nothing on the surface where the user can see
    nothing else.
    """
    flat = _prompt()
    assert "already been streamed to the user" in flat
    idx = flat.index("already been streamed to the user")
    assert "Design page" in flat[max(0, idx - 200): idx + 200]
