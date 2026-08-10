"""Orchestrator state client — delegates to the in-process Postgres store.

Previously made HTTP calls to Django. Now calls shared.services.agent_session_store
directly, eliminating the Django process dependency and one network hop.
"""
from __future__ import annotations

import json
import re as _re

from typing import Any, Dict, Optional

from shared.services import agent_session_store as _store


def _parse_handoff_dict(text: str):
    """Find and validate a HANDOFF::{...} sentinel in agent output."""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("HANDOFF::"):
            raw = stripped[len("HANDOFF::"):]
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def _strip_handoff_sentinel(text: str) -> str:
    """Remove any HANDOFF:: sentinel(s) from text before showing to the user."""
    if not text:
        return text
    return _re.sub(r"\nHANDOFF::\{[^\n]*\}", "", text).strip()


async def get_state(chat_session_id: str) -> Optional[Dict[str, Any]]:
    return await _store.get_orchestrator_state(chat_session_id)


async def set_state(
    chat_session_id: str,
    *,
    current_active_agent: Optional[str] = None,
    current_batch_id: Optional[str] = None,
    pending_user_gate: Optional[str] = None,
    last_handoff_event: Optional[Dict[str, Any]] = None,
) -> None:
    await _store.set_orchestrator_state(
        chat_session_id,
        current_active_agent=current_active_agent,
        current_batch_id=current_batch_id,
        pending_user_gate=pending_user_gate,
        last_handoff_event=last_handoff_event,
    )


async def clear_state(chat_session_id: str) -> None:
    await _store.clear_orchestrator_state(chat_session_id)


async def fetch_session_artifacts(session_id: str) -> Optional[Dict[str, Any]]:
    """Return all typed artifact fields for a session.

    Returns None if the session does not exist or the request fails, so callers
    can safely treat a None result as "no artifacts yet".
    """
    return await _store.fetch_session_artifacts(session_id)


async def clear_session_artifacts(session_id: str) -> None:
    """Reset all typed artifact fields for a session to None/empty (best-effort)."""
    await _store.clear_session_artifacts(session_id)


def parse_handoff(text: str) -> Optional[Dict[str, Any]]:
    """Find and validate a HANDOFF::{...} sentinel in agent output."""
    return _parse_handoff_dict(text)


def strip_handoff(text: str) -> str:
    """Remove any HANDOFF:: sentinel(s) from text before showing to the user."""
    return _strip_handoff_sentinel(text)
