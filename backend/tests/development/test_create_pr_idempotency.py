"""Tests for the create_pr in-session idempotency guard.

The guard lives in _existing_pr_or_none() in git_tools.py. These tests exercise
the pure helper in isolation so no live provider, session globals, or network
calls are required.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agents_orchestrator.development_agent.tools.git_tools import _existing_pr_or_none
from shared.models.development import DevelopmentArtifacts


def _make_session(pr_url_artifacts: str | None = None, pr_url_session: str | None = None):
    """Build a minimal session-like object matching DevSessionState's shape."""
    s = MagicMock()
    artifacts = DevelopmentArtifacts()
    artifacts.pr_url = pr_url_artifacts
    s.dev_artifacts = artifacts
    s.pr_url = pr_url_session or ""
    return s


class TestExistingPrOrNone:
    def test_returns_none_when_no_pr_recorded(self):
        s = _make_session()
        assert _existing_pr_or_none(s) is None

    def test_returns_url_from_dev_artifacts(self):
        url = "https://dev.azure.com/org/proj/_git/repo/pullrequest/42"
        s = _make_session(pr_url_artifacts=url)
        assert _existing_pr_or_none(s) == url

    def test_returns_url_from_session_pr_url_fallback(self):
        url = "https://github.com/owner/repo/pull/7"
        s = _make_session(pr_url_artifacts=None, pr_url_session=url)
        assert _existing_pr_or_none(s) == url

    def test_dev_artifacts_url_takes_precedence_over_session(self):
        artifacts_url = "https://dev.azure.com/org/proj/_git/repo/pullrequest/1"
        session_url = "https://github.com/owner/repo/pull/99"
        s = _make_session(pr_url_artifacts=artifacts_url, pr_url_session=session_url)
        assert _existing_pr_or_none(s) == artifacts_url

    def test_empty_string_treated_as_no_pr(self):
        s = _make_session(pr_url_artifacts="", pr_url_session="")
        assert _existing_pr_or_none(s) is None

    def test_none_artifacts_pr_url_falls_back_to_session(self):
        url = "https://dev.azure.com/org/proj/_git/repo/pullrequest/5"
        s = _make_session(pr_url_artifacts=None, pr_url_session=url)
        assert _existing_pr_or_none(s) == url
