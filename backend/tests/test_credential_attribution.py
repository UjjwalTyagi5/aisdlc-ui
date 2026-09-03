"""No borrowed tokens: every agent-runtime credential resolution names a person.

THE PRODUCT RULE. One credential, per user, per project. Whoever's token makes a call
is who the external system records, so a resolution without an owner runs the work
under whatever shared credential happens to exist and files it against that identity.
The audit trail then reads as authoritative while being wrong, which is worse than
having none.

`get_connector_for_session(..., owner_id=...)` is what resolves a person's own
project-scoped credential. Omitting it is not a style choice — it is the difference
between "srk02 deployed this" and "something deployed this".

THIS FILE IS A LEDGER, NOT A BAN. Some call sites genuinely have no person, and each
one is listed below with the reason. A new ownerless call site fails this test, which
forces whoever adds it to either thread the user through or write down why they cannot
— rather than leaving it to be discovered when somebody asks who deployed to
production.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.unit

#: Call sites allowed to resolve a connector with no owner, and why.
#:
#: Every entry is a place where there is genuinely no person to attribute to. Adding
#: to this list is a decision about attribution, so it should be argued for in review.
_ALLOWED = {
    # The factory itself, and its own docstring examples.
    "config/connector_factory.py":
        "the function being defined",
    # Integrations: catalogue, health and Test Connection. These act on a connector's
    # configuration rather than doing work in the external system, and Test Connection
    # validates a credential that has not been saved yet.
    "shared/routers/connectors.py":
        "configuration and health, not work in the external system",
    "shared/routers/project_scoped.py":
        "reads a project's connector configuration",
    # Notifications. A Slack bot token identifies an APP, not a person — every member
    # would paste the same value and Slack shows the app as the author regardless, so
    # there is no per-person attribution to be had here at any price.
    "shared/services/notify_dispatch.py":
        "Slack bot tokens are an app identity; per-person attribution is impossible",
    # The legacy deployment orchestrator, superseded by agents/deployer.py and mounted
    # at /sdlc/agent/deployment_orchestrator. Not extended; slated for removal.
    "agents_orchestrator/deployment_agent/agents/pipeline_app.py":
        "legacy route, superseded by agents/deployer.py",
}

#: Call sites that SHOULD name a person and do not yet. Each one runs work in an
#: external system on behalf of a user in a conversation, and records it against
#: whatever shared credential is configured instead.
#:
#: This is a debt list, deliberately explicit rather than absent. Fixing one means
#: passing `owner_id=get_user_id()` and deleting its line here.
_KNOWN_GAPS = {
    "agents_orchestrator/documentation_agent/tools/doc_tools.py":
        "writes pages to Confluence/SharePoint as the platform, not the author",
    "agents_orchestrator/design_architecture_agent/tools/figma_tools.py":
        "reads Figma as the platform",
    "agents_orchestrator/orchestrator/copilot_api.py":
        "one of two call sites still ownerless",
}


def _call_sites() -> list[tuple[str, int, bool]]:
    """Every get_connector_for_session call in the tree, and whether it names an owner.

    Parsed rather than grepped: a keyword argument spanning several lines is invisible
    to a line-based search, and this test exists precisely to be hard to fool.
    """
    found: list[tuple[str, int, bool]] = []
    # Only the source trees. Globbing from the repo root walks .venv first — thousands
    # of files, and slow enough that the test stops being run.
    sources = ("agents_orchestrator", "shared", "config")
    for top in sources:
        for path in (ROOT / top).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "tests/" in rel or "__pycache__" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name != "get_connector_for_session":
                    continue
                has_owner = any(k.arg == "owner_id" for k in node.keywords)
                found.append((rel, node.lineno, has_owner))
    return found


def test_there_are_call_sites_to_check():
    """A parser that silently finds nothing would make every test below vacuous."""
    assert len(_call_sites()) >= 10


def test_no_new_call_site_resolves_a_credential_without_naming_a_person():
    """THE INVARIANT. A resolution with no owner borrows a shared credential and
    records the work against it."""
    offenders = sorted({
        rel for rel, _line, has_owner in _call_sites()
        if not has_owner and rel not in _ALLOWED and rel not in _KNOWN_GAPS
    })
    assert not offenders, (
        "These resolve a connector credential without naming a person:\n  "
        + "\n  ".join(offenders)
        + "\n\nPass owner_id=get_user_id() so the external system records who did the "
          "work. If there genuinely is no person, add the file to _ALLOWED with the "
          "reason."
    )


def test_the_deployment_agent_always_names_a_person():
    """The agent this rule was written for. Every one of its resolutions runs work in
    Azure DevOps or SonarQube on somebody's behalf."""
    ownerless = [
        (rel, line) for rel, line, has_owner in _call_sites()
        if not has_owner and "deployment_agent" in rel
        and "pipeline_app" not in rel
    ]
    assert not ownerless, f"deployment agent resolutions with no owner: {ownerless}"


def test_the_executor_names_a_person():
    ownerless = [
        (rel, line) for rel, line, has_owner in _call_sites()
        if not has_owner and "deployment_executor" in rel
    ]
    assert not ownerless


@pytest.mark.parametrize("path,reason", sorted(_KNOWN_GAPS.items()))
def test_each_known_gap_still_exists(path, reason):
    """A gap that has been fixed must be removed from the list.

    Otherwise the debt list rots into a place where fixed things hide, and the count
    stops meaning anything.
    """
    sites = [(rel, line, ok) for rel, line, ok in _call_sites() if rel == path]
    assert sites, f"{path} no longer calls get_connector_for_session — drop it from _KNOWN_GAPS"
    assert any(not ok for _r, _l, ok in sites), (
        f"{path} now names a person everywhere — remove it from _KNOWN_GAPS ({reason})"
    )


def test_slack_is_documented_as_impossible_rather_than_merely_unfinished():
    """The distinction matters. A Slack bot token identifies an app; two members would
    paste the same value and Slack shows the app as author whoever configured it. That
    is not debt to be paid off, and filing it as debt would have somebody spend a day
    discovering why it cannot be done."""
    assert "impossible" in _ALLOWED["shared/services/notify_dispatch.py"]
    assert "shared/services/notify_dispatch.py" not in _KNOWN_GAPS
