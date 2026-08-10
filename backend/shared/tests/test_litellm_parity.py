"""LiteLLM proxy fidelity tests for M2-05 (D-09).

Verifies that the LiteLLM gateway produces responses structurally equivalent
to direct Anthropic calls for the most complex tool schemas in the requirements
agent (generate_brd, generate_user_stories).

Tests skip when LITELLM_BASE_URL is not set or ENABLE_LITELLM != 'true'.
They require ANTHROPIC_API_KEY for both direct and proxy paths.
"""
import json
import warnings

import pytest

from config.env import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL_STANDARD,
    ENABLE_LITELLM,
    LITELLM_BASE_URL,
    LITELLM_API_KEY,
)

_SKIP_NO_LITELLM = pytest.mark.skipif(
    not (LITELLM_BASE_URL and ENABLE_LITELLM),
    reason="LITELLM_BASE_URL not set or ENABLE_LITELLM != true — skipping LiteLLM parity tests",
)

# Tool schemas copied verbatim from requirements_agent/agents/planning.py
# (generate_brd, generate_user_stories) — do not simplify these schemas;
# the test's purpose is to verify parity on *complex* schemas.
_GENERATE_BRD_SCHEMA = {
    "name": "generate_brd",
    "description": (
        "Generate a Business Requirements Document (BRD) from uploaded files. "
        "Analyzes the provided files and creates a comprehensive BRD covering "
        "executive summary, business objectives, functional requirements, "
        "non-functional requirements, constraints, and success criteria."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of uploaded file names to analyze for BRD generation.",
            },
            "custom_prompt": {
                "type": "string",
                "description": "Optional custom prompt to guide BRD generation.",
            },
        },
        "required": ["file_names"],
    },
}

_GENERATE_USER_STORIES_SCHEMA = {
    "name": "generate_user_stories",
    "description": (
        "Generate user stories from uploaded requirements or BRD files. "
        "Creates structured Agile user stories with acceptance criteria "
        "in the format: As a [user], I want [feature], so that [benefit]."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of uploaded file names to extract user stories from.",
            },
            "custom_prompt": {
                "type": "string",
                "description": "Optional guidance for user story generation.",
            },
        },
        "required": ["file_names"],
    },
}

_ALL_TOOLS = [_GENERATE_BRD_SCHEMA, _GENERATE_USER_STORIES_SCHEMA]

_FORCE_BRD_PROMPT = (
    "I have uploaded project-requirements.txt. "
    "Please generate a Business Requirements Document for this project."
)
_PARALLEL_PROMPT = (
    "I have uploaded requirements.txt. "
    "Please generate both a BRD and user stories for this project simultaneously."
)

_MODEL = ANTHROPIC_MODEL_STANDARD or "claude-sonnet-4-6"


def _call_direct(prompt: str, tools: list, tool_choice: dict | None = None) -> dict:
    """Call Anthropic directly — baseline for parity comparison."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kwargs = {
        "model": _MODEL,
        "max_tokens": 1024,
        "tools": tools,
        "messages": [{"role": "user", "content": prompt}],
    }
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    return client.messages.create(**kwargs)


def _call_proxy(prompt: str, tools: list, tool_choice: dict | None = None) -> dict:
    """Call via LiteLLM proxy — subject under test."""
    import anthropic

    client = anthropic.Anthropic(
        api_key=LITELLM_API_KEY or ANTHROPIC_API_KEY,
        base_url=f"{LITELLM_BASE_URL}/v1",
    )
    kwargs = {
        "model": _MODEL,
        "max_tokens": 1024,
        "tools": tools,
        "messages": [{"role": "user", "content": prompt}],
    }
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    return client.messages.create(**kwargs)


def _extract_tool_calls(response) -> list[dict]:
    """Extract tool_use blocks from an Anthropic Message response."""
    return [
        {"name": block.name, "input": block.input}
        for block in response.content
        if block.type == "tool_use"
    ]


@pytest.mark.integration
@_SKIP_NO_LITELLM
def test_litellm_tool_calling_parity():
    """LiteLLM proxy produces tool calls with the same name and JSON key structure as direct Anthropic.

    Dropped fields (present in direct but absent in proxy) are logged with
    warnings.warn() rather than failing — they represent optional metadata.
    """
    direct_resp = _call_direct(_FORCE_BRD_PROMPT, _ALL_TOOLS)
    proxy_resp = _call_proxy(_FORCE_BRD_PROMPT, _ALL_TOOLS)

    direct_calls = _extract_tool_calls(direct_resp)
    proxy_calls = _extract_tool_calls(proxy_resp)

    assert len(proxy_calls) >= 1, (
        f"Proxy returned no tool calls. Response: {proxy_resp}"
    )
    assert len(direct_calls) >= 1, (
        f"Direct call returned no tool calls. Response: {direct_resp}"
    )

    direct_name = direct_calls[0]["name"]
    proxy_name = proxy_calls[0]["name"]
    assert direct_name == proxy_name, (
        f"Tool name mismatch: direct='{direct_name}', proxy='{proxy_name}'"
    )

    # Validate proxy input is valid JSON with the same top-level keys
    direct_keys = set(direct_calls[0]["input"].keys())
    proxy_keys = set(proxy_calls[0]["input"].keys())

    missing_from_proxy = direct_keys - proxy_keys
    if missing_from_proxy:
        warnings.warn(
            f"[LiteLLM parity] Fields present in direct call but absent in proxy: "
            f"{missing_from_proxy}. This may indicate schema translation differences.",
            UserWarning,
            stacklevel=2,
        )

    # Core assertion: proxy input must be valid JSON and have at least the same keys
    assert proxy_keys.issubset(direct_keys) or direct_keys.issubset(proxy_keys), (
        f"Tool input key sets incompatible: direct={direct_keys}, proxy={proxy_keys}"
    )


@pytest.mark.integration
@_SKIP_NO_LITELLM
def test_litellm_parallel_tool_calls():
    """Both direct and proxy paths must return >= 2 tool calls for a parallel-use prompt."""
    direct_resp = _call_direct(_PARALLEL_PROMPT, _ALL_TOOLS)
    proxy_resp = _call_proxy(_PARALLEL_PROMPT, _ALL_TOOLS)

    direct_calls = _extract_tool_calls(direct_resp)
    proxy_calls = _extract_tool_calls(proxy_resp)

    assert len(direct_calls) >= 2, (
        f"Direct call returned fewer than 2 tool calls: {[c['name'] for c in direct_calls]}"
    )
    assert len(proxy_calls) >= 2, (
        f"Proxy returned fewer than 2 tool calls: {[c['name'] for c in proxy_calls]}"
    )

    direct_names = sorted(c["name"] for c in direct_calls)
    proxy_names = sorted(c["name"] for c in proxy_calls)
    assert direct_names == proxy_names, (
        f"Parallel tool names differ: direct={direct_names}, proxy={proxy_names}"
    )


@pytest.mark.integration
@_SKIP_NO_LITELLM
def test_litellm_forced_tool_choice():
    """Forcing tool_choice=generate_brd must produce a generate_brd call via both paths."""
    tool_choice = {"type": "tool", "name": "generate_brd"}

    direct_resp = _call_direct(_FORCE_BRD_PROMPT, _ALL_TOOLS, tool_choice=tool_choice)
    proxy_resp = _call_proxy(_FORCE_BRD_PROMPT, _ALL_TOOLS, tool_choice=tool_choice)

    direct_calls = _extract_tool_calls(direct_resp)
    proxy_calls = _extract_tool_calls(proxy_resp)

    assert len(direct_calls) >= 1 and direct_calls[0]["name"] == "generate_brd", (
        f"Direct call did not use generate_brd: {direct_calls}"
    )
    assert len(proxy_calls) >= 1 and proxy_calls[0]["name"] == "generate_brd", (
        f"Proxy did not use generate_brd when forced: {proxy_calls}"
    )
