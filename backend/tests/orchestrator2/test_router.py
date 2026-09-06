import ast
import inspect
import re

import pytest

from agents_orchestrator.orchestrator2 import registry as reg, router
from agents_orchestrator.orchestrator2.registry import AGENT_IDS
from agents_orchestrator.orchestrator2.router import DISPLAY_NAMES, prefilter
from shared.services import model_resolver as mr


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
    same class of silent failure this engine was rebuilt to remove. `ws.py`'s turn loop
    renders an exception raised while serving a turn as a typed `error` the user can
    see, which is where this lands once `prefilter` is called from inside it — a
    property of the call site, not one this function can guarantee alone.
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


# ═══════════════════════════════════════════════════════════════════════════════
# The Context Agent's routing decision
#
# The pre-filter above answers "is this an explicit command?". Everything below is
# the other 95% of turns: `route()` reads the message and picks one of the nine
# agents by MEANING, or answers directly. The engine this replaces could not do
# this at all — the retired `stage_switch.py` matched an alias anywhere in the text, so
# "I need a PRD" routed nowhere and the user had to name the agent by hand.
# ═══════════════════════════════════════════════════════════════════════════════


def _a_resolved_model(model="claude-sonnet-4-5"):
    return mr.ResolvedModel(
        provider="anthropic", litellm_provider="anthropic", model=model,
        api_key="sk-this-projects-key", base_url=None, alias="tenant:t1:prov-1",
    )


class _FakeAIMessage:
    """The shape `guarded_completion` returns: a LangChain AIMessage has `.content`
    and `.tool_calls` (already parsed into {name, args, id} dicts)."""

    def __init__(self, *, content="", tool_calls=None):
        self.content = content
        self.tool_calls = list(tool_calls or [])


class _Recorder:
    def __init__(self):
        self.resolve_positional = None
        self.resolve_kwargs = None
        self.run_project_contextvar = "<never resolved>"
        self.llm_kwargs = None
        self.bound_tools = None
        self.messages = None
        self.guarded_kwargs = None


def _install(monkeypatch, *, response=None, resolve_raises=None):
    """Wire `_ask_model`'s three real collaborators — the resolver, the client
    builder and `guarded_completion` — to a recorder, so the LLM call is exercised
    end to end without a network call or a 7-second litellm import."""
    rec = _Recorder()

    async def _fake_resolve(tenant_id, requested_model_id=None, **kwargs):
        rec.resolve_positional = (tenant_id, requested_model_id)
        # Read the contextvar the REAL resolver falls back to when no project_id is
        # passed. If the router were leaning on ambient state instead of passing the
        # id, this is where that would show.
        rec.run_project_contextvar = mr.get_run_project()
        rec.resolve_kwargs = kwargs
        if resolve_raises is not None:
            raise resolve_raises
        return _a_resolved_model()

    class _FakeLLM:
        def bind_tools(self, tools):
            rec.bound_tools = tools
            return self

    def _fake_build(resolved):
        rec.llm_kwargs = router._llm_kwargs(resolved)
        return _FakeLLM()

    async def _fake_guarded(resolved, chat_model, messages, **kwargs):
        rec.messages = messages
        rec.guarded_kwargs = kwargs
        return response if response is not None else _FakeAIMessage(content="ok")

    monkeypatch.setattr(router, "resolve_model_for_run", _fake_resolve)
    monkeypatch.setattr(router, "_build_llm", _fake_build)
    monkeypatch.setattr(router, "guarded_completion", _fake_guarded)
    return rec


async def _route(**overrides):
    kwargs = dict(history=[], run_id="run-1", tenant_id="t1", project_id="proj-1",
                  model_id=None, offering_id=None)
    kwargs.update(overrides)
    text = kwargs.pop("text", "I need a PRD")
    return await router.route(text, **kwargs)


async def _unused_ask_model(*a, **k):
    raise AssertionError("the model must not be called here")


@pytest.fixture(autouse=True)
def _clean_run_project():
    """`resolve_model_for_run` falls back to this contextvar. A value left behind by
    another test would let a router that DOESN'T pass project_id pass anyway."""
    mr.set_run_project(None)
    yield
    mr.set_run_project(None)


# ── the three behaviours the phase exists for ────────────────────────────────


@pytest.mark.asyncio
async def test_prefilter_short_circuits_without_a_model_call(monkeypatch):
    """An unambiguous command must not spend a model call."""
    from agents_orchestrator.orchestrator2 import router

    called = False

    async def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("the model must not be called here")

    monkeypatch.setattr(router, "_ask_model", _boom)
    d = await router.route("run the testing agent", history=[], run_id="r", tenant_id="t",
                           project_id="p", model_id=None, offering_id=None)
    assert d.agent_id == "testing"
    assert called is False


@pytest.mark.asyncio
async def test_intent_without_an_agent_name_reaches_the_model(monkeypatch):
    """'I need a PRD' contains no agent alias. The old router could not route it;
    that limitation is the whole reason this phase exists."""
    from agents_orchestrator.orchestrator2 import router

    async def _fake(*a, **k):
        return router.RoutingDecision(agent_id="requirements", reason="asked for a PRD",
                                      direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", _fake)
    d = await router.route("I need a PRD", history=[], run_id="r", tenant_id="t",
                           project_id="p", model_id=None, offering_id=None)
    assert d.agent_id == "requirements"


@pytest.mark.asyncio
async def test_a_model_naming_an_unknown_agent_is_refused(monkeypatch):
    """The router's tools are generated FROM the registry, so this should be
    impossible — but a model can hallucinate, and a bad id must not reach dispatch."""
    from agents_orchestrator.orchestrator2 import router

    async def _fake(*a, **k):
        return router.RoutingDecision(agent_id="marketing", reason="x", direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", _fake)
    d = await router.route("do the thing", history=[], run_id="r", tenant_id="t",
                           project_id="p", model_id=None, offering_id=None)
    assert d.agent_id is None
    assert d.direct_reply  # says it could not choose, rather than silently doing nothing


# ── a pre-filter hit is announced as what it is ──────────────────────────────


@pytest.mark.asyncio
async def test_a_prefilter_hit_says_it_was_an_explicit_command(monkeypatch):
    """`reason` is shown to the user, so it has to say WHY this agent — and for a
    pre-filter hit the honest answer is 'because you named it', not a guess at
    intent the pre-filter never formed."""
    monkeypatch.setattr(router, "_ask_model", _unused_ask_model)
    d = await _route(text="use the project manager agent")
    assert d.agent_id == "plan"
    assert d.direct_reply is None
    assert "Project Manager" in d.reason
    assert "Plan agent" not in d.reason and "PM agent" not in d.reason


# ── the tool list is GENERATED, so it cannot offer an unrunnable agent ───────


def _tool_names(specs):
    return {s["function"]["name"] for s in specs}


def test_the_tool_list_covers_exactly_the_registry():
    """One tool per agent the engine can actually run — not a hand-typed list that
    can drift from it."""
    assert _tool_names(router._tool_specs()) == {
        f"{router._TOOL_PREFIX}{agent_id}" for agent_id in reg.REGISTRY
    }


def test_the_tool_list_cannot_offer_an_agent_the_engine_cannot_run(monkeypatch):
    """The load-bearing half: DERIVED, not copied. Take an agent out of the registry
    and the router stops offering it in the same breath. A hand-typed list would
    keep offering `security` here, and dispatch would then fail on a choice the
    router had presented as available — the old engine's undispatchable `plan`
    agent, rebuilt."""
    registry_without_security = {k: v for k, v in reg.REGISTRY.items() if k != "security"}
    monkeypatch.setattr(router, "REGISTRY", registry_without_security)

    names = _tool_names(router._tool_specs())
    assert f"{router._TOOL_PREFIX}security" not in names
    assert len(names) == len(AGENT_IDS) - 1


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_every_tool_carries_its_display_name_and_a_description(agent_id):
    """A length check alone does not test this. The first version of this assertion was
    `len(description) > len(display_name) + 20`, and the fixed boilerplate around the
    interpolations ("The  agent: . Call this when the user's message asks for that
    work.") clears that bar on its own for every display name here — so blanking a
    capability to "" passed. What has to be true is that the DESCRIPTION carries the
    CAPABILITY TEXT.

    The boilerplate's length is measured below rather than quoted, because the last
    quoted figure (62) was wrong by five characters and nothing noticed."""
    spec = next(s["function"] for s in router._tool_specs()
                if s["function"]["name"] == f"{router._TOOL_PREFIX}{agent_id}")
    capability = router._CAPABILITIES[agent_id]
    display_name = DISPLAY_NAMES[agent_id]

    # The invariant, asserted first so it is what a failure reports.
    assert capability in spec["description"], (
        f"{agent_id} is offered to the model without its capability text: "
        f"{spec['description']!r}"
    )
    assert display_name in spec["description"]
    assert "reason" in spec["parameters"]["properties"]

    # And the docstring's argument, measured rather than quoted, so it cannot go stale
    # the way the previous "62 characters" did. Only meaningful once the two assertions
    # above hold, hence last.
    boilerplate = len(spec["description"]) - len(capability) - len(display_name)
    assert boilerplate > len(display_name) + 20, (
        f"the boilerplate no longer clears the old `len(desc) > len(name) + 20` bar "
        f"on its own, so this docstring's account of why that rule was useless needs "
        f"rewriting (boilerplate {boilerplate}, name {len(display_name)})"
    )


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_no_agent_is_described_by_nothing(agent_id):
    """Every agent's capability text is non-blank and clears the floor.

    Say plainly what this can and cannot catch, since the previous docstring did not.
    Blanking an entry in `_CAPABILITIES` fires the import-time assert, so this test
    never runs and pytest reports a COLLECTION ERROR — which reads as a broken test file
    rather than as a broken invariant. The rule itself is tested directly, without
    breaking the module, by `test_the_capability_floor_rejects_a_degenerate_entry`."""
    capability = router._CAPABILITIES[agent_id]
    assert isinstance(capability, str)
    assert capability.strip()
    assert len(capability.strip()) >= router._MIN_CAPABILITY_CHARS


def test_the_capability_floor_rejects_a_degenerate_entry():
    """The floor's RULE, exercised against a broken table instead of against the real
    one — so weakening it is a red test, not a collection error.

    The number is hardcoded here on purpose. Every other assertion about the floor
    imports `_MIN_CAPABILITY_CHARS` and compares it to itself, so setting the constant
    to 0 was invisible for any non-blank string: measured, `_MIN_CAPABILITY_CHARS = 0`
    with `{"security": "does stuff"}` left the whole file green."""
    assert router._MIN_CAPABILITY_CHARS >= 40, (
        "the floor is the smoke check that rejects '' and 'does stuff'; lowering it "
        "silently is how those come back"
    )
    assert router._undescribed({"security": "", "plan": router._CAPABILITIES["plan"]}) == [
        "security"
    ]
    assert router._undescribed({"security": "does stuff"}) == ["security"]
    assert router._undescribed({"security": None}) == ["security"]
    assert router._undescribed(router._CAPABILITIES) == []


def test_the_capability_floor_cannot_tell_filler_from_a_description():
    """The limit of the floor, pinned so the comment beside it stays honest.

    Content-free boilerplate over the floor passes, and must be understood to pass:
    what actually keeps `_CAPABILITIES` useful is that the text REACHES the model
    (`test_every_tool_carries_its_display_name_and_a_description` and
    `test_the_roster_offers_no_agent_as_a_bare_name`), not its character count. If this
    test ever fails because the floor grew teeth, delete it and say so — but do not let
    a comment claim the floor enforces content while this passes."""
    filler = "handles whatever needs handling in this area of the project"
    assert len(filler) > router._MIN_CAPABILITY_CHARS
    assert router._undescribed({"security": filler}) == []


def test_the_project_manager_agent_is_named_correctly_to_the_model():
    """`plan` is the Project Manager agent in every user-facing string, and the tool
    description is user-facing by proxy: it is what the model echoes back."""
    spec = next(s["function"] for s in router._tool_specs()
                if s["function"]["name"] == f"{router._TOOL_PREFIX}plan")
    assert "Project Manager" in spec["description"]
    assert "Plan agent" not in spec["description"]
    assert "PM agent" not in spec["description"]


# ── the routing prompt ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "banned",
    ["next agent", "next stage", "sign-off", "sign off", "advance to",
     "progress to", "previous stage", "handoff gate"],
)
def test_the_routing_prompt_has_no_positional_progression_language(banned):
    """There is no ordering in this engine. Every turn considers all nine agents;
    'the next agent' meant 'the next item in a list' in the engine this replaces,
    and that is exactly the behaviour being removed."""
    assert banned not in router._system_prompt().lower()


def _advertised_agent_ids(prompt):
    """Every agent id the prompt offers the model, found anywhere in it.

    THE ADVERTISED TOOL NAME IS WHAT THE MODEL ROUTES ON, so this is the property that
    matters and it is independent of prose: a `route_to_*` is equally routable in a
    roster bullet, in a differently-shaped bullet, or in a sentence of guidance.
    """
    return {
        name.removeprefix(router._TOOL_PREFIX)
        for name in re.findall(rf"{re.escape(router._TOOL_PREFIX)}\w+", prompt)
    }


def _roster_lines(prompt):
    """The roster block's lines. Every roster entry begins '- The <name> agent'; the
    prompt's other bullets begin '- Route', '- Call', '- Never', '- There', '- Answer'.

    This pins the TEMPLATE'S SHAPE — that the roster is nine bullets in the form the
    rest of these tests parse. It is deliberately NOT what enforces the no-phantom
    invariant any more: a prefix match sees only phantoms that happen to be written in
    the current bullet shape, and `- Marketing agent (route_to_marketing): ...` (one
    word shorter) or a `route_to_marketing` in the prompt's prose both walked straight
    past it. `_advertised_agent_ids` is what closes those.
    """
    return [line for line in prompt.splitlines() if line.startswith("- The ")]


def _roster_line_for(prompt, agent_id):
    """The one prompt line advertising `agent_id`'s tool, found by the TOOL NAME rather
    than by a bullet prefix, so rewording the line around it does not hide it."""
    marker = f"({router._TOOL_PREFIX}{agent_id})"
    lines = [line for line in prompt.splitlines() if marker in line]
    assert len(lines) == 1, f"expected exactly one line advertising {marker}: {lines}"
    return lines[0]


def test_the_prompt_names_no_routing_tool_outside_the_registry():
    """The load-bearing anti-phantom assertion, and the shape-independent one.

    A phantom agent advertised to the model costs a turn: it gets chosen, `_validated`
    refuses the id (see `test_a_hallucinated_tool_name_never_reaches_dispatch`), and the
    user gets "I could not work out which agent should handle that" instead of work.

    Its predecessor keyed on the literal line prefix `- The `, so it caught the phantom
    only in the shape the template happens to use today. Measured: a phantom written as
    `- Marketing agent (route_to_marketing): ...`, and a phantom in the prompt's prose
    body, both passed the whole suite. This compares the tool ids the prompt names
    ANYWHERE against `REGISTRY`, so neither can."""
    advertised = _advertised_agent_ids(router._system_prompt())
    assert advertised == set(reg.REGISTRY), (
        f"the prompt advertises {sorted(advertised)}, the registry holds "
        f"{sorted(reg.REGISTRY)}"
    )


def test_the_routing_prompt_roster_is_exactly_the_registry_no_more():
    """The roster block itself, pinned as nine bullets of the expected shape.

    This is the template check, not the invariant check — a phantom outside this shape
    is `test_the_prompt_names_no_routing_tool_outside_the_registry`'s job. What this
    still buys is the COUNT: a roster line carrying no `(route_to_x)` at all is
    invisible to a set comparison over tool ids and perfectly visible to the model."""
    prompt = router._system_prompt()
    lines = _roster_lines(prompt)

    listed = {
        line.split("(", 1)[1].split(")", 1)[0].removeprefix(router._TOOL_PREFIX)
        for line in lines
        if "(" in line and ")" in line
    }
    assert listed == set(reg.REGISTRY), (
        f"the roster advertises {sorted(listed)}, the registry holds "
        f"{sorted(reg.REGISTRY)}"
    )
    assert len(lines) == len(reg.REGISTRY), (
        f"{len(lines)} roster lines for {len(reg.REGISTRY)} agents: {lines}"
    )
    assert "Plan agent" not in prompt


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_the_roster_offers_no_agent_as_a_bare_name(agent_id):
    """The roster is where the model reads what an agent is FOR, so the capability text
    has to REACH it — the same reach `test_every_tool_carries_its_display_name_and_a_
    description` asserts for the tool list, which the roster did not have.

    Measured: reducing the roster to `- The X agent (route_to_x).` left the suite fully
    green while advertising all nine agents to the model as bare names — verbatim the
    failure `_CAPABILITIES`' header comment claims is prevented."""
    prompt = router._system_prompt()
    capability = router._CAPABILITIES[agent_id]
    line = _roster_line_for(prompt, agent_id)

    assert capability in line, (
        f"the roster offers {agent_id} as little more than a name: {line!r}"
    )
    assert DISPLAY_NAMES[agent_id] in line


def test_the_routing_prompt_cannot_describe_an_agent_the_registry_lacks(monkeypatch):
    """The same derivation proof the tool list has. `_tool_specs` had this test and
    `_system_prompt` did not, so a hand-typed roster — the more dangerous of the two,
    since the roster is what the model reads to decide an agent EXISTS — was
    unprotected."""
    registry_without_security = {k: v for k, v in reg.REGISTRY.items() if k != "security"}
    monkeypatch.setattr(router, "REGISTRY", registry_without_security)

    prompt = router._system_prompt()
    assert f"{router._TOOL_PREFIX}security" not in prompt
    assert "Security agent" not in prompt
    assert _advertised_agent_ids(prompt) == set(registry_without_security)
    assert len(_roster_lines(prompt)) == len(AGENT_IDS) - 1


def test_the_roster_and_the_tool_list_name_the_same_agents():
    """Two generated lists that must agree. They are built from the same iteration
    today; this is what notices if one of them stops being. Read off the prompt by tool
    id rather than by bullet shape, so a reworded roster still gets compared."""
    prompt_ids = _advertised_agent_ids(router._system_prompt())
    tool_ids = {
        spec["function"]["name"].removeprefix(router._TOOL_PREFIX)
        for spec in router._tool_specs()
    }
    assert prompt_ids == tool_ids


# ── BYOK: the router's own model call is project-scoped ──────────────────────


@pytest.mark.asyncio
async def test_the_router_resolves_its_model_with_the_runs_project_id(monkeypatch):
    """THE regression this task must not reintroduce.

    `resolve_model_for_run` uses `project_id` for two enforcement decisions —
    `effective_project_offerings` (which models this project was granted) and
    `check_budgets` (whose monthly cap this spend counts against). Both are BYPASSED
    when it is None. Dispatch was just fixed to pass it; a router that resolved
    without it would reopen the identical hole on a NEW code path, where the
    dispatch tests could not see it.
    """
    rec = _install(monkeypatch)
    await _route(project_id="proj-1", tenant_id="t1")

    assert rec.resolve_kwargs is not None, "the router never resolved a model"
    assert rec.resolve_kwargs.get("project_id") == "proj-1", (
        "the router's own LLM call must be scoped to the run's project, exactly as "
        f"dispatch.run_agent does; saw {rec.resolve_kwargs!r}"
    )
    assert rec.resolve_positional == ("t1", None)
    assert "offering_id" in rec.resolve_kwargs


@pytest.mark.asyncio
async def test_the_router_passes_the_project_id_rather_than_leaning_on_ambient_state(
    monkeypatch,
):
    """`resolve_model_for_run` also reads a `_RUN_PROJECT` contextvar when no
    project_id is passed. Relying on that would make the router correct only when
    something else happened to have set it first — and the router runs BEFORE
    dispatch, which is the thing that sets it. So the id must be explicit."""
    rec = _install(monkeypatch)
    await _route(project_id="proj-9")

    assert rec.run_project_contextvar is None, (
        "the router must not depend on a contextvar it never sets"
    )
    assert rec.resolve_kwargs.get("project_id") == "proj-9"


@pytest.mark.asyncio
async def test_a_project_with_no_usable_model_is_not_papered_over(monkeypatch):
    """No env-key fallback, and no fail-soft to `agent_id=None` either. A router
    that answered 'I could not choose' when the real problem is an unconfigured
    provider sends an administrator hunting the wrong bug. `ws.py`'s turn loop renders
    an exception raised while serving a turn as a typed `error` the user can see, which
    is where this lands once `route` is called from inside it — a property of the call
    site, not one this module can guarantee alone."""
    _install(monkeypatch, resolve_raises=mr.NoModelConfiguredError("none configured"))
    with pytest.raises(mr.NoModelConfiguredError):
        await _route()


def _module_code(module) -> str:
    """A module's source with every docstring and every comment stripped.

    Assertions about what code DOES must not be satisfiable — or DEFEATED — by prose
    about it: this module's docstring names the fallback it refuses to have.
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)  # ast.unparse drops comments outright


def test_the_router_has_no_env_key_fallback_anywhere():
    """`copilot_api._classify_switch` — the classifier this replaces — falls back to
    a hardcoded ANTHROPIC_MODEL + ANTHROPIC_API_KEY when resolution fails. In a
    deployed environment there IS no platform key, so a local success on one is a
    lie about production. Source-level, because the point is that no path reaches it."""
    code = _module_code(router)
    for banned in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "local-dev", "local-env"):
        assert banned not in code, (
            f"{banned} must not appear in the orchestrator2 router — resolution "
            f"fails closed, with no platform fallback"
        )


@pytest.mark.parametrize("func_name", ["route", "_ask_model"])
def test_project_id_is_keyword_required_with_no_default(func_name):
    """A default of None is how project scoping gets dropped silently at the next
    call site added. Omitting it must be a TypeError, not a quiet unscoped call."""
    param = inspect.signature(getattr(router, func_name)).parameters["project_id"]
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


# ── the client the router builds ─────────────────────────────────────────────


def test_the_client_is_built_with_the_projects_own_key():
    """ChatLiteLLM keeps a separate named field per provider, defaulted from the
    ENVIRONMENT, and it wins over the generic `api_key` — which is how agents
    authenticated with a stale platform key and reported the tenant's valid key as
    invalid. `litellm_key_kwargs` is what makes the BYOK key the one actually used."""
    kwargs = router._llm_kwargs(_a_resolved_model())
    assert kwargs["api_key"] == "sk-this-projects-key"
    assert kwargs["anthropic_api_key"] == "sk-this-projects-key"
    assert kwargs["model"] == "claude-sonnet-4-5"
    assert kwargs["custom_llm_provider"] == "anthropic"


def test_the_client_leaves_retrying_to_guarded_completion():
    """ChatLiteLLM's own tenacity retry wraps every call underneath
    guarded_completion's, uncoordinated: one call becomes up to 3x3 real requests at
    a provider that is already rate-limiting. Measured live (dev_agent._build_llm)."""
    assert router._llm_kwargs(_a_resolved_model())["max_retries"] == 0


def test_a_model_that_rejects_temperature_is_not_sent_one():
    """litellm raises UnsupportedParamsError BEFORE the call for the gpt-5 family and
    the newest Claude models, so a hardcoded temperature fails every routing turn for
    those tenants. `temperature_kwargs` is the measured list."""
    assert "temperature" not in router._llm_kwargs(_a_resolved_model("azure/gpt-5-mini"))
    assert "temperature" not in router._llm_kwargs(_a_resolved_model("claude-opus-4-8"))
    assert router._llm_kwargs(_a_resolved_model("claude-sonnet-4-5"))["temperature"] == 0.0


# ── what the model is actually asked ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_model_sees_the_system_prompt_the_history_and_the_new_message(
    monkeypatch,
):
    rec = _install(monkeypatch)
    await _route(
        text="and now?",
        history=[{"role": "user", "content": "we finished the PRD"},
                 {"role": "assistant", "content": "noted"}],
    )
    contents = [str(getattr(m, "content", m)) for m in rec.messages]
    assert contents[0] == router._system_prompt()
    assert "we finished the PRD" in contents
    assert contents[-1] == "and now?", "the message being routed must be the last turn"


@pytest.mark.asyncio
async def test_history_is_capped_but_the_new_message_always_survives(monkeypatch):
    """An unbounded history makes a cheap routing call arbitrarily expensive and can
    push the message being routed out of the model's attention entirely."""
    rec = _install(monkeypatch)
    history = [{"role": "user", "content": f"turn {i}"} for i in range(200)]
    await _route(text="route me", history=history)

    contents = [str(getattr(m, "content", m)) for m in rec.messages]
    assert len(rec.messages) <= router._HISTORY_LIMIT + 2  # + system + the new message
    assert contents[-1] == "route me"
    assert "turn 199" in contents and "turn 0" not in contents


@pytest.mark.asyncio
async def test_a_wrongly_shaped_history_entry_surfaces_instead_of_being_dropped(
    monkeypatch,
):
    """Routing on a silently truncated conversation is a mis-route, and a mis-route
    that looks healthy is the failure this engine was rebuilt to end."""
    _install(monkeypatch)
    with pytest.raises(TypeError):
        await _route(history=["just a string"])


@pytest.mark.asyncio
async def test_a_history_turn_whose_content_is_blocks_is_read_not_dropped(monkeypatch):
    """`content` as a list of typed blocks is what several providers store for a turn
    that carried an image or a tool result alongside its text, so it is at least as
    likely a real caller's shape as a bare string.

    This dropped the turn silently until this test existed: the check was
    `isinstance(content, str)`, and a block list fell through the `continue`. The
    routing decision was then made on a conversation missing a message, which is a
    mis-route wearing the face of a correct one — in the very function whose docstring
    argues against exactly that."""
    rec = _install(monkeypatch)
    await _route(
        text="and now?",
        history=[
            {"role": "user", "content": [{"type": "text", "text": "we finished the PRD"}]},
            {"role": "assistant", "content": "noted"},
        ],
    )
    contents = [str(getattr(m, "content", m)) for m in rec.messages]
    assert "we finished the PRD" in contents, (
        f"the block-list turn was dropped; the model saw {contents!r}"
    )
    assert contents[-1] == "and now?"


@pytest.mark.asyncio
async def test_history_content_of_an_unreadable_shape_raises(monkeypatch):
    """A string and a block list are the two shapes there is evidence for. Anything
    else is a caller bug, and guessing at it is how a turn goes missing."""
    _install(monkeypatch)
    with pytest.raises(TypeError):
        await _route(history=[{"role": "user", "content": 17}])


@pytest.mark.asyncio
@pytest.mark.parametrize("artefact_role", ["system", "tool"])
async def test_an_artefact_role_is_dropped_whatever_its_content(
    monkeypatch, artefact_role
):
    """`system` and `tool` are transcript machinery, not something a person wrote or
    read. Dropping them is deliberate — and they are dropped on ROLE, before their
    content is read, so an odd shape on a turn nobody routes on cannot fail a turn.

    Parametrised over `router._ARTEFACT_ROLES`' members BY NAME. The version of this
    test this replaces asserted only that `tool` was dropped, which was true of every
    role outside a four-entry whitelist — including `agent`, the role this platform
    actually stores every assistant reply under. A test that passes because a role fell
    through a whitelist certifies the whitelist, not the intent."""
    assert artefact_role in router._ARTEFACT_ROLES
    rec = _install(monkeypatch)
    await _route(
        text="route me",
        history=[{"role": artefact_role, "content": {"weird": "shape"}},
                 {"role": "user", "content": "the real turn"}],
    )
    contents = [str(getattr(m, "content", m)) for m in rec.messages]
    assert "the real turn" in contents
    assert not any("weird" in c for c in contents)


def test_the_dropped_roles_are_exactly_the_two_transcript_artefacts():
    """The set that may be silently dropped, pinned as a set.

    Everything in `conversation_messages.role`'s vocabulary
    (`shared/models/orm.py`: `user|agent|orchestrator|system|tool`) is either read or
    raises; only these two vanish. Widening `_ARTEFACT_ROLES` is how content starts
    disappearing again, and it must cost a red test rather than a code review."""
    assert router._ARTEFACT_ROLES == frozenset({"system", "tool"})
    # A role in both sets is dropped, because the artefact check runs first — so an
    # overlap is a silent drop wearing the face of a read. (Not a tautology: these are
    # three independently written sets.)
    assert router._ARTEFACT_ROLES.isdisjoint(router._USER_ROLES | router._ASSISTANT_ROLES)
    orm_vocabulary = {"user", "agent", "orchestrator", "system", "tool"}
    assert orm_vocabulary <= router._KNOWN_ROLES, (
        "a role this platform's schema allows is neither read nor named as an "
        f"artefact: {sorted(orm_vocabulary - router._KNOWN_ROLES)}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("assistant_role", ["agent", "orchestrator", "assistant", "ai"])
async def test_an_assistant_turn_is_read_under_every_spelling(
    monkeypatch, assistant_role
):
    """THE regression this round exists for. `agent` is how this platform spells an
    assistant turn — `shared/models/orm.py`'s role column is `user|agent|orchestrator|
    system|tool`, every `persist_turn` writer in the tree passes `"agent"`, and
    `shared/routers/runs.py:256` normalises the runs API to exactly `user`|`agent`.

    The router accepted only `assistant`/`ai`, so every assistant reply this platform
    has ever stored was dropped before the model saw it — and the previous round wrote
    a docstring calling those drops "neither of them content" and a test locking the
    behaviour in. Half the conversation went missing on every turn, silently, which is
    a mis-route wearing the face of a correct one."""
    rec = _install(monkeypatch)
    await _route(
        text="and now?",
        history=[{"role": "user", "content": "we need a PRD"},
                 {"role": assistant_role, "content": "I drafted the PRD."}],
    )
    contents = [str(getattr(m, "content", m)) for m in rec.messages]
    assert "I drafted the PRD." in contents, (
        f"a role {assistant_role!r} turn was dropped; the model saw {contents!r}"
    )
    kinds = [type(m).__name__ for m in rec.messages]
    assert "AIMessage" in kinds, f"read, but not as an assistant turn: {kinds}"


@pytest.mark.asyncio
async def test_a_real_get_transcript_conversation_survives_intact(monkeypatch):
    """The literal output shape of `shared.services.conversation_service.get_transcript`
    — the repo's only transcript source, and the mapping this module documents as
    supported. Built from that shape rather than from `assistant`, because a test using
    `assistant` proves nothing about a platform that never writes it.

    Four turns in, four turns the model sees. Before this round: two."""
    rec = _install(monkeypatch)
    transcript = [
        {"id": "1", "seq": 1, "role": "user", "author_id": "u-1",
         "content": "we need a PRD for the billing rework", "content_type": "markdown"},
        {"id": "2", "seq": 2, "role": "agent", "author_id": "requirements",
         "content": "I drafted the PRD; it covers scope and NFRs.",
         "content_type": "markdown"},
        {"id": "3", "seq": 3, "role": "user", "author_id": "u-1",
         "content": "good", "content_type": "markdown"},
        {"id": "4", "seq": 4, "role": "agent", "author_id": "design",
         "content": "Next I would move to architecture.", "content_type": "markdown"},
    ]
    await _route(text="and now?", history=transcript)

    contents = [str(getattr(m, "content", m)) for m in rec.messages]
    history_seen = contents[1:-1]  # drop the system prompt and the routed message
    assert history_seen == [t["content"] for t in transcript], (
        f"{len(history_seen)} of {len(transcript)} turns reached the model: "
        f"{history_seen!r}"
    )
    assert [type(m).__name__ for m in rec.messages[1:-1]] == [
        "HumanMessage", "AIMessage", "HumanMessage", "AIMessage"
    ]


@pytest.mark.asyncio
async def test_an_unrecognised_role_raises_rather_than_vanishing(monkeypatch):
    """A role in neither the read sets nor `_ARTEFACT_ROLES` is a caller this router
    has not been taught about, and dropping it is exactly how `agent` went missing for
    a whole round. The next entry added to that column's vocabulary must cost a visible
    failure, not a quietly shorter conversation."""
    _install(monkeypatch)
    with pytest.raises(TypeError, match="reviewer"):
        await _route(history=[{"role": "reviewer", "content": "looks fine to me"}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        {"role": "agent", "content": None},          # an explicit null
        {"role": "agent"},                            # no `content` key at all
        {"role": "agent", "content": None, "tool_calls": [{"id": "c1"}]},
    ],
)
async def test_a_turn_with_no_content_is_dropped_not_raised(monkeypatch, entry):
    """`content: None` is the canonical shape for an assistant turn that made only tool
    calls, and it is also what a mapping with no `content` key yields. That is an
    absence of text, not a shape we cannot read, so it joins the empty-text drop the
    docstring already owns — the alternative was a docstring saying such turns are
    dropped while the code killed the turn."""
    rec = _install(monkeypatch)
    await _route(text="route me",
                 history=[entry, {"role": "user", "content": "the real turn"}])
    contents = [str(getattr(m, "content", m)) for m in rec.messages]
    assert "the real turn" in contents
    assert contents[-1] == "route me"


@pytest.mark.asyncio
async def test_the_call_is_attributed_to_the_router_not_to_an_agent(monkeypatch):
    """Cost logs are read per agent_type. Billing the routing call to `development`
    would make one agent's spend permanently wrong."""
    rec = _install(monkeypatch)
    await _route(tenant_id="t1")
    assert rec.guarded_kwargs["tenant_id"] == "t1"
    assert rec.guarded_kwargs["agent_type"] == "orchestrator_router"


# ── turning the model's answer into a decision ───────────────────────────────


def _tool_call(name, reason="because"):
    return {"name": name, "args": {"reason": reason}, "id": "call-1"}


@pytest.mark.asyncio
async def test_one_tool_call_becomes_the_routing_decision(monkeypatch):
    _install(monkeypatch, response=_FakeAIMessage(
        tool_calls=[_tool_call("route_to_requirements", "you asked for a PRD")]))
    d = await _route()
    assert d.agent_id == "requirements"
    assert d.reason == "you asked for a PRD"
    assert d.direct_reply is None


@pytest.mark.asyncio
async def test_no_tool_call_becomes_a_direct_reply(monkeypatch):
    """Not every message is delivery work. 'What did we decide yesterday?' needs an
    answer, not an agent — and handing it to one would interrupt work rather than
    advance it."""
    _install(monkeypatch, response=_FakeAIMessage(content="We decided to ship on Friday."))
    d = await _route(text="what did we decide yesterday?")
    assert d.agent_id is None
    assert d.direct_reply == "We decided to ship on Friday."


@pytest.mark.asyncio
async def test_content_blocks_are_read_as_text(monkeypatch):
    """Some providers return content as a list of typed blocks rather than a string.
    Reading `.content` naively would put a Python repr in front of the user."""
    _install(monkeypatch, response=_FakeAIMessage(
        content=[{"type": "text", "text": "no agent needed here"}]))
    d = await _route()
    assert d.direct_reply == "no agent needed here"


@pytest.mark.asyncio
async def test_two_tool_calls_are_refused_rather_than_guessed(monkeypatch):
    """This function returns ONE id and cannot express 'requirements, then design'.
    Taking the first would be a silent guess — the same choice the pre-filter
    already refuses to make for a message naming two agents."""
    _install(monkeypatch, response=_FakeAIMessage(
        tool_calls=[_tool_call("route_to_requirements"), _tool_call("route_to_design")]))
    d = await _route()
    assert d.agent_id is None
    assert d.direct_reply
    assert "Requirements" in d.direct_reply and "Design" in d.direct_reply


@pytest.mark.asyncio
async def test_a_tool_call_with_no_reason_still_tells_the_user_something(monkeypatch):
    """`reason` is required in the schema, but a model can omit a required argument.
    An empty line in the UI is not a reason."""
    _install(monkeypatch, response=_FakeAIMessage(
        tool_calls=[{"name": "route_to_design", "args": {}, "id": "c1"}]))
    d = await _route()
    assert d.agent_id == "design"
    assert d.reason.strip()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_name",
    [
        "route_to_marketing",   # an agent that does not exist
        "route_to_Testing",     # right agent, wrong case — ids are exact
        "route_to_",            # a truncated name
        "testing",              # the prefix omitted
        "route_to_plan_agent",  # the display name smuggled into the id
    ],
)
async def test_a_hallucinated_tool_name_never_reaches_dispatch(monkeypatch, bad_name):
    """The tools are generated from the registry so none of these can be offered —
    but a model can emit a name it was never given, and `dispatch.run_agent` would
    then answer with an `error` event for an id the router had chosen. Refuse here,
    visibly."""
    _install(monkeypatch, response=_FakeAIMessage(tool_calls=[_tool_call(bad_name)]))
    d = await _route()
    assert d.agent_id is None
    assert d.direct_reply, "a refusal must say so, not be a silent no-op"


@pytest.mark.asyncio
async def test_choosing_no_agent_and_saying_nothing_is_never_a_silent_no_op(monkeypatch):
    """agent_id=None means 'the router answers'. With no reply there is nothing to
    show, and the turn ends in silence — the exact failure mode this engine exists
    to remove."""
    async def _empty(*a, **k):
        return router.RoutingDecision(agent_id=None, reason="", direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", _empty)
    d = await _route()
    assert d.agent_id is None
    assert d.direct_reply and d.direct_reply.strip()
    assert d.reason and d.reason.strip()


def test_a_routing_decision_is_frozen():
    """A decision is a fact about a turn. Mutating it after the fact is how the
    agent announced in `agent.selected` stops being the one that ran."""
    d = router.RoutingDecision(agent_id="design", reason="r", direct_reply=None)
    with pytest.raises(Exception):
        d.agent_id = "security"


@pytest.mark.asyncio
async def test_every_agent_is_reachable_by_meaning(monkeypatch):
    """Derived from AGENT_IDS: a tenth agent cannot silently miss coverage. Proves
    the id survives tool-name round-tripping for all nine (`code_review`'s
    underscore is the one that would break a naive split)."""
    for agent_id in AGENT_IDS:
        _install(monkeypatch, response=_FakeAIMessage(
            tool_calls=[_tool_call(f"{router._TOOL_PREFIX}{agent_id}")]))
        d = await _route()
        assert d.agent_id == agent_id
