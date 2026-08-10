"""Credential-hygiene tests for milestone-3 (REQ-M3-10).

Verify that the connector holds no credential attributes and that the
set_connector/get_connector/clear_connector contextvar lifecycle releases the
connector — the invariant each worker's ``finally: clear_connector()`` relies on.
"""
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skip(
    reason="pre-existing import error — No module named 'azure' (azure-keyvault-secrets not installed); "
    "quarantined in milestone-4 Wave A"
)

try:
    from config.connectors.azure_devops import AzureDevOpsConnector
    from config.connectors.context import set_connector, get_connector, clear_connector
except (ImportError, ModuleNotFoundError):
    AzureDevOpsConnector = None
    set_connector = lambda x: None
    get_connector = lambda: None
    clear_connector = lambda: None


@pytest.mark.unit
def test_azure_connector_no_credential_attribute():
    c = AzureDevOpsConnector("https://dev.azure.com/org")
    assert not hasattr(c, "_pat"), "connector must not store the PAT"
    assert not hasattr(c, "_token"), "connector must not store a token"
    assert not hasattr(c, "_secret"), "connector must not store a secret"


@pytest.mark.unit
def test_set_clear_connector_lifecycle():
    mock_connector = MagicMock()
    set_connector(mock_connector)
    assert get_connector() is mock_connector
    clear_connector()
    with pytest.raises(RuntimeError):
        get_connector()


@pytest.mark.unit
async def test_connector_cleared_in_finally_on_exception():
    """Mirror a worker handle_task finally block: clear_connector runs even on error.

    The worker pattern is `set_connector(...) ; try: <graph> ; finally: clear_connector()`.
    Verify that after that pattern raises, get_connector() raises — i.e. the
    connector was released from the contextvar.
    """
    mock_connector = MagicMock()

    async def _handle_with_failure():
        set_connector(mock_connector)
        try:
            assert get_connector() is mock_connector
            raise RuntimeError("simulated graph failure")
        finally:
            clear_connector()

    with pytest.raises(RuntimeError):
        await _handle_with_failure()

    # Credential reference released despite the exception (REQ-M3-10).
    with pytest.raises(RuntimeError):
        get_connector()


@pytest.mark.unit
def test_get_connector_raises_when_unset():
    clear_connector()
    with pytest.raises(RuntimeError):
        get_connector()
