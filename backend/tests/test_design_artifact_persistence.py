"""Design's generated files have to become durable artifacts, not local scratch.

Two gaps, both silent.

1. THE REST CHAT PATH SET NO TENANT OR PROJECT. `register_generated_file` returns
   early without both ("no tenant/project in context - skip persist") at DEBUG level,
   so a .docx or diagram produced through POST /chat/ was written to local disk and
   never became an Artifact row: no blob upload, nothing in the project's artifact
   panel, and one debug line as the only trace. The WS path set them; this one did not.

2. FIGMA EXPORTS WERE NEVER DOWNLOADED. `export_figma_frames` returned Figma's own
   render URLs and appended a NOTE telling the caller to download and store any image
   that needs to persist - advice with no tool behind it. Those URLs expire in about
   30 days, so a design document that embedded one reviewed perfectly and broke a
   quarter later.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# -- 1. the REST path establishes the persist context -------------------------


@pytest.mark.unit
def test_the_design_rest_chat_sets_tenant_and_project():
    """Both are required - the guard is `if not tenant_id or not project_id`."""
    import inspect

    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    src = inspect.getsource(api.chat)
    assert "set_tenant_id(" in src
    assert "set_project_id(" in src


@pytest.mark.unit
def test_the_rest_path_uses_the_access_checked_project_not_the_raw_form_field():
    """`project_id` is reassigned from assert_agent_access_for_chat's return value
    before this point. Persisting under a client-supplied id would make the isolation
    key attacker-controlled."""
    import inspect

    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    src = inspect.getsource(api.chat)
    assert src.index("assert_agent_access_for_chat") < src.index("set_project_id(")


@pytest.mark.unit
def test_both_design_chat_paths_now_establish_it():
    import inspect

    from agents_orchestrator.design_architecture_agent import design_architecture_agent_api as api

    whole = inspect.getsource(api)
    assert whole.count("set_project_id(") >= 2, "WS and REST must both set it"


# -- 2. figma renders are downloaded and registered ---------------------------


class _Resp:
    def __init__(self, content=b"\x89PNG\r\n\x1a\n"):
        self.content = content

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, fail_for=()):
        self.fail_for = fail_for
        self.got = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        self.got.append(url)
        if url in self.fail_for:
            raise RuntimeError("network")
        return _Resp()


def _figma_source() -> str:
    """The module text. `@tool` wraps the function in a StructuredTool whose source
    inspect cannot reach, so the file is read directly."""
    from agents_orchestrator.design_architecture_agent.tools import figma_tools as ft

    return Path(ft.__file__).read_text(encoding="utf-8")


def _ctx(user="u1", session="s1"):
    import config.ws_helper as ws

    return (
        patch.object(ws, "get_user_id", lambda: user),
        patch.object(ws, "get_session_id", lambda: session),
    )


async def _run(images, *, fail_for=(), tmp=None):
    from agents_orchestrator.design_architecture_agent.tools import figma_tools as ft

    client = _Client(fail_for=fail_for)
    register = AsyncMock()
    a, b = _ctx()
    with a, b, \
            patch("httpx.AsyncClient", lambda *x, **k: client), \
            patch("shared.services.chat_artifacts.register_generated_file", register), \
            patch("config.sdlcSettings", lambda: type("S", (), {"FILES": str(tmp)})()):
        return await ft._persist_rendered(images, "png"), client, register


@pytest.mark.unit
async def test_each_rendered_frame_is_downloaded_and_registered(tmp_path):
    (stored, failed), client, register = await _run(
        {"1:23": "https://figma.example/a.png"}, tmp=tmp_path
    )
    assert failed == 0
    assert "1:23" in stored
    assert client.got == ["https://figma.example/a.png"]
    assert register.await_count == 1
    # Registered under the design stage, or it lands on the wrong stage's run.
    assert register.await_args.kwargs["stage"] == "design"


@pytest.mark.unit
async def test_the_returned_url_is_ours_not_figmas(tmp_path):
    """The point of the whole change: a document embeds a URL we keep serving."""
    (stored, _), _, _ = await _run({"1:23": "https://figma.example/a.png"}, tmp=tmp_path)
    assert "figma.example" not in stored["1:23"]
    assert "/generated/" in stored["1:23"]


@pytest.mark.unit
async def test_a_node_id_becomes_a_legal_filename(tmp_path):
    """Figma ids look like "1:23" and ':' is illegal in a Windows filename - the write
    would raise and every frame would be counted as failed."""
    (stored, failed), _, register = await _run({"1:23": "https://f/a.png"}, tmp=tmp_path)
    assert failed == 0
    assert ":" not in register.await_args.args[0]
    assert register.await_args.args[0] == "figma-1-23.png"


@pytest.mark.unit
async def test_one_bad_frame_does_not_lose_the_others(tmp_path):
    (stored, failed), _, _ = await _run(
        {"a": "https://f/ok.png", "b": "https://f/bad.png"},
        fail_for=("https://f/bad.png",), tmp=tmp_path,
    )
    assert set(stored) == {"a"}
    assert failed == 1


@pytest.mark.unit
async def test_no_chat_context_reports_everything_unstored():
    """A queued run has no user/session, so there is nowhere the download route could
    serve from. Counting them as failed is what makes the caller warn."""
    import config.ws_helper as ws

    from agents_orchestrator.design_architecture_agent.tools import figma_tools as ft

    with patch.object(ws, "get_user_id", lambda: ""), \
            patch.object(ws, "get_session_id", lambda: ""):
        stored, failed = await ft._persist_rendered({"a": "u1", "b": "u2"}, "png")
    assert stored == {} and failed == 2


@pytest.mark.unit
def test_the_tool_no_longer_only_advises_downloading():
    """It used to append a NOTE telling the caller to download and store the images,
    with no tool able to do it - prompt-only enforcement of a durability rule."""
    src = _figma_source()
    assert "_persist_rendered" in src
    assert "Download and store any image" not in src


@pytest.mark.unit
def test_unstored_images_are_flagged_as_temporary():
    """The remaining Figma URLs still work TODAY; embedding them silently is exactly
    how the expiry problem returns."""
    src = _figma_source()
    assert "temporary Figma URL" in src
    assert "expire" in src


# -- image content types ------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "ctype"),
    [("d.png", "image/png"), ("d.svg", "image/svg+xml"),
     ("d.jpg", "image/jpeg"), ("d.jpeg", "image/jpeg")],
)
def test_every_image_type_the_agent_can_emit_has_a_content_type(name, ctype):
    """jpg/jpeg were classified as diagrams but had no content type, so a browser was
    handed application/octet-stream and offered a download instead of the image."""
    import os

    from shared.services.chat_artifacts import _CONTENT_TYPES, _artifact_type_for

    ext = os.path.splitext(name)[1]
    assert _CONTENT_TYPES.get(ext) == ctype
    assert _artifact_type_for(name) == "diagram"
