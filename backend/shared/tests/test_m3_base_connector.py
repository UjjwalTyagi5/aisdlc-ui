"""Unit tests for the BaseConnector ABC contract (milestone-3).

Verifies the abstract surface declared in config/connectors/base.py: the 10
required abstract members are present, the ABC cannot be instantiated directly,
and ConnectorNotAvailableError is a plain Exception subclass.
"""
import inspect
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.connectors.base import BaseConnector, ConnectorNotAvailableError


@pytest.mark.unit
def test_abstract_methods_present():
    """BaseConnector declares all 10 required abstract members."""
    abstract_methods = {
        name
        for name, method in inspect.getmembers(BaseConnector)
        if getattr(method, "__isabstractmethod__", False)
    }
    required = {
        "connector_name",
        "display_name",
        "auth_adapter",
        "capability_manifest",
        "webhook_receiver",
        "rate_limit_manager",
        "health_check",
        "audit_emitter",
        "read_adapter",
        "write_adapter",
    }
    assert required <= abstract_methods, (
        f"Missing abstract methods: {required - abstract_methods}"
    )


@pytest.mark.unit
def test_base_connector_not_instantiable():
    """BaseConnector is abstract — direct construction raises TypeError."""
    with pytest.raises(TypeError):
        BaseConnector()


@pytest.mark.unit
def test_connector_not_available_error_is_exception():
    """ConnectorNotAvailableError is a plain Exception subclass."""
    assert issubclass(ConnectorNotAvailableError, Exception)
