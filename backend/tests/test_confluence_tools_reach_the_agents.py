"""Every agent that can publish a document to SharePoint can publish it to Confluence.

THE BUG THIS PINS was not a missing feature. Asked to publish an approved BRD to
Confluence and create a space for it, the PM agent answered that "this platform only
publishes to the project's SharePoint library or exports files you can upload" — which
was TRUE of that agent and false of the platform. `config/connectors/confluence.py`
implemented every capability needed; the tools were bound to the Documentation agent
and to nothing else, so on the screen the user was actually on, the capability did not
exist.

A missing binding fails exactly like a missing feature and is far harder to see: the
connector's tests pass, the capability manifest is honest, and the agent politely
declines. So the invariant is pinned as a pair — wherever SharePoint publishing is
bound, Confluence publishing is bound too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]

#: The Documentation agent is the deliberate exception: it has owned Confluence tools
#: since before the factory existed (`publish_to_confluence`, `list_confluence_pages`,
#: `ingest_confluence_page` in documentation_agent/tools/doc_tools.py), and binding the
#: factory there too would register `list_confluence_pages` twice under one name.
_HAS_ITS_OWN = {"documentation_agent"}


def _agent_modules_binding_sharepoint() -> list[Path]:
    out = []
    for path in (_BACKEND / "agents_orchestrator").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "make_sharepoint_tools(" in text and "def make_sharepoint_tools" not in text:
            out.append(path)
    return out


def test_there_is_at_least_one_publishing_agent():
    """Guards the guard: a glob that matches nothing would pass every test below."""
    assert _agent_modules_binding_sharepoint()


@pytest.mark.unit
@pytest.mark.parametrize(
    "path", _agent_modules_binding_sharepoint(), ids=lambda p: p.parent.parent.name
)
def test_a_sharepoint_publisher_also_binds_confluence(path: Path):
    agent_package = path.parent.parent.name
    if agent_package in _HAS_ITS_OWN:
        pytest.skip(f"{agent_package} owns its Confluence tools directly")

    text = path.read_text(encoding="utf-8")
    assert "make_confluence_tools(" in text, (
        f"{agent_package} can publish to SharePoint but not to Confluence. "
        "Bind make_confluence_tools alongside make_sharepoint_tools."
    )
    # Bound is not the same as REACHABLE — the variable has to reach the tool list.
    assert "*_CONFLUENCE_TOOLS" in text, (
        f"{agent_package} builds _CONFLUENCE_TOOLS but never spreads it into `tools`, "
        "so the model is never offered them."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "path", _agent_modules_binding_sharepoint(), ids=lambda p: p.parent.parent.name
)
def test_agent_and_stage_are_bound_at_registration_not_taken_from_the_model(path: Path):
    """A tool argument would let a prompt claim another agent's grant.

    The same contract `make_sharepoint_tools` states: both ids are fixed by the agent
    that registers the tools.
    """
    text = path.read_text(encoding="utf-8")
    for call in re.findall(r"make_confluence_tools\((.*?)\)", text, re.S):
        assert "agent_id=" in call and "stage=" in call, call


@pytest.mark.unit
def test_the_factory_offers_the_operations_the_failing_session_needed():
    """Create a space, publish a document into it, attach the file."""
    from shared.tools.confluence_artifacts import make_confluence_tools

    names = {t.name for t in make_confluence_tools("plan", "plan")}
    assert "create_confluence_space" in names
    assert "publish_approved_to_confluence" in names
    assert {"list_confluence_spaces", "list_confluence_pages", "read_confluence_page"} <= names
