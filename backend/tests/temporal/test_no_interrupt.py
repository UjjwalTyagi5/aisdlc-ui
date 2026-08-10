"""REQ-M10-07: structural guard — zero `interrupt(` calls in agents_orchestrator/.

Tier: unit (pure file scan — no Temporal server, no DB, no LLM).

milestone-10 moved all human-in-the-loop suspension to Temporal
(`within_agent_clarification` signal + `_run_phase_with_clarification`),
making LangGraph a pure execution engine inside agents_orchestrator/. This
test locks that invariant in permanently: if a future change reintroduces a
LangGraph `interrupt()` call inside agents_orchestrator/, this test fails
with the offending file:line.

Implemented as a pure-Python recursive scan (NOT a shelled-out `grep`) so it
is green on win32 dev boxes (no grep binary) and in CI alike.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# agents_orchestrator/ lives at agentic_app/agents_orchestrator/; this test
# file is at agentic_app/tests/temporal/test_no_interrupt.py, so
# parents[0] = tests/temporal, parents[1] = tests, parents[2] = agentic_app.
_AGENTS_ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / "agents_orchestrator"

# The call form — not the bare substring "interrupt" — so identifiers like
# "interrupted" or "self.interrupt_flag" do not false-positive.
_INTERRUPT_CALL_TOKEN = "interrupt("


@pytest.mark.unit
def test_no_interrupt_in_agents() -> None:
    """Zero `interrupt(` calls anywhere under agents_orchestrator/ (REQ-M10-07).

    Comment-only lines (stripped line starts with `#`) are excluded so a
    docstring/comment that merely mentions interrupt() does not
    self-invalidate the guard.
    """
    assert _AGENTS_ORCHESTRATOR_DIR.is_dir(), (
        f"agents_orchestrator/ directory not found at {_AGENTS_ORCHESTRATOR_DIR} "
        "— path resolution broken, cannot run the no-interrupt scan"
    )

    py_files = list(_AGENTS_ORCHESTRATOR_DIR.rglob("*.py"))
    assert py_files, (
        f"no .py files found under {_AGENTS_ORCHESTRATOR_DIR} — scan would be "
        "vacuously true; path resolution likely broken"
    )

    offenders: list[str] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _INTERRUPT_CALL_TOKEN in line:
                offenders.append(f"{path}:{lineno}")

    assert not offenders, (
        "interrupt( call(s) found in agents_orchestrator/ — LangGraph must be "
        "a pure execution engine; HITL suspension belongs to Temporal "
        "(within_agent_clarification). Offending locations:\n"
        + "\n".join(offenders)
    )
