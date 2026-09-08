"""The router routes. It does not decline on an agent's behalf.

REPORTED, twice in one session, and both times the answer was factually wrong.

    You:          can you create a story on azure boards
    Orchestrator: "I can't directly create items in Azure Boards or any external
                   tools — this platform works with your codebase and project
                   artifacts, not external issue trackers. … Would either of those
                   help?"

    You:          okay now can you pull code from azure repos
    Orchestrator: "I can't pull code from Azure Repos directly … Would you like me to
                   route you to the Development agent?"

The Requirements agent creates Azure Boards work items — it did exactly that later in
the same conversation, Task #76. The Development agent clones from Azure Repos; it had
done so an hour earlier. The router refused on their behalf, described the platform
inaccurately, and then asked permission to do the one thing it exists to do.

TWO CAUSES, and the second is why the prompt alone would not have fixed it.

1. `_CAPABILITIES` describes what each agent PRODUCES and never what it can REACH.
   Nothing in the roster says an agent touches the project's connected Azure DevOps,
   Jira or repositories, so a model reading it concludes the platform does not — and
   says so, confidently, because the roster is all it has.

2. The direct-reply rule permits answering "a question about this platform or about
   what you can do", and "can you create a story on azure boards" is exactly that
   shape. It is also a request for work an agent does, which has to win.

"It should not say that. It should automatically move to the next agent. Whatever I am
 talking about, move to that agent."
"""
import re

from agents_orchestrator.orchestrator2 import router as rtr


PROMPT = rtr._system_prompt()


def test_the_roster_says_agents_reach_the_projects_connected_tools():
    """Without this the model infers the platform is codebase-only and says so."""
    lowered = PROMPT.lower()
    assert "connect" in lowered, (
        "the roster never mentions the project's connectors, so the router has no "
        "reason to believe an agent can reach Azure DevOps or Jira at all"
    )


def test_the_roster_names_the_boards_and_repositories_agents_actually_use():
    """The two the user was refused for, by name — a generic 'integrations' would let
    the model keep guessing which ones are real."""
    lowered = PROMPT.lower()
    assert "azure devops" in lowered
    assert "jira" in lowered


def test_the_prompt_forbids_refusing_on_an_agents_behalf():
    lowered = PROMPT.lower()
    assert "behalf" in lowered and "decline" in lowered, (
        "nothing tells the router it may not answer that the platform cannot do "
        "something one of the nine does"
    )
    # The concrete refusals it actually produced, so the rule is anchored to them
    # rather than to a general sentiment a rewrite could drop.
    assert "start the agent that would" in lowered


def test_the_prompt_forbids_asking_permission_to_route():
    """"Would you like me to route you to the Development agent?" costs the user a
    whole turn to say yes to the thing they already asked for."""
    lowered = PROMPT.lower()
    assert "permission" in lowered or "would you like me to" in lowered, (
        "the router is not told to start the agent rather than offer to"
    )


def test_capability_asking_still_reaches_the_agent_that_does_it():
    """The shape of both reported failures: a capability question about work an agent
    does is a request for that work.

    Asserted on the prompt rather than a model call — the routing decision is the
    model's, and what this fix changes is what it is told.
    """
    lowered = PROMPT.lower()
    assert "can you" in lowered, (
        "the prompt does not address the 'can you …' phrasing that produced both "
        "refusals"
    )


def test_the_direct_reply_rule_still_permits_a_greeting():
    """The other half. A router that never answers anything turns "hi" into a
    Requirements run, which is the failure this rule exists for."""
    lowered = PROMPT.lower()
    assert "greeting" in lowered and "small talk" in lowered


def test_the_platform_capability_carve_out_is_narrowed_not_removed():
    """"A question about this platform" is what the model used to justify both
    refusals. It stays — someone genuinely may ask what this thing is — but it can no
    longer swallow a request for work."""
    assert re.search(r"question about (this )?platform", PROMPT, re.I), (
        "the carve-out was deleted rather than narrowed; a user asking what the "
        "platform is will now have an agent started at them"
    )
