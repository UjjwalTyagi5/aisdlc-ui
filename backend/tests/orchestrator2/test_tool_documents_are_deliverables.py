"""A document an agent produced through a TOOL is a deliverable too.

REPORTED, with a screenshot. The Design agent produced a real architecture document —
the user downloaded the .docx and it was complete, with all the tables and diagrams.
The Deliverables panel showed:

    Binary file
    This file can't be displayed as text.

"Instead of that, it should actually show the generated diagrams like it does in the
 standalone design agent. The documents were created, but they are not shown here.
 They are just put in the Word file for me to download."

WHY THE PANEL HAD ONLY A BINARY. `dispatch.run_agent` streams the graph and, for a tool
RESULT, yields `{"type": "tool.call", "name": …}` and DROPS the content:

    # A tool RESULT. Its `content` is the tool's output, and forwarding it as a
    # `stream_chunk` put it in the transcript as if the agent had said it — so it is
    # reported as activity and its text is not streamed.

That reasoning is right and stays. But the Design agent's whole document lives in that
discarded content: it writes a .docx and replies in chat with two sentences naming it.
So `deliverables.capture`, which reads only the streamed reply, had nothing to store,
and the only trace was the file — rendered by the code-tree viewer, which can only show
text and correctly refused.

THE FIX IS NOT TO STREAM IT. It is to CAPTURE it: the document becomes a markdown
deliverable the panel renders — mermaid diagrams and all — while the chat stays the
short summary the agent actually wrote.

THE RECEIPT HAS TO BE STRIPPED FIRST. `architecture.py` prepends a `SAVED: <url>` line
naming the .docx it just wrote. `deliverables.announces_a_saved_file` rejects any body
carrying a `/generated/` link — correctly, for a CHAT reply that merely points at a
document stored elsewhere. A tool result is the opposite case: it IS the document, with
a receipt attached. Stripping the receipt keeps one rule rather than carving an
exception into it.
"""
import pytest

from agents_orchestrator.orchestrator2 import deliverables as dv


def _rendered(agent_id, tool_output):
    """What `capture_tool_document` will persist, without needing a database.

    It strips the exporter's receipt and then applies the ordinary document rule —
    the same `render` a streamed reply goes through, so a document lands identically
    however it was produced.
    """
    return dv.render(agent_id, dv.strip_save_receipt(tool_output))


_DESIGN_DOC = (
    "## High-Level Design\n\n"
    + ("The duplicate banner renders inside the shared layout and reads its palette "
       "from site.css. " * 8)
    + "\n\n```mermaid\ngraph TD\n  A[Browser] --> B[RadAuthPortal]\n```\n\n"
    "## Database Schema\n\n"
    + ("No schema change is required for a presentation-only update. " * 8)
)

_RECEIPT = (
    "SAVED: http://localhost:8004/generated/af57932d/orchestrator/9de55574/output/"
    "Design_Document_Change_Duplicate_Table_Color_to_Pink.docx\n\n"
)


def test_a_tool_result_carrying_a_document_becomes_a_deliverable():
    """The reported case. Without this the panel has only the .docx."""
    rows = _rendered("design", _RECEIPT + _DESIGN_DOC)
    assert rows, "the design document produced through a tool was not captured"


def test_the_captured_document_keeps_its_diagrams():
    """What the user actually asked for — the diagrams, not a download link."""
    rows = _rendered("design", _RECEIPT + _DESIGN_DOC)
    body = "\n".join(r["content"] for r in rows)
    assert "```mermaid" in body, "the diagrams were stripped out of the document"
    assert "graph TD" in body


def test_the_save_receipt_is_not_part_of_the_document():
    """It is a line the exporter prepends, not prose the reader wants — and left in,
    `announces_a_saved_file` rejects the whole document as a mere announcement."""
    rows = _rendered("design", _RECEIPT + _DESIGN_DOC)
    body = "\n".join(r["content"] for r in rows)
    assert "SAVED:" not in body
    assert "/generated/" not in body


def test_a_tool_result_that_is_not_a_document_is_not_captured():
    """Most tool results are status lines, ids and JSON blobs. Capturing those refills
    the tab with exactly the noise the document rule was written to keep out."""
    assert _rendered("development", "Created branch feature/x") == []
    assert _rendered("security", '{"findings": [], "status": "ok"}') == []


def test_a_refusal_returned_by_a_tool_is_still_not_a_deliverable():
    """The document rule applies whole, not in part."""
    refusal = (
        "I cannot run the scan because no repository workspace is prepared.\n\n"
        "## Security Review Not Possible\n\n"
        + ("A branch must be selected before any scanner can run. " * 10)
        + "\n\n## What I Need\n\nSelect a branch and I will run the full review."
    )
    assert _rendered("security", refusal) == []


def test_design_sections_are_split_the_way_a_streamed_design_document_is():
    """A tool-produced design document must land in the panel the same shape as one the
    agent streamed — otherwise the same document looks different depending on how it
    happened to be produced."""
    rows = _rendered("design", _RECEIPT + _DESIGN_DOC)
    titles = [r["title"] for r in rows]
    assert "High-Level Design (HLD)" in titles
    assert "Database Schema" in titles


def test_an_empty_or_missing_tool_result_captures_nothing():
    assert _rendered("design", "") == []
    assert _rendered("design", None) == []


# ── the turn has to actually call it ─────────────────────────────────────────


def test_the_turn_collects_tool_results_for_capture():
    """`capture_tool_document` being correct is worth nothing if `dispatch` never
    reaches it — the shape of gap that has bitten this engine seven times."""
    import inspect

    from agents_orchestrator.orchestrator2 import dispatch

    src = inspect.getsource(dispatch.run_agent)
    assert "capture_tool_document" in src or "tool_documents" in src, (
        "tool results are still discarded, so a document produced through a tool "
        "never reaches the panel"
    )


def test_the_turn_still_does_not_stream_tool_output_into_the_chat():
    """The existing reasoning survives: forwarding a tool result as a `stream_chunk`
    puts it in the transcript as if the agent had said it."""
    import inspect

    from agents_orchestrator.orchestrator2 import dispatch

    src = inspect.getsource(dispatch.run_agent)
    tool_branch = src[src.index("if _is_tool_result(chunk):"):]
    tool_branch = tool_branch[: tool_branch.index("continue")]
    # The YIELDED key, not the bare word — the branch's own comment explains why a
    # tool result is not streamed, and asserting on the word trips on the explanation.
    assert '"type": "stream_chunk"' not in tool_branch, (
        "a tool result is being streamed into the chat as the agent's own words"
    )



# ── behaviour, not a grep ────────────────────────────────────────────────────
#
# The two tests above read `dispatch`'s SOURCE for the call. Mutation showed that is
# not enough: emptying the collection loop, or iterating an empty list, left both of
# them green — the string was still in the file. These drive a real turn.


class _ToolChunk:
    """A tool RESULT as the graph streams one. `tool_call_id` is the discriminator
    `_is_tool_result` uses — a ToolMessage reports type "tool" and a ToolMessageChunk
    reports "ToolMessageChunk", so neither type name can be relied on."""

    def __init__(self, content, name="generate_architecture"):
        self.content = content
        self.name = name
        self.tool_call_id = "call-1"
        self.tool_call_chunks = []


class _TextChunk:
    def __init__(self, content):
        self.content = content
        self.tool_call_chunks = []


class _GraphEmitting:
    def __init__(self, *messages):
        self._messages = messages

    async def astream(self, state, stream_mode=None, config=None):
        for message in self._messages:
            yield (message, {})


@pytest.fixture()
def _stub_model(monkeypatch):
    from agents_orchestrator.orchestrator2 import dispatch
    from shared.services import model_resolver as mr

    async def _resolve(tenant_id, requested_model_id=None, **kwargs):
        return mr.ResolvedModel(
            provider="anthropic", litellm_provider="anthropic", model="m",
            api_key="k", base_url=None, alias="a",
        )

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _resolve)
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


async def _turn(monkeypatch, graph, captures):
    from agents_orchestrator.orchestrator2 import dispatch
    from agents_orchestrator.orchestrator2 import registry as reg

    monkeypatch.setitem(
        reg.REGISTRY, "design",
        reg.AgentCapability(agent_id="design", load_graph=lambda: graph,
                            load_prompt=lambda: "SYS", mode="stream"),
    )

    async def _capture_tool(agent_id, output, *, run_id, tenant_id, project_id):
        captures.append(("tool", output))
        return []

    async def _capture_reply(agent_id, reply, *, run_id, tenant_id, project_id):
        captures.append(("reply", reply))
        return []

    monkeypatch.setattr(dispatch.deliverables, "capture_tool_document", _capture_tool)
    monkeypatch.setattr(dispatch.deliverables, "capture", _capture_reply)
    return [e async for e in dispatch.run_agent(
        "design", text="write the design doc", run_id="r1", tenant_id="t1",
        model_id=None, offering_id=None, project_id=None, user_id="u1",
        context="", reason="r")]


@pytest.mark.asyncio
async def test_a_document_returned_by_a_tool_reaches_capture(monkeypatch, _stub_model):
    """The reported bug, driven through the real turn."""
    captures = []
    await _turn(monkeypatch, _GraphEmitting(
        _ToolChunk(_RECEIPT + _DESIGN_DOC),
        _TextChunk("Your design document is ready."),
    ), captures)

    tool_captures = [body for kind, body in captures if kind == "tool"]
    assert tool_captures, "the tool's document never reached capture"
    assert "High-Level Design" in tool_captures[0]


@pytest.mark.asyncio
async def test_the_document_is_captured_before_the_reply(monkeypatch, _stub_model):
    """Order matters in the panel: the document should be what opens, not the two
    sentences announcing it."""
    captures = []
    await _turn(monkeypatch, _GraphEmitting(
        _ToolChunk(_RECEIPT + _DESIGN_DOC),
        _TextChunk("Your design document is ready."),
    ), captures)

    kinds = [kind for kind, _ in captures]
    assert kinds.index("tool") < kinds.index("reply")


@pytest.mark.asyncio
async def test_the_tool_output_is_still_not_streamed_into_the_chat(
    monkeypatch, _stub_model,
):
    """The whole document must not arrive as the agent's own words."""
    captures = []
    events = await _turn(monkeypatch, _GraphEmitting(
        _ToolChunk(_RECEIPT + _DESIGN_DOC),
        _TextChunk("Your design document is ready."),
    ), captures)

    streamed = "".join(
        e.get("content", "") for e in events if e.get("type") == "stream_chunk"
    )
    assert "High-Level Design" not in streamed
    assert streamed.strip() == "Your design document is ready."


@pytest.mark.asyncio
async def test_a_tool_that_floods_the_turn_is_bounded(monkeypatch, _stub_model):
    """Nothing limits what a tool may return, and this is held for the whole turn."""
    from agents_orchestrator.orchestrator2 import dispatch

    huge = "#" + ("y" * 60_000)
    captures = []
    await _turn(monkeypatch, _GraphEmitting(
        *[_ToolChunk(huge) for _ in range(6)]
    ), captures)

    kept = sum(len(body) for kind, body in captures if kind == "tool")
    assert kept <= dispatch.MAX_TOOL_DOCUMENT_CHARS + len(huge), (
        "the turn held every tool result no matter how large"
    )



@pytest.mark.asyncio
async def test_an_ordinary_tool_result_writes_nothing(monkeypatch, _stub_model):
    """Most tool results are status lines and ids. The document rule has to apply on
    this path too, or the tab refills with the noise it was written to remove."""
    captures = []
    await _turn(monkeypatch, _GraphEmitting(
        _ToolChunk("Created branch feature/x", name="create_branch"),
        _TextChunk("Branch created."),
    ), captures)

    # It still reaches capture — that is where the rule lives — but capture is what
    # decides, and `render` rejects a status line.
    from agents_orchestrator.orchestrator2 import deliverables as real_dv
    tool_bodies = [body for kind, body in captures if kind == "tool"]
    for body in tool_bodies:
        assert real_dv.render("development", real_dv.strip_save_receipt(body)) == [], (
            "a status line would have been stored as a document"
        )


@pytest.mark.asyncio
async def test_two_documents_are_captured_in_the_order_produced(
    monkeypatch, _stub_model,
):
    """An agent can emit more than one. Reversing them puts the later document above
    the earlier one in a panel that orders by arrival."""
    captures = []
    def _doc(name: str, filler: str) -> str:
        return (
            f"## {name}" + chr(10) * 2 + filler * 500
            + chr(10) * 2 + "## Section" + chr(10) * 2 + filler * 500
        )

    first, second = _doc("First Document", "a"), _doc("Second Document", "c")
    await _turn(monkeypatch, _GraphEmitting(
        _ToolChunk(first), _ToolChunk(second), _TextChunk("Both are ready."),
    ), captures)

    tool_bodies = [body for kind, body in captures if kind == "tool"]
    assert len(tool_bodies) == 2
    assert "First Document" in tool_bodies[0]
    assert "Second Document" in tool_bodies[1]
