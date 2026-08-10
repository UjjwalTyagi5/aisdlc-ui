"""Regression tests for the live-run finding 2026-05-05:

Symptoms (from user's session f8aafe4e02aa7455ce8bf17fb2caf172):
1. User said "okay push on remote but dont create pr" → dev pushed branch
   `feature/26-27-dup-check-updates` to repo `carelon`.
2. User said "test it now" → testing routed correctly but replied
   "I'm ready to test, but I don't yet have the repo + branch the
    development agent built."
3. User typed "test branch which the dev agent create and pushed changes to"
   → testing again said the same thing.

Three compounding root causes we now test for:

A) `extract_dev_chat_hints` patterns missed BACKTICK-quoted branches
   (markdown convention `feature/x-y`) and markdown-bold repo names
   (**carelon**). Fixed by adding new patterns.

B) Orchestrator's `enrich_input` for testing stage didn't include
   `Recent Conversation`, so the chat-history fallback in
   `pull_upstream_context` had no chat to mine. Fixed by adding the
   conversation block to the testing branch.

C) `submit_development_artifacts` tool docstring told the LLM to call it
   only "after PR has been created" — so when user said no PR, the dev
   never persisted development_artifacts. Fixed by loosening the docstring.
"""
from __future__ import annotations

import sys
import types

import pytest

from agents_orchestrator.testing_agent.Nodes import ingest_input
from agents_orchestrator.testing_agent.Nodes.ingest_input import classify_intent
from agents_orchestrator.testing_agent.tools.ado_clone import (
    extract_dev_chat_hints,
)


# Verbatim excerpts from the user's actual chat in the failing run.

DEV_BRANCH_PUSH_MSG = (
    "✅ **Branch pushed successfully!**\n\n"
    "**Branch:** `feature/26-27-dup-check-updates` is now live on the remote.\n\n"
    "Here's a summary of what's been delivered:"
)

DEV_BRANCH_CREATE_MSG = (
    "I'll create a new branch `feature/26-27-dup-check-updates` from `main` "
    "for both work items. Shall I go ahead with that branch name?\n\n"
    "> 🔀 **New branch:** `feature/26-27-dup-check-updates`\n"
    "> 📌 **Based on:** `main`\n"
    "> 🔗 **Work items:** #26, #27"
)

DEV_REPO_CLONE_MSG = (
    "Cloned **carelon** successfully. Now let me list the branches."
)

DEV_REPO_LISTING_MSG = (
    "I'll start by fetching both work items. Now let me list the repos in "
    "the **carelon** project so we can pick the right one to work in."
)


def test_extracts_branch_from_backtick_quoted_dev_message():
    """Dev's actual output uses backticks, not single quotes — pre-fix this missed."""
    hints = extract_dev_chat_hints(DEV_BRANCH_PUSH_MSG)
    assert hints.get("branch") == "feature/26-27-dup-check-updates", (
        f"Expected 'feature/26-27-dup-check-updates', got {hints.get('branch')!r}. "
        "Pre-fix, the patterns only matched single/double quotes — never backticks."
    )


def test_extracts_branch_from_create_message():
    """'I'll create a new branch `feature/x-y` from `main`' — pre-fix this missed."""
    hints = extract_dev_chat_hints(DEV_BRANCH_CREATE_MSG)
    assert hints.get("branch") == "feature/26-27-dup-check-updates"


def test_extracts_repo_from_cloned_message():
    """'Cloned **carelon** successfully' — pre-fix this matched neither repo nor project."""
    hints = extract_dev_chat_hints(DEV_REPO_CLONE_MSG)
    assert hints.get("repo") == "carelon", f"Expected 'carelon', got {hints!r}"


def test_extracts_project_from_in_project_pattern():
    """'in the **carelon** project' — pre-fix this missed."""
    hints = extract_dev_chat_hints(DEV_REPO_LISTING_MSG)
    assert hints.get("project") == "carelon"
    # When project is mentioned but repo isn't, default repo to project name (ADO convention)
    assert hints.get("repo") == "carelon"


def test_full_recovery_chain_from_users_actual_chat():
    """Simulate the chat-history fallback from the user's actual session.
    Iterating dev's chat messages should yield branch + repo + project = full clone target."""
    chat_messages = [
        {"role": "assistant", "content": DEV_REPO_LISTING_MSG},
        {"role": "user", "content": "carelon"},
        {"role": "assistant", "content": DEV_REPO_CLONE_MSG},
        {"role": "assistant", "content": DEV_BRANCH_CREATE_MSG},
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": "Branch created!"},
        {"role": "assistant", "content": DEV_BRANCH_PUSH_MSG},
    ]

    # Mirrors pull_upstream_context's logic: iterate newest-first, accumulate
    merged: dict = {}
    for msg in reversed(chat_messages):
        content = msg.get("content") or ""
        if not content:
            continue
        hints = extract_dev_chat_hints(content)
        for k, v in hints.items():
            merged.setdefault(k, v)
        if "branch" in merged and "repo" in merged:
            break

    assert merged.get("branch") == "feature/26-27-dup-check-updates", (
        f"Expected branch recovered, got {merged!r}"
    )
    assert merged.get("repo") == "carelon", f"Expected repo='carelon', got {merged!r}"
    # Project is optional but should default to repo
    assert merged.get("project") in ("carelon", None)


def test_existing_quoted_patterns_still_work():
    """Backward compat — single-quoted forms should still match."""
    text = "Repository 'mybackend' created in project 'mycorp'."
    hints = extract_dev_chat_hints(text)
    assert hints.get("repo") == "mybackend"
    assert hints.get("project") == "mycorp"


def test_existing_branch_quoted_pattern_still_works():
    """'Creating branch: <name>' (no quotes) still matches."""
    text = "Creating branch: feature/login-page"
    hints = extract_dev_chat_hints(text)
    assert hints.get("branch") == "feature/login-page"


def test_ado_url_still_winning_over_backtick():
    """If ADO URL is present, it wins (most authoritative)."""
    text = (
        "Draft PR created: https://dev.azure.com/myorg/myproject/_git/myrepo/pullrequest/123\n"
        "Cloned **otherrepo** successfully."
    )
    hints = extract_dev_chat_hints(text)
    # ADO URL should populate first, repo should be myrepo (not otherrepo)
    assert hints.get("repo") == "myrepo"
    assert hints.get("project") == "myproject"


def test_extracts_branch_from_orchestrator_development_context_section():
    text = (
        "## Branch\n"
        "feature/new-case-flow\n\n"
        "## Repository\n"
        "https://dev.azure.com/myorg/carelon/_git/radauth"
    )

    hints = extract_dev_chat_hints(text)

    assert hints.get("branch") == "feature/new-case-flow"
    assert hints.get("project") == "carelon"
    assert hints.get("repo") == "radauth"


@pytest.mark.asyncio
async def test_orchestrator_context_blob_recovers_dev_handoff_and_asks_scope(monkeypatch):
    async def no_context(*_args, **_kwargs):
        return None

    async def no_artifacts(*_args, **_kwargs):
        return {}

    async def no_sleep(*_args, **_kwargs):
        return None

    context_broker = types.ModuleType("config.context_broker")
    context_broker.build_context = no_context
    handoff_router = types.ModuleType("config.handoff_router")
    handoff_router.pop_cached_context = lambda _sid: None
    state_client = types.ModuleType("config.orchestrator_state_client")
    state_client.fetch_session_artifacts = no_artifacts

    monkeypatch.setitem(sys.modules, "config.context_broker", context_broker)
    monkeypatch.setitem(sys.modules, "config.handoff_router", handoff_router)
    monkeypatch.setitem(sys.modules, "config.orchestrator_state_client", state_client)
    monkeypatch.setattr(ingest_input, "get_session_id", lambda: "session-with-dev-context")
    monkeypatch.setattr(ingest_input.asyncio, "sleep", no_sleep)

    state = {
        "user_prompt": (
            "Development Context\n"
            "## Branch\n"
            "feature/new-case-flow\n\n"
            "## Repository\n"
            "https://dev.azure.com/myorg/carelon/_git/radauth\n\n"
            "Task intent: test it"
        ),
        "input_file_path": None,
        "clone_target": None,
    }

    pulled = await ingest_input.pull_upstream_context(state)

    upstream = pulled.get("upstream_development") or {}
    assert upstream.get("branch_name") == "feature/new-case-flow"
    assert upstream.get("repo") == "radauth"
    assert upstream.get("project") == "carelon"

    classified = await classify_intent({"user_prompt": "test it", **pulled})
    assert classified["classified_intent"] == "greeting"
    assert classified["awaiting_scope"] is True
    assert "What testing would you like me to run" in classified["final_user_message"]
