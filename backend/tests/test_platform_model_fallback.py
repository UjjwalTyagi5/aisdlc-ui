"""The platform's own API key is never spent by accident.

CARRIED DEBT #5, audited. The recorded note said the "no env fallback" guarantee was
"one layer deep" — `orchestrator2/dispatch.py` reads no env key, but the agents beneath
it were unaudited. The audit result is narrower and worse than the note implied:

  · Only the TESTING agent ever builds a model from `ANTHROPIC_API_KEY`
    (`testing_agent/config/shared.py` and `testing_agent/agents/testing_agent.py`).
    Every other agent resolves through `resolve_chat_model`, which fails closed.
  · Both sites fire only when the run's resolved model is missing from the contextvar
    — which their own comments say happens, because each node re-enters asyncio in an
    executor thread and the contextvar does not follow.
  · The guard was `AGENT_RUNTIME_MODE == "enterprise" or not ANTHROPIC_API_KEY`. On
    THIS deployment the mode is `local` and the key IS set, so the fallback was live:
    a dropped contextvar mid-run spent the PLATFORM's key instead of the project's,
    bypassing the project's grant and its budget, silently and with a normal-looking
    answer.

So the fallback is now opt-in. `ALLOW_PLATFORM_MODEL_FALLBACK` defaults to false, which
makes a BYOK deployment fail closed; a developer with no provider configured can turn it
on deliberately. An escape hatch you have to ask for is not the same thing as one that
fires on its own.
"""
from __future__ import annotations

import inspect

import pytest


def _fallback_sources() -> list[str]:
    from agents_orchestrator.testing_agent.agents import testing_agent
    from agents_orchestrator.testing_agent.config import shared

    return [inspect.getsource(shared), inspect.getsource(testing_agent)]


def test_only_the_testing_agent_builds_a_model_from_the_env_key():
    """The audit's result, pinned. If another agent grows an env-key fallback, this
    fails and somebody has to decide about it deliberately."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "agents_orchestrator"
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "api_key=ANTHROPIC_API_KEY" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(root)).replace("\\", "/"))
    assert sorted(offenders) == [
        "testing_agent/agents/testing_agent.py",
        "testing_agent/config/shared.py",
    ], offenders


def test_build_llm_fails_closed_when_the_flag_is_off(monkeypatch):
    """BEHAVIOURAL, not a source check.

    The first version of this test asserted the flag's NAME appeared in the module —
    which the import line satisfies. Deleting the actual guard left all thirteen tests
    green. A source assertion that a mere import can satisfy is not a test.

    `build_llm`'s own docstring says "There is NO platform fallback — fail CLOSED when
    nothing is resolved". That was untrue until this change; now it is true by default.
    """
    from shared.services import model_resolver as mr

    from agents_orchestrator.testing_agent.config import shared

    monkeypatch.setattr(shared, "ALLOW_PLATFORM_MODEL_FALLBACK", False, raising=False)
    monkeypatch.setenv("ALLOW_PLATFORM_MODEL_FALLBACK", "")
    mr.set_resolved_model(None)
    try:
        with pytest.raises(RuntimeError, match="No BYOK model resolved"):
            shared.build_llm()
    finally:
        mr.set_resolved_model(None)


def test_build_llm_still_works_when_the_flag_is_deliberately_on(monkeypatch):
    """The other half. A guard that refuses even when asked is not opt-in, it is a
    removal — and a developer with no provider configured would have no way to work."""
    from shared.services import model_resolver as mr

    from agents_orchestrator.testing_agent.config import shared

    monkeypatch.setenv("ALLOW_PLATFORM_MODEL_FALLBACK", "true")
    mr.set_resolved_model(None)
    try:
        import importlib

        from config import env
        importlib.reload(env)
        if not env.ANTHROPIC_API_KEY:
            pytest.skip("no ANTHROPIC_API_KEY in this environment to fall back to")
        llm = shared.build_llm()
        assert llm is not None
    finally:
        mr.set_resolved_model(None)
        monkeypatch.delenv("ALLOW_PLATFORM_MODEL_FALLBACK", raising=False)
        import importlib

        from config import env
        importlib.reload(env)


def test_the_flag_defaults_to_off():
    """A BYOK deployment must fail closed without anyone having configured anything.
    A default of true would make the fix a no-op for exactly the deployments that need
    it, and nobody would notice — the symptom is a working answer."""
    import importlib

    from config import env

    importlib.reload(env)
    assert env.ALLOW_PLATFORM_MODEL_FALLBACK is False


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True),
    ("false", False), ("0", False), ("", False), (None, False),
])
def test_the_flag_is_read_the_same_way_everywhere(monkeypatch, value, expected):
    import importlib

    from config import env

    if value is None:
        monkeypatch.delenv("ALLOW_PLATFORM_MODEL_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("ALLOW_PLATFORM_MODEL_FALLBACK", value)
    importlib.reload(env)
    assert env.ALLOW_PLATFORM_MODEL_FALLBACK is expected
    monkeypatch.delenv("ALLOW_PLATFORM_MODEL_FALLBACK", raising=False)
    importlib.reload(env)


def test_enterprise_still_fails_closed_regardless_of_the_flag():
    """The flag LOOSENS a check; it must not be able to loosen the enterprise one.
    Read off the source because constructing the enterprise path here would mean
    reloading half the config."""
    for src in _fallback_sources():
        assert 'AGENT_RUNTIME_MODE == "enterprise"' in src, (
            "the enterprise guard must survive alongside the new flag"
        )


def test_the_refusal_names_what_to_do_about_it():
    """A turn that fails closed must say why. 'No BYOK model resolved' with no next
    step is the kind of error that gets worked around by setting the env key."""
    for src in _fallback_sources():
        assert "Model Providers" in src or "administrator" in src
