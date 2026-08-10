"""Shared fixtures for milestone-9.2 cost test package.

mock_audit_service mirrors tests/audit/conftest.py's fixture of the same name —
duplicated locally because pytest fixtures are not shared across sibling
top-level test packages without a common conftest.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_audit_service():
    """Mock AuditEventService with async emit() and emit_blocking() methods."""
    svc = MagicMock()
    svc.emit = AsyncMock(return_value=None)
    svc.emit_blocking = AsyncMock(return_value=None)
    svc._write = AsyncMock(return_value=None)
    svc._send_to_dead_letter = AsyncMock(return_value=None)
    return svc
