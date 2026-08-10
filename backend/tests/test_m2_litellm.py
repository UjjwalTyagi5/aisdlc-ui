"""M2-05 LiteLLM fidelity and cost tracking tests.

Integration tests (require litellm-proxy running):
  - test_litellm_response_parity
  - test_litellm_forced_tool_choice
  - test_litellm_parallel_tool_calls
  - test_litellm_cost_tracking

All tests are marked @pytest.mark.integration and use @proxy_available to skip
automatically when ENABLE_LITELLM=false (proxy not configured).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import litellm

from config.env import LITELLM_BASE_URL, LITELLM_API_KEY, ENABLE_LITELLM

# Skip all tests in this module when the LiteLLM proxy is not configured/enabled.
proxy_available = pytest.mark.skipif(
    not ENABLE_LITELLM,
    reason="ENABLE_LITELLM=false — set true and start litellm-proxy to run LiteLLM tests",
)

# Tool schemas used across multiple tests
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "The city name"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current time for a timezone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "Timezone identifier, e.g. UTC"},
                },
                "required": ["timezone"],
            },
        },
    },
]


@pytest.mark.integration
@proxy_available
def test_litellm_response_parity():
    """D-08: Tool call round-trip through the proxy returns at least one tool call.

    Sends a prompt that requests both weather and time tools. Asserts the proxy
    routes the request correctly and the model returns tool calls.
    """
    response = litellm.completion(
        model="claude-sonnet-4-6",
        messages=[
            {"role": "user", "content": "What is the weather in London? Also what time is it in UTC?"}
        ],
        tools=_TOOLS,
        api_base=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
    )
    tool_calls = response.choices[0].message.tool_calls
    assert tool_calls is not None, "Expected tool_calls to be non-None"
    assert len(tool_calls) >= 1, f"Expected at least 1 tool call, got {len(tool_calls)}"


@pytest.mark.integration
@proxy_available
def test_litellm_forced_tool_choice():
    """D-08: Forced tool_choice is respected end-to-end through the proxy.

    When tool_choice forces 'get_weather', the model must return exactly that
    tool call — validating that tool_choice is forwarded without loss by LiteLLM.
    """
    response = litellm.completion(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "What is the weather in Paris?"}],
        tools=_TOOLS,
        tool_choice={"type": "function", "function": {"name": "get_weather"}},
        api_base=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
    )
    tool_calls = response.choices[0].message.tool_calls
    assert tool_calls is not None and len(tool_calls) >= 1, "Expected forced tool call"
    assert tool_calls[0].function.name == "get_weather", (
        f"Expected forced tool 'get_weather', got '{tool_calls[0].function.name}'"
    )


@pytest.mark.integration
@proxy_available
def test_litellm_parallel_tool_calls():
    """D-08: Parallel tool calls are returned when the prompt requests multiple cities.

    Sends a prompt that naturally elicits parallel calls to get_weather for two
    different cities. Asserts at least 2 tool calls are returned.
    """
    response = litellm.completion(
        model="claude-sonnet-4-6",
        messages=[
            {"role": "user", "content": "What is the weather in London and Paris at the same time?"}
        ],
        tools=_TOOLS,
        api_base=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
    )
    tool_calls = response.choices[0].message.tool_calls
    assert tool_calls is not None, "Expected tool_calls for parallel request"
    assert len(tool_calls) >= 2, (
        f"Expected >= 2 parallel tool calls for London+Paris, got {len(tool_calls)}"
    )


@pytest.mark.integration
@proxy_available
def test_litellm_cost_tracking():
    """D-07: Token usage metadata is present in the proxy response.

    Makes a simple chat completion (no tools) and asserts the response carries
    prompt_tokens and completion_tokens > 0. The proxy must propagate usage
    metadata from the upstream provider for per-run cost tracking to work.
    """
    response = litellm.completion(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "Say hello in one word."}],
        api_base=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
    )
    usage = response.usage
    assert usage is not None, "Expected usage metadata in proxy response"
    assert usage.prompt_tokens > 0, (
        f"Expected prompt_tokens > 0, got {usage.prompt_tokens}"
    )
    assert usage.completion_tokens > 0, (
        f"Expected completion_tokens > 0, got {usage.completion_tokens}"
    )
