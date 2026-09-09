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


# ── A spend cap is not a malformed request ────────────────────────────────────
#
# REPORTED: every Development turn came back "Agent error: BadRequestError" while the
# Orchestrator, on the same tenant, worked. The provider had already given the reason
# and the date it lifts —
#
#   "You have reached your specified workspace API usage limits. You will regain
#    access on 2026-10-01 at 00:00 UTC."
#
# — and both the reply and the log line reduced it to the class name. `BadRequestError`
# is also the type a genuinely malformed request arrives under, so the type map
# answered "usually a model configuration problem", which is the wrong action: it sends
# people into the agent code looking for a bug that is not there. The distinguishing
# information is only in the message, so the message is READ as a signal — and, per
# this module's rule, never returned.


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        # Anthropic, verbatim from the incident.
        "You have reached your specified workspace API usage limits. "
        "You will regain access on 2026-10-01 at 00:00 UTC.",
        # OpenAI / Azure.
        "insufficient_quota: You exceeded your current quota, please check your plan.",
        # Anthropic, the other half of the same story.
        "Your credit balance is too low to access the Anthropic API.",
    ],
)
def test_a_spend_cap_is_named_as_one_rather_than_as_a_bad_request(message):
    from shared.services.model_errors import friendly_model_error

    out = friendly_model_error(_exc("BadRequestError", message))

    assert "usage or spend limit" in out
    assert "retrying will not help" in out
    # The action that actually fixes it, and the one place to do it.
    assert "raise the limit" in out and "Model Management" in out
    # The old answer, which pointed at the wrong thing entirely.
    assert "malformed" not in out


@pytest.mark.unit
def test_the_spend_cap_sentence_still_never_echoes_the_exception():
    """The signal is read, not repeated. A provider message can carry the tenant's key."""
    from shared.services.model_errors import friendly_model_error

    leaky = "insufficient_quota for key sk-ant-api03-SECRETVALUE on workspace foo"
    out = friendly_model_error(_exc("BadRequestError", leaky))

    assert "SECRETVALUE" not in out
    assert "sk-ant" not in out
    assert "usage or spend limit" in out


@pytest.mark.unit
def test_an_ordinary_bad_request_is_still_answered_as_a_bad_request():
    """Guards the guard: if the signal matched everything, the case above proves nothing."""
    from shared.services.model_errors import friendly_model_error

    out = friendly_model_error(_exc("BadRequestError", "tools: Tool names must be unique."))
    assert "malformed" in out
    assert "usage or spend limit" not in out


@pytest.mark.unit
def test_the_development_agent_no_longer_replies_with_a_class_name():
    """It was the last agent still doing it — the one the incident was reported on."""
    import importlib
    import inspect

    src = inspect.getsource(
        importlib.import_module("agents_orchestrator.development_agent.agents.dev_agent")
    )
    assert 'content=f"Agent error: {type(e).__name__}"' not in src
    assert "friendly_model_error" in src
    for leak in ('content=f"Agent error: {e}"', "content=str(e)", 'content=f"{e}"'):
        assert leak not in src
