"""BYOK per-tenant LLM key routing tests.

REQ-M7-18: tenant_id threaded through LangGraph AgentState.
REQ-M7-19: BYOK opt-in; silent fallback to platform default; api_key_alias in cost log.
SC#5: cost-log alias is non-secret.
SC#7: 5-minute TTL so key rotation is picked up within the window.
SC#8: resolved key never appears in any log line or exception message.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════════════
# NOTE (D-4, Phase 3): The platform ANTHROPIC_API_KEY fallback was removed and
# shared/services/llm_key.py retired. The former TestResolveApiKey and
# TestCostLogAlias suites — which covered resolve_llm_api_key /
# get_effective_api_key / get_api_key_alias and the platform-fallback path —
# were deleted because that behavior no longer exists. Agents now resolve via
# shared.services.model_resolver.resolve_model_for_run and fail CLOSED; see
# tests/test_model_resolver_failclosed.py for the replacement coverage.
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# TestAgentStateTenantId — tenant_id field presence + activity threading (Task 2)
# ═══════════════════════════════════════════════════════════════════════════

class TestAgentStateTenantId:
    """tenant_id field present in all active AgentState TypedDicts (REQ-M7-18)."""

    async def test_requirements_agent_state_has_tenant_id(self):
        """AgentState in requirements_agent planning.py has a tenant_id field."""
        from agents_orchestrator.requirements_agent.agents.planning import AgentState
        hints = AgentState.__annotations__
        assert "tenant_id" in hints, "AgentState in planning.py must have tenant_id field"

    async def test_design_agent_state_has_tenant_id(self):
        """AgentState in design_architecture_agent architecture.py has a tenant_id field."""
        from agents_orchestrator.design_architecture_agent.agents.architecture import AgentState
        hints = AgentState.__annotations__
        assert "tenant_id" in hints, "AgentState in architecture.py must have tenant_id field"

    async def test_dev_agent_state_has_tenant_id(self):
        """DevAgentState in development_agent dev_agent.py has a tenant_id field."""
        from agents_orchestrator.development_agent.agents.dev_agent import DevAgentState
        hints = DevAgentState.__annotations__
        assert "tenant_id" in hints, "DevAgentState in dev_agent.py must have tenant_id field"

    async def test_tenant_id_threaded_through_pipeline_session(self):
        """The conversational path passes tenant_id into the stage's session (REQ-M7-18).

        Was test_tenant_id_passed_from_activity_to_graph, importing
        workflows.activities.requirements_activity — a Temporal @activity.defn
        wrapper. workflows/activities/__init__.py's own docstring: "The per-agent
        @activity.defn wrappers that used to sit beside these were Temporal bindings
        and are gone with it." tenant_id threading itself is very much still a live
        concern; it's verified against its actual current carrier,
        workflows/activities/pipeline_session.py, instead.
        """
        import inspect
        from workflows.activities import pipeline_session
        src = inspect.getsource(pipeline_session)
        assert "input.tenant_id" in src or "tenant_id = getattr(input" in src, (
            "pipeline_session must thread tenant_id from the activity input"
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestByokNoLeak — per-invocation factory + exception no-leak (Task 3)
# ═══════════════════════════════════════════════════════════════════════════

class TestByokNoLeak:
    """Per-invocation LLM factory uses tenant key; exceptions never leak key (SC#8)."""

    async def test_model_resolver_used_in_design_agent(self):
        """architecture.py resolves the model via model_resolver (post-D-4 cutover)."""
        import inspect
        from agents_orchestrator.design_architecture_agent.agents import architecture
        src = inspect.getsource(architecture)
        assert "resolve_model_for_run" in src, (
            "architecture.py must resolve via model_resolver.resolve_model_for_run"
        )

    async def test_model_resolver_used_in_requirements_agent(self):
        """planning.py resolves the model via model_resolver (post-D-4 cutover)."""
        import inspect
        from agents_orchestrator.requirements_agent.agents import planning
        src = inspect.getsource(planning)
        assert "resolve_model_for_run" in src, (
            "planning.py must resolve via model_resolver.resolve_model_for_run"
        )

    async def test_user_api_key_alias_in_design_or_requirements_agent(self):
        """user_api_key_alias metadata is set in at least one agent (SC#5)."""
        import inspect
        from agents_orchestrator.design_architecture_agent.agents import architecture
        from agents_orchestrator.requirements_agent.agents import planning
        arch_src = inspect.getsource(architecture)
        plan_src = inspect.getsource(planning)
        assert "user_api_key_alias" in arch_src or "user_api_key_alias" in plan_src, (
            "At least one agent must set user_api_key_alias metadata in LLM calls (SC#5)"
        )

    async def test_module_level_singleton_removed_from_architecture(self):
        """Module-level 'orchestrator = ChatLiteLLM(...)' singleton is removed from architecture.py."""
        import inspect
        from agents_orchestrator.design_architecture_agent.agents import architecture
        src = inspect.getsource(architecture)
        # The module-level singleton would look like: orchestrator = ChatLiteLLM(
        # Per-invocation factory is acceptable, but global singleton is not.
        lines = src.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("orchestrator = ChatLiteLLM("):
                # It must be inside a function/method, not at module level
                # Module-level lines have no indentation
                if not line.startswith(" ") and not line.startswith("\t"):
                    raise AssertionError(
                        f"Module-level 'orchestrator = ChatLiteLLM(...)' found at line {i + 1} "
                        "in architecture.py — must be replaced by per-tenant factory"
                    )

    async def test_exception_handler_does_not_log_str_exc(self):
        """Exception handlers in design agent must not log str(exc) — only type name (SC#8).

        Was a whole-file substring check ("str(e)" not in src or "logger" not in
        src) — which fails the moment BOTH strings appear anywhere in the file, even
        when they're nowhere near each other. architecture.py legitimately uses
        str(e) once outside logging (a truncated tool-error message shown to the
        user in chat, not written to server logs — see the comment at that call
        site), so the blunt check could never pass while that call exists. The
        actual guard this test is for — no logger.* call embeds str(exc) or the bare
        exception object — is checked directly against each logger call site below.
        """
        import inspect
        import re
        from agents_orchestrator.design_architecture_agent.agents import architecture
        src = inspect.getsource(architecture)
        # One level of nested parens is enough for every logger call in this file
        # (e.g. type(e).__name__ inside logger.error(...)).
        logger_calls = re.findall(r"logger\.\w+\((?:[^()]|\([^()]*\))*\)", src)
        assert logger_calls, "expected at least one logger.* call in architecture.py"
        # "str(e)" catches the direct case; ", e," / ", e)" catches the bare exception
        # object passed as a positional %s arg (implicitly str()'d by logging) without
        # matching legitimate type(e)/str(e) subexpressions, which are "(e)" not ", e".
        offenders = [
            call for call in logger_calls
            if "str(e)" in call or re.search(r",\s*e\s*[,)]", call)
        ]
        assert not offenders, (
            "architecture.py logs str(exc) or the bare exception object — use "
            f"type(e).__name__ instead (SC#8): {offenders}"
        )
