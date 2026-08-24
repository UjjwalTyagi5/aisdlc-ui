"""Requirements has no capture hook today (unlike Code Review/Security/Testing/
Deployment/Documentation) — `_capture_requirements_artifacts` closes that gap by
mirroring a substantial reply (normalised Gherkin acceptance criteria, doc/BRD
summaries) into `requirements_artifacts.sections` as markdown, ACCUMULATING
distinct replies across turns (unlike `_capture_stage_report`'s overwrite-latest
semantics) so both the AC turn and the docs turn are kept.

Also covers `sections_from_run` rendering the new `requirements_artifacts` column
on reload/replay (markdown sections + a `requirements-files` file-tree)."""
from types import SimpleNamespace

import pytest

from agents_orchestrator.orchestrator import copilot_api
from shared.services.orchestrator.artifacts_view import sections_from_run


class _FakeAsyncCM:
    """Stand-in for `async with get_db_session_superuser() as s:` — mirrors the
    pattern used in tests/copilot/test_capture_stage_files.py and
    test_requirements_card_gating.py."""

    def __init__(self, run):
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: self._run)


class _FakeWebSocket:
    async def send_text(self, text):
        pass


def _patch_existing_run(monkeypatch, requirements_artifacts):
    run = SimpleNamespace(requirements_artifacts=requirements_artifacts)
    monkeypatch.setattr(copilot_api, "get_db_session_superuser", lambda: _FakeAsyncCM(run))


def _patch_persist_and_send(monkeypatch):
    persisted = []
    sent = []

    async def _fake_persist(run_id, tenant_id, column, payload, **kwargs):
        persisted.append((run_id, tenant_id, column, payload))

    async def _fake_send(websocket, payload):
        sent.append(payload)

    monkeypatch.setattr(copilot_api, "_persist_run_artifact", _fake_persist)
    monkeypatch.setattr(copilot_api, "_send", _fake_send)
    return persisted, sent


LONG_REPLY = "Given a user is on the login page\n" + ("When they submit valid credentials\n" * 20)


@pytest.mark.asyncio
async def test_appends_markdown_section_for_substantial_reply(monkeypatch):
    _patch_existing_run(monkeypatch, None)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    await copilot_api._capture_requirements_artifacts(
        "run-1", "t1", LONG_REPLY, _FakeWebSocket())

    assert len(persisted) == 1
    run_id, tenant_id, col, payload = persisted[0]
    assert run_id == "run-1"
    assert tenant_id == "t1"
    assert col == "requirements_artifacts"
    assert len(payload["sections"]) == 1
    sec = payload["sections"][0]
    assert sec["id"] == "requirements-doc-1"
    assert sec["stage"] == "requirements"
    assert sec["kind"] == "markdown"
    # _reply_artifact_title prefers the first markdown heading, else the first
    # non-empty line, over the flat "Requirements" fallback (distinguishable panel
    # titles across incremental captures) — LONG_REPLY has no heading, so its first
    # line is what's derived.
    assert sec["title"] == "Given a user is on the login page"
    assert sec["content"] == LONG_REPLY.strip()

    assert len(sent) == 1
    assert sent[0]["type"] == "artifact.ready"
    assert sent[0]["stage"] == "requirements"
    assert sent[0]["artifacts"] == payload["sections"]


@pytest.mark.asyncio
async def test_accumulates_distinct_replies_across_turns(monkeypatch):
    existing = {"sections": [{
        "id": "requirements-doc-1", "stage": "requirements", "kind": "markdown",
        "title": "Requirements", "content": "first substantial reply " * 20,
    }]}
    _patch_existing_run(monkeypatch, existing)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    second_reply = "a completely different substantial reply " * 20
    await copilot_api._capture_requirements_artifacts(
        "run-1", "t1", second_reply, _FakeWebSocket())

    assert len(persisted) == 1
    _, _, _, payload = persisted[0]
    assert len(payload["sections"]) == 2
    assert payload["sections"][0]["id"] == "requirements-doc-1"
    assert payload["sections"][1]["id"] == "requirements-doc-2"
    assert payload["sections"][1]["content"] == second_reply.strip()


@pytest.mark.asyncio
async def test_skips_duplicate_reply(monkeypatch):
    existing = {"sections": [{
        "id": "requirements-doc-1", "stage": "requirements", "kind": "markdown",
        "title": "Requirements", "content": LONG_REPLY.strip(),
    }]}
    _patch_existing_run(monkeypatch, existing)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    await copilot_api._capture_requirements_artifacts(
        "run-1", "t1", LONG_REPLY, _FakeWebSocket())

    assert persisted == []
    assert sent == []


@pytest.mark.asyncio
async def test_short_reply_writes_nothing(monkeypatch):
    _patch_existing_run(monkeypatch, None)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    await copilot_api._capture_requirements_artifacts(
        "run-1", "t1", "too short", _FakeWebSocket())

    assert persisted == []
    assert sent == []


@pytest.mark.asyncio
async def test_caps_sections_at_ten(monkeypatch):
    existing = {"sections": [
        {"id": f"requirements-doc-{i}", "stage": "requirements", "kind": "markdown",
         "title": "Requirements", "content": f"reply number {i} " * 20}
        for i in range(1, 11)
    ]}
    _patch_existing_run(monkeypatch, existing)
    persisted, _sent = _patch_persist_and_send(monkeypatch)

    new_reply = "yet another distinct substantial reply " * 20
    await copilot_api._capture_requirements_artifacts(
        "run-1", "t1", new_reply, _FakeWebSocket())

    assert len(persisted) == 1
    _, _, _, payload = persisted[0]
    assert len(payload["sections"]) == 10
    assert payload["sections"][-1]["content"] == new_reply.strip()
    # oldest entry (requirements-doc-1) dropped off the front once capped.
    assert payload["sections"][0]["id"] == "requirements-doc-2"


def test_sections_from_run_renders_requirements_artifacts():
    run = SimpleNamespace(
        requirements_payload=None,
        requirements_artifacts={
            "sections": [{
                "id": "requirements-doc-1", "stage": "requirements", "kind": "markdown",
                "title": "Requirements", "content": "Given ... When ... Then ...",
            }],
            "has_files": True,
        },
        design_artifacts=None,
        development_artifacts=None,
        code_review_artifacts=None,
        security_artifacts=None,
        testing_artifacts=None,
        deployment_artifacts=None,
        documentation_artifacts=None,
    )

    out = sections_from_run(run)

    ids = [s["id"] for s in out]
    assert "requirements-doc-1" in ids
    assert "requirements-files" in ids
    file_tree = next(s for s in out if s["id"] == "requirements-files")
    assert file_tree["kind"] == "file-tree"
    assert file_tree["source"] == "requirements"
