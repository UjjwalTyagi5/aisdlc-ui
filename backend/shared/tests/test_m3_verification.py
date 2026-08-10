"""CI exit gate checks for M3 milestone verification.

These grep-based tests confirm that the milestone-3 connector-hub migration has
removed every legacy provider pattern from the active orchestrator package and
that no direct backend SDK import leaks into agent code. They run with no infra
and are designed to be a hard gate in CI.

Each test uses subprocess.run so the assertion is identical to what a CI shell
script would run — no mocking, no import tricks.
"""
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[3]
_ORCHESTRATOR_PATH = str(_REPO_ROOT / "agentic_app" / "agents_orchestrator")
_AGENTIC_APP_PATH = str(_REPO_ROOT / "agentic_app")


@pytest.mark.unit
def test_no_direct_sdk_import():
    """grep -r 'from azure.devops' agents_orchestrator/ must return exit code 1.

    Agent code must never import a backend SDK directly — all backend access
    flows through the injected connector (config.connectors.context.get_connector).
    """
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "from azure.devops", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found direct azure.devops imports in agents_orchestrator/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )


@pytest.mark.unit
def test_no_get_pm_provider():
    """grep -r 'get_pm_provider' agents_orchestrator/ must return exit code 1.

    milestone-3-03 replaced the get_pm_provider() factory with get_connector()
    contextvar DI. Any residual call means the migration is incomplete.
    """
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "get_pm_provider", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found get_pm_provider references in agents_orchestrator/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )


@pytest.mark.unit
def test_no_get_provider_helper():
    """grep -r '_get_provider' agents_orchestrator/ must return exit code 1.

    The local _get_provider() / _get_design_provider() helper functions were
    deleted in milestone-3-03 in favour of get_connector().
    """
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "_get_provider", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found _get_provider helper references in agents_orchestrator/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )


@pytest.mark.unit
def test_no_config_providers_import_in_orchestrator():
    """The legacy config.providers.* import pattern must be gone repo-wide.

    The legacy provider tree was deleted in milestone-3.1; connectors live under
    config.connectors.*. The grep pattern is assembled from fragments so this
    test's own source does not self-match the repo-wide gate.
    """
    pattern = "from config." + "providers"
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", pattern, _AGENTIC_APP_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found legacy provider-tree imports in agentic_app/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )


@pytest.mark.unit
def test_no_base_project_management_provider():
    """The legacy provider ABC must be gone repo-wide (milestone-3.1).

    Agent and connector code is fully migrated to BaseConnector. milestone-3.1
    deleted config/providers/, so this gate is now widened from the orchestrator
    package to the whole agentic_app tree (as anticipated by milestone-3-03).
    The grep pattern is assembled from fragments so this test's own source does
    not self-match the repo-wide gate.
    """
    pattern = "BaseProjectManagement" + "Provider"
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", pattern, _AGENTIC_APP_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found legacy provider ABC references in agentic_app/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )
