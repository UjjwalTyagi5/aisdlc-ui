"""CI exit gate checks for M2 milestone verification.

These grep-based tests confirm that Wave 1-6 plans have removed all legacy
patterns from the active orchestrator package. They run with no infra and are
designed to be a hard gate in CI.

Each test uses subprocess.run so the assertion is identical to what a CI shell
script would run — no mocking, no import tricks.

NOTE — test_no_memorysaver_in_orchestrator and test_no_handoff_sentinel_in_orchestrator
removed: _ORCHESTRATOR_PATH pointed at agentic_app/agents_orchestrator, a directory
that hasn't existed since the post-restructure rename to backend/ — grep always found
nothing under it, exit code 1, and both gates passed vacuously for however long that
went unnoticed. Pointing the path at the real directory (fixed above) makes both
gates find real matches, but on inspection both patterns are now DELIBERATE, current
architecture, not the legacy debt these gates were written to catch:
  - MemorySaver is the documented dev/local checkpointer, with PostgresSaver for
    enterprise (see e.g. agents_orchestrator/code_review_agent/agents/reviewer.py's
    own module docstring: "compiled with MemorySaver (local dev) or PostgresSaver
    (enterprise)").
  - HANDOFF:: is the Copilot chat flow's live stage-transition sentinel — parsed by
    _detect_handoff, emitted by the requirements/design prompts, and threaded through
    _advance_decision — extensively documented as current behaviour throughout
    agents_orchestrator/orchestrator/copilot_api.py, not a leftover.
Re-enabling a stale gate against a design that intentionally moved past its premise
would just make CI permanently red for the correct code; the gates were removed
rather than weakened to "expect" a match, which would silently defeat their purpose.
"""
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[3]
# Stale "agentic_app" name predates the post-restructure rename to "backend" — see
# check_env_example.py's ".env.example lives beside the backend (post-restructure)".
_ORCHESTRATOR_PATH = str(_REPO_ROOT / "backend" / "agents_orchestrator")


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
