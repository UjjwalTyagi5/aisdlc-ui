import uuid

from shared.services.agent_profile_store import (
    merge_profiles,
    inject_prompt,
    ResolvedProfile,
    _disabled_for_project,
)
from shared.models.orm import AgentProfile


def _row(scope, **kw):
    return AgentProfile(agent_id="requirements", scope=scope, created_by="t", **kw)


def test_merge_orders_org_then_workspace_then_project():
    rows = [
        _row("project", prompt_prepend="P"),
        _row("org", prompt_prepend="O"),
        _row("workspace", prompt_prepend="W"),
    ]
    merged = merge_profiles(rows)
    assert merged.prompt_prepend == "O\n\nW\n\nP"


def test_merge_unions_disabled_curated():
    rows = [
        _row("org", disabled_curated=["a"]),
        _row("project", disabled_curated=["b"]),
    ]
    merged = merge_profiles(rows)
    assert merged.disabled_curated == {"a", "b"}


def test_project_override_wins_for_thresholds():
    rows = [
        _row("org", thresholds={"coverage": 70, "block_on_critical": True}),
        _row("project", thresholds={"coverage": 85}),
    ]
    merged = merge_profiles(rows)
    assert merged.thresholds == {"coverage": 85, "block_on_critical": True}


def test_inject_prompt_wraps_base():
    profile = ResolvedProfile(prompt_prepend="RULES", prompt_append="SIGNOFF")
    out = inject_prompt("BASE", profile)
    assert out.startswith("RULES")
    assert "BASE" in out
    assert out.rstrip().endswith("SIGNOFF")


def test_empty_profile_returns_base_unchanged():
    out = inject_prompt("BASE", ResolvedProfile())
    assert out.strip() == "BASE"


def test_disabled_for_project_unions_org_and_matching_project():
    pid = uuid.uuid4()
    other = uuid.uuid4()
    rows = [
        AgentProfile(agent_id="development", scope="org", created_by="t",
                     disabled_curated=["web_search"]),
        AgentProfile(agent_id="development", scope="project", scope_id=pid,
                     created_by="t", disabled_curated=["jira"]),
        AgentProfile(agent_id="development", scope="project", scope_id=other,
                     created_by="t", disabled_curated=["should_not_apply"]),
    ]
    out = _disabled_for_project(rows, str(pid))
    assert out["development"] == {"web_search", "jira"}


def test_disabled_for_project_ignores_unrelated_scopes():
    pid = uuid.uuid4()
    rows = [
        AgentProfile(agent_id="testing", scope="workspace", scope_id=uuid.uuid4(),
                     created_by="t", disabled_curated=["x"]),
    ]
    out = _disabled_for_project(rows, str(pid))
    assert out == {}
