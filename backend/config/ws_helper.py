# agents/requirements_agent/ws_helpers.py

import contextvars

import asyncio

import logging

from datetime import datetime
import pytz
import uuid

# session context var

SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default=None)
USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default=None)
PROVIDER_KIND: contextvars.ContextVar[str] = contextvars.ContextVar("provider_kind", default="azure_devops")
# Chat scope needed to persist chat-generated files as Artifact rows (so they surface
# in the project artifacts panel, not just the live WS download). project/run may be
# absent for a purely ad-hoc chat — the artifact writer degrades gracefully then.
TENANT_ID: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default=None)
PROJECT_ID: contextvars.ContextVar[str] = contextvars.ContextVar("project_id", default=None)
RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default=None)

def set_tenant_id(tenant_id):
    TENANT_ID.set(str(tenant_id) if tenant_id else None)

def set_project_id(project_id):
    PROJECT_ID.set(str(project_id) if project_id else None)

def set_run_id(run_id):
    RUN_ID.set(str(run_id) if run_id else None)

def get_tenant_id():
    try:
        return TENANT_ID.get()
    except LookupError:
        return None

def get_project_id():
    try:
        return PROJECT_ID.get()
    except LookupError:
        return None

def get_run_id():
    try:
        return RUN_ID.get()
    except LookupError:
        return None

def set_session_id(session_id: str):
    return SESSION_ID.set(session_id)

def reset_session_id(token) -> None:
    try:
        SESSION_ID.reset(token)
    except (ValueError, LookupError):
        SESSION_ID.set(None)

def set_user_id(user_id: str):
    USER_ID.set(user_id)

def set_provider_kind(kind: str):
    PROVIDER_KIND.set(kind or "azure_devops")

def get_session_id():
    try:
        return SESSION_ID.get()
    except LookupError:
        return None

def get_user_id():
    try:
        return USER_ID.get()
    except LookupError:
        return None

def get_provider_kind() -> str:
    try:
        return PROVIDER_KIND.get() or "azure_devops"
    except LookupError:
        return "azure_devops"

import asyncio

# You'll need to capture the main loop at startup.

MAIN_LOOP: asyncio.AbstractEventLoop | None = None

def set_main_loop(loop: asyncio.AbstractEventLoop):

    global MAIN_LOOP

    MAIN_LOOP = loop



_LEVEL_TO_TYPE = {
    "INFO":    "info",
    "LOGS":    "log",
    "ERROR":   "error",
    "WARNING": "warning",
    "SUCCESS": "success",
    "FILE":    "file_generated",
}


def broadcast_log(manager, message: str, level: str = "INFO"):

    session_id = get_session_id()
    ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    print(message, "=======================")
    activity = {

        "id": f"activity_{int(datetime.utcnow().timestamp()*1000)}_{uuid.uuid4().hex[:6]}",

        "message": message,

        "type": _LEVEL_TO_TYPE.get(level.upper(), "log"),

        "time": ist.strftime("%I:%M:%S %p"),

        "printData": None,

        "sessionId": session_id,

    }

    payload = {"type": "activity_update", "activity": activity}

    # If we have the main loop and it's running, schedule thread-safe.

    if MAIN_LOOP and MAIN_LOOP.is_running():

        try:

            asyncio.run_coroutine_threadsafe(manager.broadcast(payload), MAIN_LOOP)

            return

        except Exception as e:

            print(f"broadcast_log scheduling failed on MAIN_LOOP: {e}")

    # Otherwise, if we're inside an async context, use current loop.

    try:

        loop = asyncio.get_running_loop()

        loop.create_task(manager.broadcast(payload))

    except RuntimeError:

        # No running loop: fallback to synchronous log

        print(f"Could not schedule broadcast, falling back. {level}: {message}")


def broadcast_file_diff(manager, path: str, original: str, modified: str, change_kind: str) -> None:
    """Broadcast a structured file-diff event so the frontend can render a diff card
    for a write_file/edit_file call. Sibling to broadcast_log — same thread-safe
    MAIN_LOOP scheduling dance, since file_tools' write_file/edit_file are sync
    functions calling into async broadcast machinery."""

    session_id = get_session_id()

    payload = {
        "type": "file_diff",
        "session_id": session_id,
        "path": path,
        "original": original,
        "modified": modified,
        "change_kind": change_kind,
    }

    # If we have the main loop and it's running, schedule thread-safe.

    if MAIN_LOOP and MAIN_LOOP.is_running():

        try:

            asyncio.run_coroutine_threadsafe(manager.broadcast_to_session(payload), MAIN_LOOP)

            return

        except Exception as e:

            print(f"broadcast_file_diff scheduling failed on MAIN_LOOP: {e}")

    # Otherwise, if we're inside an async context, use current loop.

    try:

        loop = asyncio.get_running_loop()

        loop.create_task(manager.broadcast_to_session(payload))

    except RuntimeError:

        # No running loop: fallback to synchronous log

        print(f"Could not schedule file_diff broadcast, falling back. {change_kind}: {path}")
