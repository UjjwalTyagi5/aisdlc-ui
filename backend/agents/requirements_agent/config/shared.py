"""Shared per-request state for the requirements agent.

Re-exports agent_folder helpers from the central module so every
agent reads/writes the same ContextVar instance.
"""
from config.agent_context import (
    AGENT_FOLDER,
    get_agent_folder,
    set_agent_folder,
)

output_file = ""
prev_session_id = ""
