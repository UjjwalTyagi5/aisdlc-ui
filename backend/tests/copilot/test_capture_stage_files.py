"""Task 2 — `_capture_stage_files` persists downstream generators' in-memory output
(Security SBOM/findings, Code Review findings/patches, Deployment staged files) to a
run-keyed directory on disk, so the Copilot artifacts panel can browse them the same
way it already browses Development's workspace (RepoFileTree + CodeViewer).

Session shapes (ground truth read from each agent's session_state.py / tools):
- security_agent / code_review_agent: session.last_artifact is the
  SecurityArtifact / CodeReviewArtifact `.model_dump()` — `sbom` (security only) and
  `findings` (both) are top-level keys; each finding may carry `autofix_patch`.
- deployment_agent: session.staged_files is `[{path, language, contents}]` — NOTE the
  field is `contents` (plural), not `content`; the implementation accepts either.
"""
from types import SimpleNamespace

import pytest

from agents_orchestrator.orchestrator import copilot_api


class _FakeWebSocket:
    async def send_text(self, text):
        pass


def _patch_generated_dir(monkeypatch, tmp_path):
    """Route the three generated-file writers at a tmp dir instead of the real
    `{FILES}/system/orchestrator/<run>/generated/<stage>` tree."""
    def _fake(run_id, stage):
        d = tmp_path / run_id / stage
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    monkeypatch.setattr(copilot_api, "_generated_stage_dir", _fake)
    return tmp_path


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


@pytest.mark.asyncio
async def test_security_writes_sbom_and_findings(tmp_path, monkeypatch):
    out_dir = _patch_generated_dir(monkeypatch, tmp_path)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    import agents_orchestrator.security_agent.config.session_state as sec_state
    session = SimpleNamespace(last_artifact={
        "sbom": [{"name": "x", "version": "1.0"}],
        "findings": [{"id": "S-001", "title": "f", "severity": "high"}],
    })
    monkeypatch.setattr(sec_state, "get_session", lambda rid: session)

    result = await copilot_api._capture_stage_files(
        "security", "run-1", "t1", "p1", _FakeWebSocket())

    assert result is True
    sbom_file = out_dir / "run-1" / "security" / "sbom.json"
    findings_file = out_dir / "run-1" / "security" / "findings.md"
    assert sbom_file.exists()
    assert '"name": "x"' in sbom_file.read_text()
    assert findings_file.exists()
    assert "S-001" in findings_file.read_text()
    assert len(persisted) == 1
    assert len(sent) == 1
    assert sent[0]["type"] == "artifact.ready"
    assert sent[0]["artifacts"][0]["kind"] == "file-tree"


@pytest.mark.asyncio
async def test_code_review_writes_findings_and_patch(tmp_path, monkeypatch):
    out_dir = _patch_generated_dir(monkeypatch, tmp_path)
    _patch_persist_and_send(monkeypatch)

    import agents_orchestrator.code_review_agent.config.session_state as cr_state
    session = SimpleNamespace(last_artifact={
        "findings": [{
            "id": "F-001", "severity": "medium", "description": "issue",
            "autofix_patch": "--- a\n+++ b\n",
        }],
    })
    monkeypatch.setattr(cr_state, "get_session", lambda rid: session)

    result = await copilot_api._capture_stage_files(
        "code_review", "run-2", "t1", "p1", _FakeWebSocket())

    assert result is True
    findings_file = out_dir / "run-2" / "code_review" / "findings.md"
    patch_file = out_dir / "run-2" / "code_review" / "fix_1.patch"
    assert findings_file.exists()
    assert "F-001" in findings_file.read_text()
    assert patch_file.exists()
    assert patch_file.read_text() == "--- a\n+++ b\n"


@pytest.mark.asyncio
async def test_deployment_writes_staged_files_contents_field(tmp_path, monkeypatch):
    out_dir = _patch_generated_dir(monkeypatch, tmp_path)
    _patch_persist_and_send(monkeypatch)

    import agents_orchestrator.deployment_agent.config.session_state as dep_state
    session = SimpleNamespace(staged_files=[
        {"path": "Dockerfile", "language": "dockerfile", "contents": "FROM x"},
    ])
    monkeypatch.setattr(dep_state, "get_session", lambda rid: session)

    result = await copilot_api._capture_stage_files(
        "deployment", "run-3", "t1", "p1", _FakeWebSocket())

    assert result is True
    written = out_dir / "run-3" / "deployment" / "Dockerfile"
    assert written.exists()
    assert written.read_text() == "FROM x"


@pytest.mark.asyncio
async def test_deployment_accepts_content_fallback_field(tmp_path, monkeypatch):
    out_dir = _patch_generated_dir(monkeypatch, tmp_path)
    _patch_persist_and_send(monkeypatch)

    import agents_orchestrator.deployment_agent.config.session_state as dep_state
    session = SimpleNamespace(staged_files=[
        {"path": "k8s/deploy.yaml", "content": "kind: Deployment"},
    ])
    monkeypatch.setattr(dep_state, "get_session", lambda rid: session)

    result = await copilot_api._capture_stage_files(
        "deployment", "run-4", "t1", "p1", _FakeWebSocket())

    assert result is True
    written = out_dir / "run-4" / "deployment" / "k8s" / "deploy.yaml"
    assert written.exists()
    assert written.read_text() == "kind: Deployment"


@pytest.mark.asyncio
async def test_empty_session_returns_false_and_writes_nothing(tmp_path, monkeypatch):
    out_dir = _patch_generated_dir(monkeypatch, tmp_path)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    import agents_orchestrator.security_agent.config.session_state as sec_state
    monkeypatch.setattr(sec_state, "get_session", lambda rid: SimpleNamespace(last_artifact=None))

    result = await copilot_api._capture_stage_files(
        "security", "run-5", "t1", "p1", _FakeWebSocket())

    assert result is False
    assert not (out_dir / "run-5" / "security").exists() or not list((out_dir / "run-5" / "security").iterdir())
    assert persisted == []
    assert sent == []


@pytest.mark.asyncio
async def test_unknown_stage_returns_false():
    result = await copilot_api._capture_stage_files(
        "not_a_real_stage", "run-6", "t1", "p1", _FakeWebSocket())
    assert result is False


@pytest.mark.asyncio
async def test_requirements_stage_checks_output_dir(tmp_path, monkeypatch):
    """Requirements writes its docx (BRD/MoM/Risk Register) to
    `{FILES}/<user>/requirements_agent/<run_id>/output` on its own — like
    Testing/Documentation, this only checks whether that dir has anything in it."""
    persisted, sent = _patch_persist_and_send(monkeypatch)

    async def _fake_output_dir(run_id, stage, project_id=None):
        assert stage == "requirements"
        d = tmp_path / "alice" / "requirements_agent" / run_id / "output"
        d.mkdir(parents=True, exist_ok=True)
        (d / "BRD.docx").write_bytes(b"fake")
        return str(d)

    monkeypatch.setattr(
        "shared.routers.runs._run_stage_output_dir", _fake_output_dir
    )

    result = await copilot_api._capture_stage_files(
        "requirements", "run-10", "t1", "p1", _FakeWebSocket())

    assert result is True
    assert len(sent) == 1
    emitted_ids = [a["id"] for a in sent[0]["artifacts"]]
    assert "requirements-files" in emitted_ids


class _FakeAsyncCM:
    """Stand-in for the `async with get_db_session_for_tenant(...) as s:` block —
    `s.execute(...)` returns an object whose `.scalar_one_or_none()` yields the
    fake `run` row carrying whatever `_capture_stage_report` already persisted."""

    def __init__(self, run):
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: self._run)


def _patch_db_existing_artifacts(monkeypatch, column: str, sections: list):
    """Simulate `_capture_stage_report` having already persisted a report section
    for this run+stage this turn, so `_capture_stage_files`'s DB re-fetch of
    `existing` sees it (this is how C1's emit — and C3's persisted-sections
    exclusion — get exercised deterministically, independent of a real DB)."""
    run = SimpleNamespace(**{column: {"sections": sections, "markdown": "..."}})
    monkeypatch.setattr(copilot_api, "get_db_session_for_tenant", lambda tenant_id: _FakeAsyncCM(run))


@pytest.mark.asyncio
async def test_emit_carries_report_and_file_tree_c1(tmp_path, monkeypatch):
    """C1: when a report section was already persisted this turn, the
    `artifact.ready` emitted here must carry BOTH sections so the frontend's
    per-stage replace semantics don't drop the Report when this (second) event
    for the stage lands."""
    _patch_generated_dir(monkeypatch, tmp_path)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    report_section = {"id": "security-report", "stage": "security", "kind": "markdown",
                       "title": "Security Report", "content": "..."}
    _patch_db_existing_artifacts(monkeypatch, "security_artifacts", [report_section])

    import agents_orchestrator.security_agent.config.session_state as sec_state
    session = SimpleNamespace(last_artifact={
        "sbom": [{"name": "x", "version": "1.0"}],
        "findings": [{"id": "S-001", "title": "f", "severity": "high"}],
    })
    monkeypatch.setattr(sec_state, "get_session", lambda rid: session)

    result = await copilot_api._capture_stage_files(
        "security", "run-7", "t1", "p1", _FakeWebSocket())

    assert result is True
    assert len(sent) == 1
    emitted_ids = [a["id"] for a in sent[0]["artifacts"]]
    assert "security-report" in emitted_ids
    assert "security-files" in emitted_ids


@pytest.mark.asyncio
async def test_emit_is_file_tree_only_when_no_report_persisted_c1(tmp_path, monkeypatch):
    """C1 edge case: no report was persisted this turn (short reply, or none at
    all) — the emit must not carry a `None`/missing report entry, just the
    file-tree section."""
    _patch_generated_dir(monkeypatch, tmp_path)
    persisted, sent = _patch_persist_and_send(monkeypatch)
    _patch_db_existing_artifacts(monkeypatch, "security_artifacts", [])

    import agents_orchestrator.security_agent.config.session_state as sec_state
    session = SimpleNamespace(last_artifact={
        "findings": [{"id": "S-001", "title": "f", "severity": "high"}],
    })
    monkeypatch.setattr(sec_state, "get_session", lambda rid: session)

    result = await copilot_api._capture_stage_files(
        "security", "run-8", "t1", "p1", _FakeWebSocket())

    assert result is True
    assert len(sent) == 1
    assert sent[0]["artifacts"] == [{"id": "security-files", "stage": "security",
                                     "kind": "file-tree", "title": "Generated files",
                                     "source": "security"}]


@pytest.mark.asyncio
async def test_persisted_sections_exclude_file_tree_c3(tmp_path, monkeypatch):
    """C3: the persisted `{stage}_artifacts.sections` must never gain a
    `kind=="file-tree"` entry (only `has_files: True`) — `sections_from_run`
    synthesizes the file-tree section exactly once on reload; persisting it here
    too would duplicate the `{stage}-files` id there (React dup-key)."""
    _patch_generated_dir(monkeypatch, tmp_path)
    persisted, sent = _patch_persist_and_send(monkeypatch)

    report_section = {"id": "security-report", "stage": "security", "kind": "markdown",
                       "title": "Security Report", "content": "..."}
    _patch_db_existing_artifacts(monkeypatch, "security_artifacts", [report_section])

    import agents_orchestrator.security_agent.config.session_state as sec_state
    session = SimpleNamespace(last_artifact={
        "sbom": [{"name": "x", "version": "1.0"}],
        "findings": [{"id": "S-001", "title": "f", "severity": "high"}],
    })
    monkeypatch.setattr(sec_state, "get_session", lambda rid: session)

    result = await copilot_api._capture_stage_files(
        "security", "run-9", "t1", "p1", _FakeWebSocket())

    assert result is True
    assert len(persisted) == 1
    _, _, col, payload = persisted[0]
    assert col == "security_artifacts"
    kinds = [sec.get("kind") for sec in payload["sections"]]
    assert "file-tree" not in kinds
    assert payload["sections"] == [report_section]
    assert payload["has_files"] is True
