"""Tests for Development agent native capability tags (D2 task).

Verifies:
  1. All NATIVE_TAGS["development"] values are in the taxonomy.
  2. config_capability_report["development"] == [] with native + curated default-on.
  3. Native tags live only in native_tags.py (DP1 side-map constraint — dev_agent.py
     is NOT modified).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from shared.capabilities import native_tags, taxonomy
from shared.capabilities.config_check import config_capability_report
from config.agent_registry import AGENT_REGISTRY


# ── 1. All tag values are valid taxonomy capabilities ────────────────────────

def test_development_native_tags_are_valid_capabilities():
    tags = native_tags.NATIVE_TAGS.get("development", {})
    assert tags, "NATIVE_TAGS['development'] is empty — tags were not added"
    taxonomy.assert_valid(tags.values())


# ── 2. No capability gap with native + curated default-on ────────────────────

def test_development_no_gap_with_default_configuration():
    """With only native + default-on curated tools, Development must have zero gaps."""
    report = config_capability_report({"agents": {}})
    assert report.get("development", []) == [], (
        f"Development still has capability gaps: {report.get('development')}"
    )


def test_development_required_caps_all_covered_natively():
    """Every required capability for Development has at least one native tool tag."""
    provided = native_tags.native_capabilities("development")
    required = set(AGENT_REGISTRY["development"].required_capabilities)
    missing = required - provided
    assert not missing, (
        f"These required caps have no native tool tag: {sorted(missing)}"
    )


# ── 3. dev_agent.py was NOT modified (DP1 constraint) ────────────────────────

def test_dev_agent_has_no_capability_annotations():
    """DP1: capability tags must live in native_tags.py, never as decorators in tool files."""
    dev_agent_path = (
        pathlib.Path(__file__).parents[2]
        / "agents_orchestrator/development_agent/agents/dev_agent.py"
    )
    assert dev_agent_path.exists(), f"dev_agent.py not found at {dev_agent_path}"
    source = dev_agent_path.read_text(encoding="utf-8")
    # Guard: the word "native_tags" should NOT appear in dev_agent.py
    assert "native_tags" not in source, (
        "dev_agent.py imports or references native_tags — capability tags must stay "
        "in shared/capabilities/native_tags.py (DP1)."
    )
    # Guard: no capability-string literals like "repo.write" in dev_agent.py
    for cap in taxonomy.CAPABILITIES:
        assert f'"{cap}"' not in source and f"'{cap}'" not in source, (
            f"Capability literal {cap!r} found in dev_agent.py — must not embed tags there."
        )


# ── Spot-checks for individual required capabilities ─────────────────────────

@pytest.mark.parametrize("cap", [
    "repo.read",
    "repo.write",
    "vcs.branch.create",
    "code.generate",
    "code.edit",
    "code.lint",
    "code.build",
    "vcs.commit",
    "vcs.pr.create",
    "artifact.write",
])
def test_required_cap_is_covered(cap: str):
    provided = native_tags.native_capabilities("development")
    assert cap in provided, f"Required capability {cap!r} is not covered by any native tool tag"
