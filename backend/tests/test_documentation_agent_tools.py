"""Isolated unit coverage for Documentation agent internals that don't need the full
graph or a live LLM key -- tool-level edge cases and model-resolution regression
guards. The full-loop proof lives in test_documentation_agent_live_e2e.py; this file
is deliberately narrower and faster.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents_orchestrator.documentation_agent.config.session_state import get_session, clear_session
from agents_orchestrator.documentation_agent.tools import doc_tools
from config.ws_helper import set_session_id


def _git(args: list[str], cwd: str) -> None:
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def bound_session():
    """A session bound to a fresh temp work_dir, cleared before and after."""
    session_id = "doc-tools-unit-test"
    clear_session(session_id)
    set_session_id(session_id)
    s = get_session(session_id)
    d = tempfile.mkdtemp(prefix="doc_agent_tools_test_")
    s.work_dir = d
    yield s, d
    clear_session(session_id)
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_generate_changelog_defaults_an_unconventional_prefix_to_changed(bound_session):
    s, d = bound_session
    _git(["init", "-q"], d)
    _git(["config", "user.email", "t@t.com"], d)
    _git(["config", "user.name", "T"], d)
    (Path(d) / "a.txt").write_text("x", encoding="utf-8")
    _git(["add", "a.txt"], d)
    _git(["commit", "-q", "-m", "bump dependency version"], d)  # no conventional prefix

    result = json.loads(await doc_tools.generate_changelog.ainvoke({"since_ref": ""}))

    assert result["status"] == "ok"
    assert "### Changed" in result["changelog"]
    assert "bump dependency version" in result["changelog"]


@pytest.mark.asyncio
async def test_generate_changelog_excludes_merge_commits(bound_session):
    s, d = bound_session
    # -b trunk: explicit initial branch name -- git's default-branch-name config
    # (master vs. main) varies per machine, so this can't be left implicit if the
    # test is going to `checkout` back to it by name.
    _git(["init", "-q", "-b", "trunk"], d)
    _git(["config", "user.email", "t@t.com"], d)
    _git(["config", "user.name", "T"], d)
    (Path(d) / "a.txt").write_text("x", encoding="utf-8")
    _git(["add", "a.txt"], d)
    _git(["commit", "-q", "-m", "feat: base commit"], d)
    _git(["checkout", "-q", "-b", "side"], d)
    (Path(d) / "b.txt").write_text("y", encoding="utf-8")
    _git(["add", "b.txt"], d)
    _git(["commit", "-q", "-m", "feat: side commit"], d)
    _git(["checkout", "-q", "trunk"], d)
    _git(["merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side"], d)

    result = json.loads(await doc_tools.generate_changelog.ainvoke({"since_ref": ""}))

    assert result["status"] == "ok"
    assert "Merge branch" not in result["changelog"]
    assert result["commit_count"] == 2  # base + side, not the merge commit


@pytest.mark.asyncio
async def test_generate_changelog_with_no_commits_yet_returns_error_gracefully(bound_session):
    s, d = bound_session
    _git(["init", "-q"], d)
    # No commits at all — git log returns 128 in this case.

    result = json.loads(await doc_tools.generate_changelog.ainvoke({"since_ref": ""}))

    assert result["status"] == "error"
    assert result["changelog"] == ""


@pytest.mark.asyncio
async def test_read_repo_file_denies_a_path_traversal_attempt(bound_session):
    s, d = bound_session
    (Path(d) / "safe.txt").write_text("safe content", encoding="utf-8")

    result = await doc_tools.read_repo_file.ainvoke({"path": "../../../etc/passwd"})

    assert result.startswith("ERROR")
    assert "traversal" in result.lower()


@pytest.mark.asyncio
async def test_read_upstream_artifacts_returns_all_null_with_no_tenant_bound():
    session_id = "doc-tools-unbound-test"
    clear_session(session_id)
    set_session_id(session_id)
    # s.tenant_id / s.project_id left at their "" defaults -- never bound.

    result = json.loads(await doc_tools.read_upstream_artifacts.ainvoke({}))

    assert result == {
        "requirements": None, "design": None, "development": None,
        "testing": None, "code_review": None, "security": None,
    }
    clear_session(session_id)


@pytest.mark.asyncio
async def test_save_document_sanitizes_a_non_kebab_filename(bound_session, monkeypatch):
    s, d = bound_session
    s.project_id = "proj-1"
    out_root = tempfile.mkdtemp(prefix="doc_agent_output_")
    monkeypatch.setattr(doc_tools, "DOCS_OUTPUT_ROOT", out_root)

    result = await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Overview",
        "filename": "My Overview!!.docx", "markdown_contents": "# Overview\n",
    })

    assert "Saved" in result
    assert len(s.generated_docs) == 1
    saved_name = s.generated_docs[0]["filename"]
    assert saved_name.endswith(".md")  # non-.md extension coerced, not left as .docx
    assert " " not in saved_name and "!" not in saved_name
    shutil.rmtree(out_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_save_document_replaces_rather_than_duplicates_on_the_same_filename(bound_session, monkeypatch):
    s, d = bound_session
    s.project_id = "proj-1"
    out_root = tempfile.mkdtemp(prefix="doc_agent_output_")
    monkeypatch.setattr(doc_tools, "DOCS_OUTPUT_ROOT", out_root)

    await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Overview v1",
        "filename": "overview.md", "markdown_contents": "# v1\n",
    })
    await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Overview v2",
        "filename": "overview.md", "markdown_contents": "# v2\n",
    })

    assert len(s.generated_docs) == 1  # replaced, not appended
    assert s.generated_docs[0]["title"] == "Overview v2"
    shutil.rmtree(out_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_save_document_refuses_empty_content(bound_session):
    s, d = bound_session
    result = await doc_tools.save_document.ainvoke({
        "doc_type": "overview", "title": "Empty", "filename": "empty.md",
        "markdown_contents": "   ",
    })
    assert result.startswith("ERROR")
    assert len(s.generated_docs) == 0


@pytest.mark.asyncio
async def test_open_docs_pr_refuses_with_no_documents_generated(bound_session):
    s, d = bound_session
    result = await doc_tools.open_docs_pr.ainvoke({})
    assert result.startswith("ERROR")
    assert "no documents generated" in result.lower()


@pytest.mark.asyncio
async def test_open_docs_pr_refuses_with_no_prepared_target(bound_session):
    s, d = bound_session
    s.generated_docs = [{"filename": "x.md", "contents": "x", "id": "1", "type": "overview", "title": "X", "format": "md", "path": "", "bytes": 1}]
    s.repo_name = ""  # never prepared
    result = await doc_tools.open_docs_pr.ainvoke({})
    assert result.startswith("ERROR")
    assert "no prepared target" in result.lower()


@pytest.mark.asyncio
async def test_publish_to_sharepoint_reports_not_connected_cleanly(bound_session):
    import sys
    s, d = bound_session
    s.tenant_id = "tenant-1"
    s.generated_docs = [{"filename": "x.md", "contents": "x", "id": "1", "type": "overview", "title": "X", "format": "md", "path": "", "bytes": 1}]

    mock_notification_targets = MagicMock()
    mock_notification_targets.sharepoint_target = AsyncMock(return_value=None)

    with patch.dict(sys.modules, {"shared.services.notification_targets": mock_notification_targets}):
        result = await doc_tools.publish_to_sharepoint.ainvoke({})

    assert result.startswith("ERROR")
    assert "not connected" in result.lower()


@pytest.mark.asyncio
async def test_list_sharepoint_documents_reports_not_connected_cleanly(bound_session):
    import sys
    s, d = bound_session
    s.tenant_id = "tenant-1"

    mock_notification_targets = MagicMock()
    mock_notification_targets.sharepoint_target = AsyncMock(return_value=None)

    with patch.dict(sys.modules, {"shared.services.notification_targets": mock_notification_targets}):
        result = await doc_tools.list_sharepoint_documents.ainvoke({})

    assert result.startswith("ERROR")
    assert "not connected" in result.lower()


def test_resolve_model_tries_byok_first_and_returns_it_on_success():
    from agents_orchestrator.documentation_agent.agents.compiler import _resolve_model
    import sys

    fake_byok_model = MagicMock(name="byok_model")

    # Mock resolve_chat_model at import time by mocking the entire module
    mock_model_resolver = MagicMock()
    mock_model_resolver.resolve_chat_model = MagicMock(return_value=fake_byok_model)

    with patch.dict(sys.modules, {"shared.services.model_resolver": mock_model_resolver}):
        result = _resolve_model({"model_id": "claude-x", "offering_id": "off-1"})

    assert result is fake_byok_model
    mock_model_resolver.resolve_chat_model.assert_called_once()
    call_kwargs = mock_model_resolver.resolve_chat_model.call_args.kwargs
    assert call_kwargs["model_id"] == "claude-x"
    assert call_kwargs["offering_id"] == "off-1"


def test_resolve_model_propagates_a_resolution_failure():
    """A failed model resolution must surface, NOT fall back to the platform key.

    This test previously asserted the opposite — that _resolve_model caught the error
    and built a ChatAnthropic from the platform's ANTHROPIC_API_KEY. That behaviour was
    the bug: the catch-all also swallowed the ImportError from `resolve_chat_model`,
    which did not exist, so EVERY tenant run silently used the platform key and skipped
    budgets, model grants and rate limits.

    Whether a local-dev fallback is permitted is now resolve_chat_model's decision, made
    once against AGENT_RUNTIME_MODE, rather than each agent's own catch-all.
    See tests/test_byok_no_platform_fallback.py.
    """
    from agents_orchestrator.documentation_agent.agents.compiler import _resolve_model
    import sys

    mock_model_resolver = MagicMock()
    mock_model_resolver.resolve_chat_model = MagicMock(
        side_effect=RuntimeError("no provider configured")
    )

    with patch.dict(sys.modules, {"shared.services.model_resolver": mock_model_resolver}), patch(
        "langchain_anthropic.ChatAnthropic"
    ) as mock_chat_anthropic:
        with pytest.raises(RuntimeError, match="no provider configured"):
            _resolve_model({"model_id": "claude-requested", "offering_id": None})

    mock_chat_anthropic.assert_not_called()
