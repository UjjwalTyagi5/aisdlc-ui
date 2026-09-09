"""Every delivery agent can read the project's approved documents and file them to SharePoint.

WHY A FAN-OUT TEST AND NOT SIX PER-AGENT ONES. The tools are correct in one place —
`shared/tools/project_documents` and `shared/tools/sharepoint_artifacts` are both tested
directly. What goes wrong at this scale is an agent being MISSED: a file whose tool list
never got the spread, which no per-agent test catches because nobody writes the test for
the agent they forgot. So this imports each agent's real tool list and asserts on it.

IT ALSO ASSERTS THE ABSENCE. `publish_approved_to_sharepoint` publishes only what an
owner has accepted, and no tool anywhere may delete from a SharePoint library — a
plausible-sounding delete tool is exactly what a model would reach for, so its
non-existence is checked on every agent rather than assumed from the module.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: agent module path -> the tool-list attribute it exposes.
#: `testing` and `monitoring_feedback` are absent deliberately: neither builds a static
#: module-level tool list (testing assembles per-invocation in `Nodes/ingest_input.py`),
#: so there is nothing here to assert against. They are not "missed" — they are shaped
#: differently, and pretending otherwise would make this test lie about coverage.
AGENTS = [
    ("agents_orchestrator.requirements_agent.agents.planning", "tools", "requirements"),
    ("agents_orchestrator.design_architecture_agent.agents.architecture", "tools", "design"),
    ("agents_orchestrator.pm_agent.agents.schedule", "tools", "plan"),
    ("agents_orchestrator.development_agent.agents.dev_agent", "tools", "development"),
    ("agents_orchestrator.code_review_agent.agents.reviewer", "_tools", "code_review"),
    ("agents_orchestrator.security_agent.agents.scanner", "_tools", "security"),
    ("agents_orchestrator.deployment_agent.agents.deployer", "_tools", "deployment"),
]


def _tool_names(module_path: str, attr: str) -> set[str]:
    import importlib

    mod = importlib.import_module(module_path)
    return {getattr(t, "name", "") for t in getattr(mod, attr)}


@pytest.mark.parametrize("module_path,attr,stage", AGENTS, ids=[a[2] for a in AGENTS])
def test_the_agent_can_read_approved_documents(module_path, attr, stage):
    """`read_document` is how a stage builds on another's signed-off work. An agent
    without it silently cannot, and reports "no documents" rather than an error."""
    names = _tool_names(module_path, attr)
    assert "list_project_documents" in names, f"{stage} cannot list documents"
    assert "read_document" in names, f"{stage} cannot read documents"


@pytest.mark.parametrize("module_path,attr,stage", AGENTS, ids=[a[2] for a in AGENTS])
def test_the_agent_can_file_approved_documents_to_sharepoint(module_path, attr, stage):
    names = _tool_names(module_path, attr)
    assert "publish_approved_to_sharepoint" in names, f"{stage} cannot publish"
    assert "list_sharepoint_documents" in names
    assert "read_sharepoint_document" in names


@pytest.mark.parametrize("module_path,attr,stage", AGENTS, ids=[a[2] for a in AGENTS])
def test_no_agent_can_delete_from_sharepoint(module_path, attr, stage):
    """THE CONSTRAINT, checked per agent rather than trusted from the module. Removing a
    file from the business's library is a person's job, done in SharePoint where its own
    permissions and recycle bin apply."""
    names = _tool_names(module_path, attr)
    offenders = [
        n for n in names
        if "sharepoint" in n.lower() and ("delete" in n.lower() or "remove" in n.lower())
    ]
    assert offenders == [], f"{stage} has a SharePoint delete tool: {offenders}"


def test_the_publish_tool_is_the_approved_only_one():
    """NON-VACUITY, and a real distinction. The Documentation agent has its OWN
    `publish_to_sharepoint`, which files whatever it generated this session — in memory,
    reviewed by nobody. The fanned-out tool is a different one, named differently on
    purpose, and reads the artifacts table for rows an owner approved. A test asserting
    only "some publish tool exists" would pass on either and mean nothing."""
    names = _tool_names("agents_orchestrator.requirements_agent.agents.planning", "tools")
    assert "publish_approved_to_sharepoint" in names
    assert "publish_to_sharepoint" not in names
