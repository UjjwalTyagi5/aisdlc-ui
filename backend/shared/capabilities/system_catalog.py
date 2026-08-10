"""Tier-1 curated catalog — first-party managed MCP servers (decisions D8, DP2).

Vendor-defined (seeded in code), owned at system scope, capability-tagged by us.
Org admins only enable/disable + configure; they never build these. `default_on_agents`
lists the agents for which the tool is bound by default. `url` points at the platform-
hosted managed MCP server (filled per environment; placeholder allowed in dev).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CuratedTool:
    key: str                       # stable id, e.g. "req-quality-linter"
    display_name: str              # shown in the "Verified / Recommended" UI section
    capability: str                # the single capability this tool satisfies
    transport: str                 # streamable_http | sse | stdio
    url: Optional[str] = None      # managed MCP server endpoint (http/sse)
    command: Optional[str] = None  # stdio
    default_on_agents: tuple[str, ...] = ()
    config_schema: dict = field(default_factory=dict)  # admin-tunable knobs (e.g. severity floor)


SYSTEM_CATALOG: list[CuratedTool] = [
    CuratedTool(
        key="req-quality-linter",
        display_name="Requirements Quality Linter (NLP)",
        capability="req.quality.analyze",
        transport="streamable_http",
        url=None,  # set per-env to the managed server; resolution tolerates None in dev
        default_on_agents=("requirements",),
        config_schema={"ruleset": {"type": "string", "default": "standard"}},
    ),
    # ── Development cohort — linter/formatter managed MCP servers ─────────────
    CuratedTool(
        key="dev-ruff",
        display_name="Ruff linter",
        capability="code.lint",
        transport="stdio",
        command="ruff check --output-format=json",
        default_on_agents=("development",),
        config_schema={"target_version": {"type": "string", "default": "py311"}},
    ),
    CuratedTool(
        key="dev-eslint",
        display_name="ESLint",
        capability="code.lint",
        transport="stdio",
        command="eslint --format=json",
        default_on_agents=("development",),
        config_schema={"config_path": {"type": "string", "default": ".eslintrc"}},
    ),
    CuratedTool(
        key="dev-prettier",
        display_name="Prettier formatter",
        capability="code.format",
        transport="stdio",
        command="prettier --write",
        default_on_agents=("development",),
        config_schema={"tab_width": {"type": "integer", "default": 2}},
    ),
    # Cohort A curated tools (Semgrep, Trivy, Gitleaks, Kroki, Spectral…)
    # are seeded in their agent fan-out plan (roadmap Plan 2). Keep this seed lean.
]


def curated_for_agent(agent_id: str, disabled: "set[str] | frozenset[str]" = frozenset()) -> list[CuratedTool]:
    """Default-on curated tools for an agent, minus any admin-disabled keys."""
    return [
        t for t in SYSTEM_CATALOG
        if agent_id in t.default_on_agents and t.key not in disabled
    ]
