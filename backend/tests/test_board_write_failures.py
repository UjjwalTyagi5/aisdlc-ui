"""Two defects from one live Requirements chat, where a user tried to create stories.

The transcript is the specification here. A user asked for two stories on the board,
approved, and got:

    Error creating User Story 'Initialize SDLC Agentic Platform skeleton':
    Client error '400 Bad Request' for url
    'https://acme.atlassian.net/rest/api/3/issue'

Two separate faults in one line:

  1. The 400 itself. The board tools are provider-neutral and speak ADO's vocabulary —
     azure_devops.create_item defaults to item_type="User Story" — so that is what
     arrives whichever board is wired. Jira's default schemes have no "User Story"
     type and reject it.
  2. The message. It names the tenant's Jira instance and the API path, and says
     nothing about WHAT was wrong. Both halves are bad: the URL should never reach the
     model's context or the saved transcript, and "400 Bad Request" gives the agent
     nothing to correct.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── 1. the vocabulary the tools actually speak ───────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("User Story", "Story"),
        ("user story", "Story"),
        ("  USER STORY  ", "Story"),
        ("Product Backlog Item", "Story"),
        ("PBI", "Story"),
        ("Requirement", "Story"),
        ("Issue", "Task"),
    ],
)
def test_ado_vocabulary_is_translated_for_jira(given, expected):
    from config.connectors.jira import JiraConnector

    assert JiraConnector._jira_item_type(given) == expected


@pytest.mark.unit
@pytest.mark.parametrize("native", ["Story", "Bug", "Task", "Epic"])
def test_types_jira_already_has_are_untouched(native):
    from config.connectors.jira import JiraConnector

    assert JiraConnector._jira_item_type(native) == native


@pytest.mark.unit
def test_an_unknown_type_is_passed_through_not_guessed_at():
    """A project with a custom issue-type scheme must keep working. Mapping an
    unrecognised type onto a default would break exactly the setups we cannot see."""
    from config.connectors.jira import JiraConnector

    assert JiraConnector._jira_item_type("Spike") == "Spike"
    assert JiraConnector._jira_item_type("") == ""


# ── 2. the failure message ───────────────────────────────────────────────────


def _http_error(status: int, body) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://acme.atlassian.net/rest/api/3/issue")
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.unit
def test_the_instance_url_never_reaches_the_message():
    """THE HEADLINE. str(exc) on an httpx error renders the full URL, and every board
    tool used to interpolate it straight into what the model reads."""
    from agents_orchestrator.requirements_agent.agents.planning import _board_error

    msg = _board_error(_http_error(400, {"errors": {"issuetype": "invalid"}}))
    assert "acme.atlassian.net" not in msg
    assert "/rest/api/3/" not in msg
    assert "https://" not in msg


@pytest.mark.unit
def test_jira_field_errors_are_surfaced_because_they_are_the_actionable_part():
    """The reason lives in the response BODY, not in the exception's own message.
    Surfacing it is what lets the agent correct itself instead of retrying blindly."""
    from agents_orchestrator.requirements_agent.agents.planning import _board_error

    msg = _board_error(
        _http_error(400, {"errors": {"issuetype": "The issue type selected is invalid."}})
    )
    assert "issuetype" in msg
    assert "The issue type selected is invalid." in msg
    assert "400" in msg


@pytest.mark.unit
def test_jira_error_messages_list_is_surfaced_too():
    from agents_orchestrator.requirements_agent.agents.planning import _board_error

    msg = _board_error(_http_error(400, {"errorMessages": ["Field 'summary' is required."]}))
    assert "Field 'summary' is required." in msg


@pytest.mark.unit
def test_azure_devops_message_shape_is_surfaced():
    """ADO answers {"message": "..."} rather than Jira's two fields."""
    from agents_orchestrator.requirements_agent.agents.planning import _board_error

    msg = _board_error(_http_error(400, {"message": "TF401347: work item type invalid."}))
    assert "TF401347" in msg


@pytest.mark.unit
@pytest.mark.parametrize(("status", "needle"), [(401, "credential"), (403, "credential"), (404, "project name")])
def test_auth_and_not_found_get_an_actionable_sentence(status, needle):
    from agents_orchestrator.requirements_agent.agents.planning import _board_error

    msg = _board_error(_http_error(status, {}))
    assert needle in msg
    assert "acme.atlassian.net" not in msg


@pytest.mark.unit
def test_a_non_json_body_does_not_crash_the_error_path():
    """An error handler that throws replaces a useful message with a stack trace."""
    from agents_orchestrator.requirements_agent.agents.planning import _board_error

    request = httpx.Request("POST", "https://acme.atlassian.net/rest/api/3/issue")
    response = httpx.Response(500, text="<html>gateway</html>", request=request)
    msg = _board_error(httpx.HTTPStatusError("boom", request=request, response=response))
    assert "500" in msg
    assert "acme.atlassian.net" not in msg


@pytest.mark.unit
def test_a_non_http_exception_falls_back_to_the_type_name_only():
    from agents_orchestrator.requirements_agent.agents.planning import _board_error

    msg = _board_error(RuntimeError("connection to https://acme.atlassian.net failed"))
    assert "RuntimeError" in msg
    assert "acme.atlassian.net" not in msg


# ── the sweep: no board tool interpolates a raw exception any more ───────────


@pytest.mark.unit
def test_no_connector_call_site_interpolates_the_raw_exception():
    """16 sites did. The three that remain are parsing the MODEL's own JSON, where the
    raw error is the useful thing and no connector is involved."""
    import inspect

    from agents_orchestrator.requirements_agent.agents import planning

    raw = [
        line.strip()
        for line in inspect.getsource(planning).splitlines()
        if "{exc}" in line and "_board_error" not in line
    ]
    assert all("JSON" in line for line in raw), raw
