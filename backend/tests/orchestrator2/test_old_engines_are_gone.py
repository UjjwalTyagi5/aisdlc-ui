"""The old engines are retired.

`copilot_api.py` gave a system prompt to THREE of nine agents and had no `plan` branch
at all, both failing soft — an agent that answered vaguely or not at all, never an
error anyone could chase. That is the defect that prompted this rebuild.
`orchestrator_api.py` was reference-only from the start.

Both stayed alive through Phases 1-4 deliberately: until Phase 4 the Copilot was the
only surface that actually ran agents, and deleting it earlier would have left the
product with no working orchestration.
"""
import importlib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

_RETIRED = (
    "agents_orchestrator.orchestrator.copilot_api",
    "agents_orchestrator.orchestrator.orchestrator_api",
    "agents_orchestrator.orchestrator.copilot_cards",
    "agents_orchestrator.orchestrator.stage_switch",
)


@pytest.mark.parametrize("module", _RETIRED)
def test_the_module_cannot_be_imported(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_the_package_directory_is_gone():
    assert not (BACKEND / "agents_orchestrator" / "orchestrator").exists()
    assert not (BACKEND / "tests" / "copilot").exists()


def test_nothing_still_imports_them():
    """A stale import is a BOOT failure, not a test failure, so it is worth a check
    that reads the tree rather than waiting for the app to start and discovering it."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if any(part in (".venv", "__pycache__", "node_modules") for part in path.parts):
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            if any(m.split(".", 1)[1] in stripped for m in
                   ("x.orchestrator.copilot_api", "x.orchestrator.orchestrator_api",
                    "x.orchestrator.copilot_cards", "x.orchestrator.stage_switch")):
                offenders.append(f"{path.relative_to(BACKEND)}: {stripped}")
    assert offenders == [], "\n".join(offenders)


def test_the_copilot_rest_endpoints_are_gone():
    from shared.routers.runs import runs_router

    paths = {r.path for r in runs_router.routes}
    for gone in ("/{run_id}/copilot/advance", "/{run_id}/copilot/set-stage",
                 "/{run_id}/copilot/cancel-turn"):
        assert gone not in paths, f"{gone} is still registered"


def test_the_orchestrator2_socket_survives():
    """The one that must NOT be deleted. Removing the wrong mount is the obvious way to
    break this task, and nothing else in this file would notice — a missing mount does
    not fail the boot, it just makes the product stop working."""
    import process_api

    paths = {getattr(r, "path", "") for r in process_api.app.routes}
    assert any("orchestrator2" in p for p in paths), sorted(p for p in paths if "agent" in p)


def test_the_runs_api_still_serves_the_orchestrator():
    """Deliverables, transcript and workspace reads all live on the runs router, which
    Task 5 edits. They are what the Deliverables tab and the code tree read."""
    from shared.routers.runs import runs_router

    paths = {r.path for r in runs_router.routes}
    for kept in ("/{run_id}/deliverables", "/{run_id}/artifacts", "/{run_id}/transcript"):
        assert kept in paths, f"{kept} was removed by accident"
