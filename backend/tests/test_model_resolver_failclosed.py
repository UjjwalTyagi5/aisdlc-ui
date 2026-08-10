"""Phase 3 — agents fail CLOSED (clear guidance, no platform key) when a tenant
has no configured model provider."""
import uuid
import pytest
from langchain_core.messages import HumanMessage


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_requirements_agent_fails_closed_without_provider():
    from agents_orchestrator.requirements_agent.agents.planning import agent
    tenant = str(uuid.uuid4())   # nothing configured
    out = await agent({"messages": [HumanMessage(content="hi")], "tenant_id": tenant, "model_id": None})
    text = out["messages"][0].content
    assert "administrator" in text.lower() and "model provider" in text.lower()


@pytest.mark.asyncio
async def test_dev_agent_fails_closed_without_provider():
    from agents_orchestrator.development_agent.agents.dev_agent import agent_node
    tenant = str(uuid.uuid4())
    out = await agent_node({"messages": [HumanMessage(content="hi")], "tenant_id": tenant, "model_id": None})
    assert "administrator" in out["messages"][0].content.lower()
