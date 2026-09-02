"""A failed model call must say what to DO about it.

From a live Design chat: an Azure gpt-5-mini deployment in swedencentral hit its rate
limit, the wrapper retried three times, and the entire reply the user received was

    Agent error: RateLimitError

Safe and true and useless. It does not say the limit is the provider's and temporary,
that nothing they did caused it, that waiting fixes it, or that another model would
work right now. A class name is a log line, not an answer.

The safety property it existed for is real and is kept: a BYOK provider error can echo
the tenant's own API key, so `str(exc)` never reaches the user.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _exc(name: str, msg: str = "boom") -> Exception:
    return type(name, (Exception,), {})(msg)


@pytest.mark.unit
def test_a_rate_limit_says_it_is_temporary_and_offers_a_way_forward():
    from shared.services.model_errors import friendly_model_error

    out = friendly_model_error(_exc("RateLimitError"))
    assert "rate limiting" in out
    assert "temporary" in out
    assert "switch to a different model" in out
    assert "RateLimitError" not in out  # the class name is not the message


@pytest.mark.unit
def test_an_auth_failure_points_at_the_administrator_not_the_user():
    from shared.services.model_errors import friendly_model_error

    out = friendly_model_error(_exc("AuthenticationError"))
    assert "administrator" in out
    assert "Model Providers" in out


@pytest.mark.unit
def test_a_context_overflow_tells_the_user_to_start_a_new_chat():
    from shared.services.model_errors import friendly_model_error

    out = friendly_model_error(_exc("ContextWindowExceededError"))
    assert "new chat" in out


@pytest.mark.unit
def test_the_provider_message_is_never_echoed():
    """THE SAFETY PROPERTY. A BYOK error can carry the tenant's own key."""
    from shared.services.model_errors import friendly_model_error

    secret = "sk-ant-super-secret-value"
    for name in ("RateLimitError", "AuthenticationError", "SomethingUnheardOf"):
        out = friendly_model_error(_exc(name, f"failed with key {secret}"))
        assert secret not in out


@pytest.mark.unit
def test_an_unknown_failure_still_reports_its_type():
    """The old behaviour for everything, kept as the fallback — an unrecognised error
    the user can quote to us beats a shrug."""
    from shared.services.model_errors import friendly_model_error

    out = friendly_model_error(_exc("WidgetExplodedError"))
    assert "WidgetExplodedError" in out
    assert "try again" in out.lower()


@pytest.mark.unit
def test_a_wrapped_cause_is_unwrapped_one_level():
    """Retry wrappers nest the real cause; the outer type is often a generic wrapper
    whose name says nothing."""
    from shared.services.model_errors import friendly_model_error

    inner = _exc("RateLimitError")
    outer = _exc("RetryError")
    outer.__cause__ = inner
    assert "rate limiting" in friendly_model_error(outer)


@pytest.mark.unit
@pytest.mark.parametrize(
    "module",
    [
        "agents_orchestrator.design_architecture_agent.agents.architecture",
        "agents_orchestrator.requirements_agent.agents.planning",
    ],
)
def test_neither_agent_replies_with_a_bare_class_name(module):
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    assert 'content=f"Agent error: {type(e).__name__}"' not in src
    assert "friendly_model_error" in src


@pytest.mark.unit
@pytest.mark.parametrize(
    "module",
    [
        "agents_orchestrator.design_architecture_agent.agents.architecture",
        "agents_orchestrator.requirements_agent.agents.planning",
    ],
)
def test_neither_agent_interpolates_the_raw_model_exception(module):
    """The rule that produced the class-name reply in the first place, still enforced."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    for leak in ('content=f"Agent error: {e}"', 'content=str(e)', 'content=f"{e}"'):
        assert leak not in src
