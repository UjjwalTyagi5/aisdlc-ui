"""Every piece of per-run state the agents' tools read is set by the turn.

THIS FILE EXISTS BECAUSE THE SAME BUG HAPPENED SEVEN TIMES.

`orchestrator2` loads each agent's GRAPH, not the `*_agent_api.py` wrapper around it
(decision D10a). The tools inside those graphs take no arguments for the project, the
user or the run — they read `config/ws_helper` contextvars that the wrapper sets. Every
one this engine forgot became a defect that only a live run could find, and each was
fixed alone, as if it were the last:

  1. the project's connector      — "no ADO credentials are configured"
  2. the project's MCP tools      — silently missing, no error at all
  3. `session_id` / `user_id`     — "No files yet" over a repo that had been cloned
  4. `runs.development_artifacts` — the code tree and PR link could never appear
  5. `tenant_id`                  — "requires a signed-in approver … running in the
                                     background", for a user sitting right there
  6. `consequential_approved`     — "yes" recorded nowhere, so the gate looped forever
  7. `project_id`                 — "no Azure DevOps board is connected to this
                                     project", on a project with one connected

Fixing the seventh by hand and waiting for the eighth is the actual bug. This test
enumerates what the standalone wrappers provide and fails when the Orchestrator's turn
does not provide it too — so the next omission is a red test rather than a demo that
falls over.
"""
import inspect
import re

import pytest

from agents_orchestrator.orchestrator2 import dispatch


_TURN_SOURCE = inspect.getsource(dispatch.run_agent)


#: What a turn MUST establish, and what breaks without each. Every entry was a real,
#: reported failure except where marked.
_REQUIRED = {
    "set_session_id": "the agent's working directory and its LangGraph thread",
    "set_user_id": "whose credential the connector resolves, and the file path",
    "set_tenant_id": "the consequential gate cannot identify the actor without it",
    "set_project_id": "which project's boards, connectors and models the tools reach",
    "set_run_id": "what the agent's own tools attribute their work to",
    "set_provider_kind": "azure_devops vs github vs jira, for the tools' defaults",
    "set_consequential_approved": "whether the user approved THIS action THIS turn",
}

#: Deliberately NOT set, with the reason. Listed so "we forgot" and "we decided" are
#: distinguishable — an omission with no entry here is the eighth instance.
_DELIBERATELY_ABSENT = {
    "set_websocket_context": (
        "orchestrator2 yields its events to its own socket rather than broadcasting "
        "through ConnectionManager. Registering one here would send this run's frames "
        "to whatever else is connected — the cross-session leak fixed in 48a60b42."
    ),
    "set_agent_folder": (
        "the agents fall back to their own per-agent directory, which is what "
        "`_run_stage_output_dir` already resolves. Setting one would move the files "
        "out from under the Deliverables panel."
    ),
}


@pytest.mark.parametrize("setter", sorted(_REQUIRED))
def test_the_turn_establishes(setter):
    """One per field, so a failure names the missing one rather than 'context'."""
    assert f"{setter}(" in _TURN_SOURCE, (
        f"a turn never calls {setter} — {_REQUIRED[setter]}"
    )


@pytest.mark.parametrize("setter", sorted(_REQUIRED))
def test_the_turn_clears(setter):
    """Contextvars outlive the coroutine that set them. Anything left behind is
    inherited by whatever runs next on this worker — a standalone agent request, or a
    background job — which is how a consent flag or a tenant becomes somebody else's.
    """
    if setter == "set_session_id":
        # Has its own token-based reset, which is stricter than assignment.
        assert "reset_session_id" in _TURN_SOURCE
        return
    # Cleared to a falsey value in the turn's `finally`.
    assert re.search(rf"{setter}\((?:None|\"\"|''|False)\)", _TURN_SOURCE), (
        f"{setter} is set but never cleared, so it leaks past the turn"
    )


def test_every_setter_the_wrappers_use_is_either_required_or_explained():
    """THE POINT OF THE FILE. Reads the standalone wrappers and compares.

    A contextvar the wrappers set, that this engine neither sets nor has a recorded
    reason to skip, is the next instance of the pattern — found here rather than by a
    user whose board write silently does nothing.
    """
    import pathlib

    root = pathlib.Path(inspect.getfile(dispatch)).resolve().parents[2]
    wrapper_setters: set[str] = set()
    for path in root.rglob("*_agent_api.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        wrapper_setters.update(re.findall(r"\b(set_[a-z_]+)\(", text))

    # Only the ones ws_helper actually owns; the wrappers set plenty of their own state.
    from config import ws_helper

    owned = {n for n in wrapper_setters if hasattr(ws_helper, n)}
    accounted = set(_REQUIRED) | set(_DELIBERATELY_ABSENT)
    unaccounted = owned - accounted

    assert not unaccounted, (
        "the standalone wrappers establish per-run state that the Orchestrator neither "
        "sets nor records a reason to skip: " + ", ".join(sorted(unaccounted)) +
        ". Each previous one of these was a live defect — decide which it is and add "
        "it to _REQUIRED or _DELIBERATELY_ABSENT."
    )


def test_the_reasons_for_skipping_are_not_empty():
    """A deliberate omission with no reason is indistinguishable from a forgotten one,
    which is what this file exists to prevent."""
    for setter, reason in _DELIBERATELY_ABSENT.items():
        assert len(reason) > 40, f"{setter} is skipped without a real reason"
