"""Unit tests for per-tenant rate-limit isolation (REQ-M3-11, T-M3-02).

AzureDevOpsConnector uses a class-level dict of per-tenant asyncio.Semaphores so
one tenant acquiring its semaphore cannot block another tenant. These tests
verify isolation and identity of the semaphore objects. No infra required.
"""
import asyncio

import pytest

pytestmark = pytest.mark.skip(
    reason="pre-existing import error — No module named 'azure' (azure-keyvault-secrets not installed); "
    "quarantined in milestone-4 Wave A"
)

try:
    from config.connectors.azure_devops import AzureDevOpsConnector
except (ImportError, ModuleNotFoundError):
    AzureDevOpsConnector = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tenant_isolation():
    """Two different tenants acquire without deadlock; both keys registered."""
    connector = AzureDevOpsConnector("https://dev.azure.com/org")
    await asyncio.wait_for(connector.rate_limit_manager("tenant-a"), timeout=2)
    await asyncio.wait_for(connector.rate_limit_manager("tenant-b"), timeout=2)
    assert "tenant-a" in AzureDevOpsConnector._tenant_semaphores
    assert "tenant-b" in AzureDevOpsConnector._tenant_semaphores


@pytest.mark.unit
@pytest.mark.asyncio
async def test_different_tenants_get_different_semaphores():
    """Distinct tenant_ids map to distinct Semaphore objects."""
    connector_one = AzureDevOpsConnector("https://dev.azure.com/org")
    connector_two = AzureDevOpsConnector("https://dev.azure.com/org")
    await connector_one.rate_limit_manager("tenant-x")
    await connector_two.rate_limit_manager("tenant-y")
    sem_x = AzureDevOpsConnector._tenant_semaphores["tenant-x"]
    sem_y = AzureDevOpsConnector._tenant_semaphores["tenant-y"]
    assert sem_x is not sem_y


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_tenant_gets_same_semaphore():
    """Repeated calls for the same tenant reuse the same Semaphore object."""
    connector = AzureDevOpsConnector("https://dev.azure.com/org")
    await connector.rate_limit_manager("tenant-shared")
    sem_first = AzureDevOpsConnector._tenant_semaphores["tenant-shared"]
    await connector.rate_limit_manager("tenant-shared")
    sem_second = AzureDevOpsConnector._tenant_semaphores["tenant-shared"]
    assert sem_first is sem_second
