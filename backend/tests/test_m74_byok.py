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

    async def test_tenant_id_passed_from_activity_to_graph(self):
        """Activity ainvoke call includes 'tenant_id' in the initial input dict.

        Verifies by inspecting the activity source that the pattern
        `'tenant_id': input.tenant_id` appears in the ainvoke call.
        """
        import inspect
        from workflows.activities import requirements_activity
        src = inspect.getsource(requirements_activity)
        assert "input.tenant_id" in src, (
            "requirements_activity must pass input.tenant_id in the ainvoke dict"
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
        """Exception handlers in design agent must not log str(exc) — only type name (SC#8)."""
        import inspect
        from agents_orchestrator.design_architecture_agent.agents import architecture
        src = inspect.getsource(architecture)
        # Check that str(e) is not in a logger.* call in the exception handler context
        # We allow print(f"... {e}") removal; the replacement should be logger.error with type(e).__name__
        # The key guard: the exception handler should NOT pass str(e) to logger
        assert "str(e)" not in src or "logger" not in src, (
            "architecture.py should not log str(e) in exception handler — use type(e).__name__ (SC#8)"
        )
