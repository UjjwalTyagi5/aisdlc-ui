"""ContextVar-based dependency injection for the active connector.

Mirrors the contextvar pattern in config/ws_helper.py. The orchestrator/worker
calls set_connector() at task start; agent tools call get_connector() to reach
the backend without importing any SDK. clear_connector() releases the connector
in the worker's finally block so credentials do not survive task completion
(REQ-M3-10). Unlike ws_helper's getters, get_connector() RAISES when unset —
it never returns None, forcing explicit injection.
"""
from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.connectors.base import BaseConnector

_connector_var: contextvars.ContextVar["BaseConnector | None"] = contextvars.ContextVar(
    "active_connector", default=None
)


def set_connector(connector: "BaseConnector") -> None:
    _connector_var.set(connector)


def get_connector() -> "BaseConnector":
    connector = _connector_var.get()
    if connector is None:
        raise RuntimeError(
            "No connector injected — call set_connector() before invoking agent tools"
        )
    return connector


def clear_connector() -> None:
    _connector_var.set(None)
