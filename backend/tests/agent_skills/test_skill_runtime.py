"""Unit tests for the skills RUNTIME (skill_store index + skill_runtime channel).

Pure in-process — no Postgres, no app. Covers build_skills_index, the load_skill
contextvar channel (set/clear/isolation, tool hit/miss), prepare_agent_turn fail-soft
with monkeypatched resolvers, and inject_prompt's skills_index back-compat.
"""
from __future__ import annotations

import pytest

from shared.services import prompt_runtime
from shared.services.agent_profile_store import ResolvedProfile, inject_prompt
from shared.services.prompt_runtime import prepare_agent_turn
from shared.services.skill_runtime import (
    get_skill_tools,
    skill_context_scope,
)
from shared.services.skill_store import ResolvedSkill, build_skills_index


def _skill(key: str, *, description: str = "does a thing", when: str = "when needed",
           body: str = "full instructions", origin: str = "vendor") -> ResolvedSkill:
    return ResolvedSkill(
        skill_key=key,
        agent_id="requirements",
        origin=origin,
        display_name=key.replace("-", " ").title(),
        description=description,
        when_to_use=when,
        body=body,
        runtime="llm",
    )


# ── build_skills_index ────────────────────────────────────────────────────────────

def test_build_skills_index_empty():
    assert build_skills_index([]) == ""


def test_build_skills_index_normal():
    idx = build_skills_index([
        _skill("story-splitting-spidr", description="split stories", when="story too big"),
        _skill("acceptance-criteria-review", description="review AC", when=""),
    ])
    assert idx.startswith("AVAILABLE SKILLS")
    assert "load_skill(" in idx
    assert "- story-splitting-spidr: split stories (use when: story too big)" in idx
    # when_to_use empty -> no "(use when: ...)" suffix
    assert "- acceptance-criteria-review: review AC" in idx
    assert "acceptance-criteria-review: review AC (use when" not in idx


def test_build_skills_index_caps_and_reports_remainder():
    many = [
        _skill(f"skill-{i}", description="x" * 200, when="y" * 200)
        for i in range(50)
    ]
    idx = build_skills_index(many)
    assert len(idx) <= 1500 + 40  # cap + the "…and N more" line
    assert "…and" in idx and "more" in idx
    # Not every skill made it in.
    assert idx.count("\n- ") < 50


# ── contextvar channel: set / clear / isolation ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_skill_tools_empty_outside_scope():
    assert get_skill_tools("requirements") == []


@pytest.mark.asyncio
async def test_scope_sets_and_clears():
    assert get_skill_tools("requirements") == []
    async with skill_context_scope("requirements", [_skill("story-splitting-spidr")]):
        tools = get_skill_tools("requirements")
        assert len(tools) == 1
        assert tools[0].name == "load_skill"
    # cleared on exit
    assert get_skill_tools("requirements") == []


@pytest.mark.asyncio
async def test_scope_clears_on_exception():
    with pytest.raises(RuntimeError):
        async with skill_context_scope("requirements", [_skill("s")]):
            assert get_skill_tools("requirements")
            raise RuntimeError("boom")
    assert get_skill_tools("requirements") == []


@pytest.mark.asyncio
async def test_scope_is_per_agent_isolated():
    async with skill_context_scope("requirements", [_skill("story-splitting-spidr")]):
        assert len(get_skill_tools("requirements")) == 1
        # a different agent sees no skills from another agent's scope
        assert get_skill_tools("design") == []


@pytest.mark.asyncio
async def test_empty_skills_is_noop_scope():
    async with skill_context_scope("requirements", []):
        assert get_skill_tools("requirements") == []


# ── load_skill tool: hit / miss ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_skill_hit_returns_body():
    skill = _skill("story-splitting-spidr", body="## How to split\nUse SPIDR.")
    async with skill_context_scope("requirements", [skill]):
        (load_skill,) = get_skill_tools("requirements")
        out = await load_skill.ainvoke({"name": "story-splitting-spidr"})
    assert "Use SPIDR." in out
    assert "story-splitting-spidr" in out


@pytest.mark.asyncio
async def test_load_skill_miss_lists_valid_keys():
    async with skill_context_scope("requirements", [
        _skill("story-splitting-spidr"), _skill("acceptance-criteria-review"),
    ]):
        (load_skill,) = get_skill_tools("requirements")
        out = await load_skill.ainvoke({"name": "no-such-skill"})
    assert "Unknown skill" in out
    assert "story-splitting-spidr" in out
    assert "acceptance-criteria-review" in out


# ── prepare_agent_turn fail-soft ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_agent_turn_no_tenant_returns_base():
    injected, skills, profile = await prepare_agent_turn(
        "requirements", "BASE PROMPT", tenant_id=None)
    assert injected == "BASE PROMPT"
    assert skills == []
    assert isinstance(profile, ResolvedProfile)


@pytest.mark.asyncio
async def test_prepare_agent_turn_no_base_returns_base():
    injected, skills, profile = await prepare_agent_turn(
        "requirements", "", tenant_id="t1")
    assert injected == ""
    assert skills == []


@pytest.mark.asyncio
async def test_prepare_agent_turn_failsoft_on_resolver_error(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prompt_runtime, "resolve_profile_cached", _boom)
    injected, skills, profile = await prepare_agent_turn(
        "requirements", "BASE PROMPT", tenant_id="t1", project_id=None)
    assert injected == "BASE PROMPT"
    assert skills == []
    assert isinstance(profile, ResolvedProfile)


@pytest.mark.asyncio
async def test_prepare_agent_turn_composes_index_without_profile(monkeypatch):
    """Skills index must be injected even when the profile is empty."""
    import shared.services.skill_runtime as skill_runtime

    async def _empty_profile(*a, **k):
        return ResolvedProfile()

    async def _skills(*a, **k):
        return [_skill("story-splitting-spidr", description="split")]

    monkeypatch.setattr(prompt_runtime, "resolve_profile_cached", _empty_profile)
    monkeypatch.setattr(skill_runtime, "resolve_active_skills", _skills)
    # clear TTL cache so the monkeypatched resolver is actually consulted
    skill_runtime.invalidate_skills_cache()

    injected, skills, profile = await prepare_agent_turn(
        "requirements", "BASE PROMPT", tenant_id="t1")
    assert "BASE PROMPT" in injected
    assert "AVAILABLE SKILLS" in injected
    assert "story-splitting-spidr" in injected
    assert len(skills) == 1
    skill_runtime.invalidate_skills_cache()


# ── inject_prompt skills_index back-compat ──────────────────────────────────────────

def test_inject_prompt_empty_skills_index_unchanged():
    profile = ResolvedProfile(prompt_append="APPENDED")
    with_default = inject_prompt("BASE", profile)
    with_empty = inject_prompt("BASE", profile, "")
    assert with_default == with_empty
    assert "AVAILABLE SKILLS" not in with_default


def test_inject_prompt_places_index_after_base_before_contract():
    profile = ResolvedProfile(output_contract_extra="CONTRACT")
    out = inject_prompt("BASE", profile, "INDEXBLOCK")
    assert out.index("BASE") < out.index("INDEXBLOCK") < out.index("CONTRACT")
