"""Structured pipeline context shared by the SDLC orchestrator stages."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def empty_pipeline_context(
    session_id: str = "",
    user_id: str = "",
    provider_kind: str = "azure_devops",
) -> Dict[str, Any]:
    return {
        "session_id": session_id or "",
        "user_id": user_id or "",
        "board_provider": provider_kind or "azure_devops",
        "requirements": {},
        "design": {},
        "development": {},
        "testing": {},
        "last_handoff_event": {},
    }


def build_pipeline_context(
    *,
    session_id: str,
    user_id: Optional[str] = "",
    provider_kind: Optional[str] = None,
    artifacts: Optional[Dict[str, Any]] = None,
    last_handoff_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    artifacts = artifacts or {}
    requirements = _as_dict(artifacts.get("requirements_payload"))
    explicit_handoff = _as_dict(last_handoff_event)
    stored_handoff = _as_dict(artifacts.get("last_handoff_event"))

    return {
        "session_id": session_id or "",
        "user_id": user_id or artifacts.get("user_id") or "",
        "board_provider": provider_kind or requirements.get("provider_kind") or "azure_devops",
        "requirements": requirements,
        "design": _as_dict(artifacts.get("design_artifacts")),
        "development": _as_dict(artifacts.get("development_artifacts")),
        "testing": _as_dict(artifacts.get("testing_artifacts")),
        "last_handoff_event": explicit_handoff or stored_handoff,
    }


def pipeline_context_json(context: Optional[Dict[str, Any]]) -> str:
    return json.dumps(context or {}, ensure_ascii=False, default=str)
