"""An agent mid-conversation keeps the turn.

THE BUG THIS FILE EXISTS FOR, from a real session:

    You:          pull code from ADO, I want to make some changes
    Orchestrator: Which repository? What kind of changes?      <- should have routed
    You:          company repository, adding a FEATURE
    Development:  Here are the projects... which one?
    You:          2
    Orchestrator: Great, you've selected Company. Which repository?   <- WRONG
    Development:  Here are the repositories in Company... which one?  <- asked again

The Orchestrator kept interleaving itself into a conversation the Development agent was
already holding, and re-asking what had just been asked. Two turns were spent on the
same question.

THE CAUSE. `route()` runs on EVERY turn, and the router is never told which agent
answered the previous one. So a reply of "2" or "main" arrives as a bare, contextless
message — and the prompt explicitly permits answering directly for "a follow-up about
something already produced in this conversation", which is exactly what that looks
like.

An agent that has asked the user a question owns the answer to it. That is not a
routing decision at all.
"""
import pytest

from agents_orchestrator.orchestrator2 import router as rtr


def test_route_accepts_the_agent_that_answered_last():
    """Keyword-only and defaulted to None: a caller with no previous turn is the normal
    first-message case, not an error."""
    import inspect

    param = inspect.signature(rtr.route).parameters["last_agent"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


def _continuity_text(agent_id):
    """Only what continuity ADDED to the prompt.

    The roster already names every agent and every `route_to_*` tool, so asserting
    against the whole prompt proves nothing: mutation showed `"Development" in built`
    staying true with the continuity line removed entirely, and again with it added
    but naming nobody. The delta is the only part that is this feature's.
    """
    base = rtr._system_prompt()
    built = rtr._system_prompt_with_continuity(agent_id)
    assert built.startswith(base), "continuity must add to the prompt, not rewrite it"
    return built[len(base):]


def test_the_prompt_tells_the_model_who_is_mid_conversation():
    """The routing prompt is where this is decided, so the fact has to reach it.

    Read off the built prompt rather than the source, because a constant that is never
    interpolated into the prompt would satisfy a source grep and change nothing.
    """
    added = _continuity_text("development")
    assert "Development" in added, "the added line does not say WHICH agent"
    assert f"{rtr._TOOL_PREFIX}development" in added, (
        "the added line names no tool, so the model has nothing to call"
    )
    lowered = added.lower()
    assert "continu" in lowered or "already" in lowered


def test_no_continuity_line_when_no_agent_has_spoken():
    """A first message must be routed on its own merits — a stale continuity line would
    pin every conversation to whichever agent happened to run first."""
    built = rtr._system_prompt_with_continuity(None)
    assert built == rtr._system_prompt()


@pytest.mark.asyncio
async def test_a_short_answer_continues_the_agent_rather_than_being_answered(monkeypatch):
    """The end-to-end shape of the bug: 'main' is an answer to 'which branch?', not a
    new request, and must not be handled by the Orchestrator itself."""
    seen = {}

    async def _fake_ask(text, **kwargs):
        seen["system"] = kwargs.get("system_prompt") or ""
        return rtr.RoutingDecision(agent_id="development", reason="r", direct_reply=None)

    monkeypatch.setattr(rtr, "_ask_model", _fake_ask)
    decision = await rtr.route(
        "main", history=[], run_id="r1", tenant_id="t1", project_id="p1",
        model_id=None, offering_id=None, last_agent="development",
    )
    assert decision.agent_id == "development"
    # Compared against the BASE prompt, not searched for a name the roster supplies
    # anyway. `route` computing continuity and then sending the plain prompt is a real
    # way to get this wrong, and it survived the first version of this assertion.
    assert seen["system"] != rtr._system_prompt(), (
        "the model was sent the plain prompt — it was never told an agent is "
        "mid-conversation"
    )
    assert seen["system"] == rtr._system_prompt_with_continuity("development")


@pytest.mark.asyncio
async def test_an_explicit_new_request_can_still_change_agent(monkeypatch):
    """Continuity is a nudge, not a lock. 'Any agent can come at any time according to
    the chat' — pinning the conversation to one agent would rebuild the linearity this
    engine exists to remove."""
    async def _fake_ask(text, **kwargs):
        return rtr.RoutingDecision(agent_id="security", reason="r", direct_reply=None)

    monkeypatch.setattr(rtr, "_ask_model", _fake_ask)
    decision = await rtr.route(
        "now run a security review", history=[], run_id="r1", tenant_id="t1",
        project_id="p1", model_id=None, offering_id=None, last_agent="development",
    )
    assert decision.agent_id == "security"


@pytest.mark.asyncio
async def test_the_prefilter_still_wins_over_continuity(monkeypatch):
    """Naming an agent outright is the one thing that needs no interpretation, and it
    must not be overridden by whoever spoke last."""
    decision = await rtr.route(
        "run the security agent", history=[], run_id="r1", tenant_id="t1",
        project_id="p1", model_id=None, offering_id=None, last_agent="development",
    )
    assert decision.agent_id == "security"
