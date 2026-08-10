"""CI exit gate checks for M2 milestone verification.

These three grep-based tests confirm that Wave 1-6 plans have removed all
legacy patterns from the active orchestrator package. They run with no infra
and are designed to be a hard gate in CI.

Each test uses subprocess.run so the assertion is identical to what a CI shell
script would run — no mocking, no import tricks.
"""
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[3]
_ORCHESTRATOR_PATH = str(_REPO_ROOT / "agentic_app" / "agents_orchestrator")


@pytest.mark.unit
def test_no_memorysaver_in_orchestrator():
    """grep -r 'MemorySaver' agents_orchestrator/ must return exit code 1 (no matches).

    M2-03 migrated all agent graphs to PostgresSaver via config.checkpoint.build_checkpointer.
    Any residual MemorySaver import in the orchestrator package means the migration is incomplete.
    """
    result = subprocess.run(
        ["grep", "-r", "MemorySaver", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found MemorySaver references in agents_orchestrator/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )


@pytest.mark.unit
def test_no_direct_anthropic_in_orchestrator():
    r"""grep -r 'anthropic\.Anthropic\(\)' agents_orchestrator/ must return exit code 1.

    M2-05 replaced all direct anthropic.Anthropic() client instantiations with
    ChatLiteLLM / litellm.acompletion via the LiteLLM proxy gateway. Any remaining
    direct client construction bypasses cost tracking, rate limiting, and retries.
    """
    result = subprocess.run(
        ["grep", "-r", r"anthropic\.Anthropic()", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found direct anthropic.Anthropic() calls in agents_orchestrator/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )


@pytest.mark.unit
def test_no_handoff_sentinel_in_orchestrator():
    """grep -r 'HANDOFF::' agents_orchestrator/ must return exit code 1.

    M2-02 / M2-06 replaced the string-sentinel HANDOFF:: pattern with typed
    RequirementsArtifact written to Postgres + Redis pub/sub. Any residual sentinel
    string means agent output is not being persisted through the artifact service.
    """
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "HANDOFF::", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        f"Found HANDOFF:: sentinel strings in agents_orchestrator/:\n"
        f"{result.stdout.decode(errors='replace')}"
    )
