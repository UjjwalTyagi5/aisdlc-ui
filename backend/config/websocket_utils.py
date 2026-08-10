import asyncio
from uuid import uuid4
import threading

websocket_manager = None
current_session_id = None


def set_websocket_context(manager, session_id):
    """Set the global WebSocket manager and session ID."""
    global websocket_manager, current_session_id
    websocket_manager = manager
    current_session_id = session_id


 