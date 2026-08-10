"""Unit tests for the vendor-skill registry (shared.skills.registry).

Pure disk-read tests — no Postgres, no app. Assert the packaged vendor catalog
loads exactly the expected shape, the testing agent's own skills are surfaced,
malformed SKILL.md files are skipped fail-soft, and lookups behave.
"""
from __future__ import annotations

from shared.skills import registry
from shared.skills.registry import (
    VendorSkill,
    all_vendor_skills,
    get_vendor_skill,
    vendor_skills_for,
)

# Every agent that must ship exactly 2 vendor skills (excludes 'testing', which is
# surfaced from its own on-disk dir and carries many more).
EXPECTED_TWO_PER_AGENT = {
    "requirements": {"story-splitting-spidr", "acceptance-criteria-review"},
    "design": {"adr-authoring", "api-contract-review"},
    "development": {"tdd-red-green-refactor", "safe-refactoring"},
    "code_review": {"security-first-review", "performance-review"},
    "security": {"threat-modeling-stride", "dependency-triage"},
    "deployment": {"rollback-planning", "release-readiness-checklist"},
    "documentation": {"runbook-authoring", "changelog-discipline"},
}


def test_loads_exactly_14_vendor_skills_across_7_agents():
    catalog = all_vendor_skills(reload=True)
    for agent_id, keys in EXPECTED_TWO_PER_AGENT.items():
        assert agent_id in catalog, f"missing agent {agent_id}"
        loaded = {s.skill_key for s in catalog[agent_id]}
        assert loaded == keys, f"{agent_id}: expected {keys}, got {loaded}"

    total_vendor = sum(len(EXPECTED_TWO_PER_AGENT[a]) for a in EXPECTED_TWO_PER_AGENT)
    assert total_vendor == 14

    counted = sum(
        len(catalog[a]) for a in EXPECTED_TWO_PER_AGENT
    )
    assert counted == 14


def test_testing_agent_surfaces_its_own_skills_dir():
    catalog = all_vendor_skills()
    assert "testing" in catalog
    assert len(catalog["testing"]) >= 12
    keys = {s.skill_key for s in catalog["testing"]}
    # A representative sample of the testing agent's on-disk skills.
    assert {"unit", "negative_edge", "smoke"} <= keys


def test_every_skill_is_wellformed():
    catalog = all_vendor_skills()
    for agent_id, skills in catalog.items():
        for s in skills:
            assert isinstance(s, VendorSkill)
            assert s.agent_id == agent_id
            assert s.skill_key
            assert s.display_name and s.display_name[0].isupper()
            assert s.description.strip()
            assert s.runtime in ("llm", "shell")
            assert s.body.strip()


def test_humanize_display_names():
    skill = get_vendor_skill("requirements", "story-splitting-spidr")
    assert skill is not None
    assert skill.display_name == "Story Splitting Spidr"


def test_get_vendor_skill_hit_and_miss():
    assert get_vendor_skill("design", "adr-authoring") is not None
    assert get_vendor_skill("design", "does-not-exist") is None
    assert get_vendor_skill("no-such-agent", "adr-authoring") is None


def test_vendor_skills_for_returns_copy():
    a = vendor_skills_for("security")
    assert len(a) == 2
    a.clear()
    # mutating the returned list must not corrupt the cache
    assert len(vendor_skills_for("security")) == 2


def test_malformed_skill_md_is_skipped(tmp_path, monkeypatch, caplog):
    """A SKILL.md with no frontmatter is logged and skipped, not fatal."""
    good = tmp_path / "good-skill"
    good.mkdir()
    (good / "SKILL.md").write_text(
        "---\nname: good-skill\ndescription: fine\nruntime: llm\n---\nbody here\n",
        encoding="utf-8",
    )
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter at all, just prose", encoding="utf-8")

    import logging

    with caplog.at_level(logging.WARNING):
        loaded = registry._load_dir("fake", tmp_path)

    keys = {s.skill_key for s in loaded}
    assert keys == {"good-skill"}
    assert any("bad-skill" in rec.getMessage() or "malformed" in rec.getMessage().lower()
               for rec in caplog.records)


def test_parse_handles_missing_optional_fields(tmp_path):
    d = tmp_path / "minimal"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: minimal\n---\njust a body\n", encoding="utf-8")
    loaded = registry._load_dir("fake", tmp_path)
    assert len(loaded) == 1
    s = loaded[0]
    assert s.skill_key == "minimal"
    assert s.description == ""
    assert s.when_to_use == ""
    assert s.runtime == "llm"
    assert "just a body" in s.body
