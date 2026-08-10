"""Central per-request context for every agent in agentic_app.

AGENT_FOLDER is a ContextVar so concurrent requests entering through
different routers (individual-agent vs. orchestrator) don't clobber
each other's save paths.

Each router handler must call ``set_agent_folder(...)`` at the top
before invoking the agent graph:

    * Individual agent entry: set_agent_folder("<agent_name>_agent")
      e.g. "requirements_agent", "design_architecture_agent"
    * Orchestrator entry:     set_agent_folder("orchestrator")

Consumers build paths with ``get_agent_folder()`` instead of
hardcoding the subfolder name.
"""
import contextvars
import json
from typing import Any, Dict, Iterable, Optional

AGENT_FOLDER: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_folder", default="orchestrator"
)


def set_agent_folder(name: str) -> None:
    AGENT_FOLDER.set(name)


def get_agent_folder() -> str:
    return AGENT_FOLDER.get()


def parse_pipeline_context(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _compact(value: Any, *, max_string: int = 3000, max_items: int = 30, depth: int = 0) -> Any:
    if depth > 5:
        return str(value)[:max_string]
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "...[truncated]"
    if isinstance(value, list):
        compacted = [_compact(v, max_string=max_string, max_items=max_items, depth=depth + 1) for v in value[:max_items]]
        if len(value) > max_items:
            compacted.append(f"...[{len(value) - max_items} more items]")
        return compacted
    if isinstance(value, dict):
        return {
            str(k): _compact(v, max_string=max_string, max_items=max_items, depth=depth + 1)
            for k, v in value.items()
            if v not in (None, "", [], {})
        }
    return value


def format_pipeline_context(
    pipeline_context: Any,
    *,
    sections: Optional[Iterable[str]] = None,
) -> str:
    context = parse_pipeline_context(pipeline_context)
    if not context:
        return ""

    selected: Dict[str, Any] = {
        "session_id": context.get("session_id"),
        "user_id": context.get("user_id"),
        "board_provider": context.get("board_provider"),
        "last_handoff_event": context.get("last_handoff_event"),
    }
    for section in sections or ("requirements", "design", "development", "testing"):
        selected[section] = context.get(section)

    compacted = _compact(selected)
    if not compacted:
        return ""
    return "[STRUCTURED PIPELINE CONTEXT JSON]\n" + json.dumps(
        compacted,
        ensure_ascii=False,
        default=str,
    )


def build_agent_input_text(
    *,
    conversation_context: Optional[str] = None,
    task_intent: Optional[str] = None,
    pipeline_context: Any = None,
    pipeline_sections: Optional[Iterable[str]] = None,
) -> str:
    parts = []
    if conversation_context:
        parts.append(str(conversation_context))
    structured_context = format_pipeline_context(
        pipeline_context,
        sections=pipeline_sections,
    )
    if structured_context:
        parts.append(structured_context)
    if task_intent:
        parts.append(f"Task intent: {task_intent}")
    return "\n\n".join(parts)
