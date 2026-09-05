import pytest

from agents_orchestrator.orchestrator2.registry import AGENT_IDS
from agents_orchestrator.orchestrator2.router import DISPLAY_NAMES, prefilter


@pytest.mark.parametrize(
    "text,expected",
    [
        ("run the testing agent", "testing"),
        ("switch to the security agent", "security"),
        ("use the project manager agent", "plan"),
        ("open the code review agent", "code_review"),
    ],
)
def test_unambiguous_commands_short_circuit(text, expected):
    """These cost no model call and must be perfectly predictable."""
    assert prefilter(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I need a PRD",                      # real intent, no agent named -> the LLM's job
        "document this function",            # mentions docs, but is in-agent work
        "what did the security agent find?", # a question ABOUT an agent, not a request to run it
        "",
    ],
)
def test_ambiguous_text_defers_to_the_model(text):
    """The old router's whole failure was matching aliases anywhere in the text.
    'I need a PRD' names no agent and must reach the LLM; 'document this function'
    names one and must NOT route, because it is a request to the CURRENT agent."""
    assert prefilter(text) is None


def test_the_project_manager_agent_is_never_called_plan_to_users():
    """The id is `plan`; the name is 'Project Manager agent'. Users type the name."""
    assert prefilter("run the project manager agent") == "plan"


# ── every agent must be reachable ─────────────────────────────────────────────


def test_every_agent_has_a_display_name():
    """An agent in AGENT_IDS with no display name is unreachable by name — the
    silent gap `registry.py` exists to make impossible. Adding a tenth agent must
    break this test (and the import assert), not a user's routing."""
    missing = [agent_id for agent_id in AGENT_IDS if agent_id not in DISPLAY_NAMES]
    assert not missing, f"agents in AGENT_IDS with no display name: {missing}"
    assert set(DISPLAY_NAMES) == set(AGENT_IDS)


def test_the_project_manager_agent_is_not_named_plan_or_pm_in_display_names():
    """`plan` is the Project Manager agent in every user-facing string."""
    assert DISPLAY_NAMES["plan"] == "Project Manager"


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_every_agent_routes_by_its_display_name(agent_id):
    """Derived from AGENT_IDS, so a new agent cannot silently miss coverage."""
    assert prefilter(f"run the {DISPLAY_NAMES[agent_id]} agent") == agent_id


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_every_agent_routes_by_its_raw_id(agent_id):
    """`code_review` / `code-review` normalise to the same words as the display name."""
    assert prefilter(f"switch to the {agent_id} agent") == agent_id


# ── adversarial: the cases that must NOT route ────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # A negation. Firing here would do the exact OPPOSITE of what was asked —
        # the single worst outcome available to this function. It costs one model
        # call to defer and one wrong agent run to guess, so it defers. Note the
        # rule does no negation detection at all: requiring the command to BE the
        # whole message means "don't ..." simply never reaches the verb.
        "don't run the testing agent",
        "do not run the testing agent",
        "please don't run the testing agent",
        "no need to run the testing agent",
        # Past tense, a statement of fact rather than a request. There is no verb
        # in command position, so nothing matches.
        "the security agent already ran",
        "the security agent has already run",
        "i already ran the testing agent",
        # A verb but no agent named — there is nothing to route TO.
        "run the agent",
        "run it",
        "switch to the other agent",
        # Two agents named. This function returns ONE id and cannot express
        # "testing, then security"; picking the first would be a silent guess.
        "run the testing agent and the security agent",
        "run the testing agent then the deployment agent",
        "should i use the design agent or the development agent",
        # Questions about an agent, not orders to run one.
        "run the testing agent?",
        "can you tell me what the security agent does",
        "why did the deployment agent fail",
        # In-agent work that names an agent's word. This is the exact class of
        # false positive the old alias-anywhere router produced.
        "document this function",
        "run the tests",
        "review this code",
        "deploy to staging",
        "add a security header",
        # The command buried inside a larger sentence: real intent, but the
        # surrounding clause changes it (a condition, a hypothetical, a report).
        "when the build is green, run the testing agent",
        "if we run the testing agent it will take an hour",
        "i was wondering whether to run the testing agent",
        # No agent named at all — the message the old router could not handle and
        # the whole reason a Context Agent exists.
        "I need a PRD",
        "help me plan the sprint",
    ],
)
def test_adversarial_text_defers_to_the_model(text):
    assert prefilter(text) is None


# ── shape of an accepted command ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("RUN THE TESTING AGENT", "testing"),
        ("  run   the    testing   agent  ", "testing"),
        ("run the testing agent.", "testing"),
        ("please run the testing agent", "testing"),
        ("run the testing agent please", "testing"),
        ("open the code-review agent", "code_review"),
        ("go to the documentation agent", "documentation"),
        ("hand off to the deployment agent", "deployment"),
        ("use the project manager agent", "plan"),
    ],
)
def test_accepted_command_shapes(text, expected):
    """Casing, spacing, a trailing full stop and bare politeness carry no intent,
    so stripping them cannot turn a non-command into a command."""
    assert prefilter(text) == expected


def test_the_literal_word_agent_is_required():
    """"run testing" is a plausible order to the agent already working; only
    "run the testing agent" is unambiguously a hand-off."""
    assert prefilter("run testing") is None
    assert prefilter("use design") is None
    assert prefilter("run the testing agent") == "testing"


# ── it must never raise ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [None, 0, 3.14, [], {}, object(), b"run the testing agent", "   ", "\n\t", "?!;", "\x00"],
)
def test_odd_input_returns_none_and_never_raises(value):
    """Odd input is handled, and this test can now say BY WHAT.

    It used to prove nothing. `prefilter` wrapped its whole body in
    `except Exception: return None`, so every value here returned `None` whether the
    `isinstance` guard existed or not — delete the guard and the test still passed.
    With the blanket catch gone the guard is the only thing standing between
    `prefilter(None)` and `AttributeError`, so removing it fails this test.
    """
    assert prefilter(value) is None


def test_an_internal_fault_surfaces_instead_of_being_routed_away(monkeypatch):
    """The other half of removing that catch, pinned so it cannot creep back.

    A broken `_COMMAND` or a broken `_NAME_TO_ID` must not be laundered into `None`.
    `None` means "not an unambiguous command, let the Context Agent read it" — a
    perfectly normal answer — so a bug that always returns it is a router that
    silently stops routing and looks entirely healthy while doing it. That is the
    same class of silent failure this engine was rebuilt to remove, and `ws.py`
    already turns an exception in a turn into a typed `error` the user can see.
    """
    from agents_orchestrator.orchestrator2 import router

    class _BrokenPattern:
        def match(self, _text):
            raise RuntimeError("regex regression")

    monkeypatch.setattr(router, "_COMMAND", _BrokenPattern())

    with pytest.raises(RuntimeError, match="regex regression"):
        router.prefilter("run the testing agent")


def test_very_long_input_is_handled():
    assert prefilter("run the testing agent " * 5000) is None
    assert prefilter("x" * 100_000) is None
