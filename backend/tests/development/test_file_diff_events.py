"""Tests for the file_diff WS event (Task 1 of the Development Agent chat overhaul):
write_file/edit_file must broadcast a structured {"type": "file_diff", ...} message
carrying full before/after file contents, so a later task can render a diff card in
the chat UI. Broadcast dispatch reuses broadcast_log's thread-safe MAIN_LOOP
scheduling (config/ws_helper.py) — with no MAIN_LOOP set in tests, that falls
through to `asyncio.get_running_loop().create_task(...)`, so these tests run as
async and flush the scheduled task with `await asyncio.sleep(0)` before asserting,
same technique as tests/cost/test_cost_service.py and tests/test_m93_eval_emit.py.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agents_orchestrator.development_agent.config.session_state import (
    clear_session,
    get_session,
)
from config.ws_helper import set_session_id, set_user_id

pytestmark = pytest.mark.asyncio


@pytest.fixture
def dev_session(tmp_path):
    session_id = "dev-file-diff-events-test"
    set_user_id("dev-file-diff-events-user")
    set_session_id(session_id)
    s = get_session(session_id)
    s.work_dir = str(tmp_path)
    yield s
    clear_session(session_id)


async def test_write_file_on_new_file_emits_created_diff(monkeypatch, dev_session):
    from agents_orchestrator.development_agent.tools import file_tools

    mock_broadcast = AsyncMock()
    monkeypatch.setattr(file_tools.manager, "broadcast", mock_broadcast)

    result = file_tools.write_file.invoke(
        {"relative_path": "src/App.jsx", "content": "export default function App() {}\n"}
    )
    await asyncio.sleep(0)

    assert "Successfully wrote" in result

    diff_calls = [c for c in mock_broadcast.await_args_list if c.args[0].get("type") == "file_diff"]
    assert len(diff_calls) == 1
    payload = diff_calls[0].args[0]
    assert payload["session_id"] == "dev-file-diff-events-test"
    assert payload["path"] == "src/App.jsx"
    assert payload["original"] == ""
    assert payload["modified"] == "export default function App() {}\n"
    assert payload["change_kind"] == "created"


async def test_write_file_overwriting_existing_file_emits_edited_diff(monkeypatch, dev_session):
    from agents_orchestrator.development_agent.tools import file_tools

    full_path = file_tools.resolve_safe_path(dev_session.work_dir, "config.py")
    full_path.write_text("OLD = 1\n", encoding="utf-8")

    mock_broadcast = AsyncMock()
    monkeypatch.setattr(file_tools.manager, "broadcast", mock_broadcast)

    result = file_tools.write_file.invoke(
        {"relative_path": "config.py", "content": "NEW = 2\n"}
    )
    await asyncio.sleep(0)

    assert "Successfully wrote" in result

    diff_calls = [c for c in mock_broadcast.await_args_list if c.args[0].get("type") == "file_diff"]
    assert len(diff_calls) == 1
    payload = diff_calls[0].args[0]
    assert payload["path"] == "config.py"
    assert payload["original"] == "OLD = 1\n"
    assert payload["modified"] == "NEW = 2\n"
    assert payload["change_kind"] == "edited"


async def test_write_file_overwriting_existing_but_empty_file_emits_edited_diff(monkeypatch, dev_session):
    """Regression for the review finding: change_kind must be derived from whether the
    file existed before the write, not from whether `original` happened to end up "".
    A pre-existing 0-byte file is still an edit, not a creation, even though its
    original content is indistinguishable (as a string) from a brand-new file's."""
    from agents_orchestrator.development_agent.tools import file_tools

    full_path = file_tools.resolve_safe_path(dev_session.work_dir, "__init__.py")
    full_path.write_text("", encoding="utf-8")  # pre-existing, genuinely empty file

    mock_broadcast = AsyncMock()
    monkeypatch.setattr(file_tools.manager, "broadcast", mock_broadcast)

    result = file_tools.write_file.invoke(
        {"relative_path": "__init__.py", "content": "from .models import Foo\n"}
    )
    await asyncio.sleep(0)

    assert "Successfully wrote" in result

    diff_calls = [c for c in mock_broadcast.await_args_list if c.args[0].get("type") == "file_diff"]
    assert len(diff_calls) == 1
    payload = diff_calls[0].args[0]
    assert payload["path"] == "__init__.py"
    assert payload["original"] == ""
    assert payload["modified"] == "from .models import Foo\n"
    assert payload["change_kind"] == "edited"


async def test_edit_file_emits_diff_matching_pre_and_post_content(monkeypatch, dev_session):
    from agents_orchestrator.development_agent.tools import file_tools

    full_path = file_tools.resolve_safe_path(dev_session.work_dir, "app.py")
    full_path.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

    mock_broadcast = AsyncMock()
    monkeypatch.setattr(file_tools.manager, "broadcast", mock_broadcast)

    result = file_tools.edit_file.invoke({
        "relative_path": "app.py",
        "old_string": "return 'hi'",
        "new_string": "return 'hello, world'",
    })
    await asyncio.sleep(0)

    assert "Successfully edited" in result

    diff_calls = [c for c in mock_broadcast.await_args_list if c.args[0].get("type") == "file_diff"]
    assert len(diff_calls) == 1
    payload = diff_calls[0].args[0]
    assert payload["path"] == "app.py"
    assert payload["original"] == "def hello():\n    return 'hi'\n"
    assert payload["modified"] == "def hello():\n    return 'hello, world'\n"
    assert payload["change_kind"] == "edited"


async def test_write_file_still_emits_activity_log_alongside_diff(monkeypatch, dev_session):
    """The diff event is additive, not a replacement — broadcast_log's
    activity_update message must still fire (asserted via manager.broadcast, which
    both broadcast_log and broadcast_file_diff funnel through)."""
    from agents_orchestrator.development_agent.tools import file_tools

    mock_broadcast = AsyncMock()
    monkeypatch.setattr(file_tools.manager, "broadcast", mock_broadcast)

    file_tools.write_file.invoke({"relative_path": "a.txt", "content": "hello\n"})
    await asyncio.sleep(0)

    types = [c.args[0].get("type") for c in mock_broadcast.await_args_list]
    assert "activity_update" in types
    assert "file_diff" in types
