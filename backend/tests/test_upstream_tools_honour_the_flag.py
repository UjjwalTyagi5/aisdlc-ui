"""The four rewritten upstream-read tools, called for real, against a real database.

WHY THIS FILE EXISTS SEPARATELY. `test_artifact_consumption.py` proves the shared
helper. It does not prove the four TOOLS were wired to it correctly, and the existing
agent suites do not cover these tools at all beyond the no-tenant early return — so
"behaviour is unchanged with the flag off" was, until this file, an untested claim
about the highest-blast-radius part of the change.

WHAT EACH TOOL HAS TO DO:

  flag off  exactly what it did before — read the latest working payload, approved or
            not. Every existing project is here, so a regression breaks the estate.
  flag on   the published version only, and when there is none, SAY SO rather than
            returning null. A bare null reads as "the stage produced nothing", and an
            agent told that will proceed as though there were nothing to wait for.
"""
from __future__ import annotations

import json
import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.ws_helper import set_session_id  # noqa: E402
from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.artifact_versions import (  # noqa: E402
    publish_version, snapshot_stage_payload,
)

pytestmark = pytest.mark.usefixtures("purge_created_orgs")

PRODUCER = "producer@example.com"
OWNER = "owner@example.com"

DRAFT_DESIGN = {"c4": "the unapproved working draft"}
DRAFT_TESTING = {"suites": "unapproved"}
PUBLISHED_DESIGN = {"c4": "the approved one"}


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def seeded():
    """A project with a run carrying working payloads, and nothing published."""
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    run = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'Upstream Tool Test')"
        ), {"i": org, "s": f"upst-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Upstream Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.execute(text(
            "INSERT INTO runs (id, tenant_id, project_id, stage, design_artifacts, "
            "  testing_artifacts, security_artifacts, requirements_payload) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:p AS uuid), 'design', "
            "  CAST(:d AS jsonb), CAST(:te AS jsonb), CAST(:se AS jsonb), CAST(:r AS jsonb))"
        ), {"i": run, "t": org, "p": proj,
            "d": json.dumps(DRAFT_DESIGN), "te": json.dumps(DRAFT_TESTING),
            "se": json.dumps({"findings": "unapproved"}),
            "r": json.dumps({"stories": "unapproved"})})
        await s.commit()
    yield {"org": org, "project": proj, "run": run}


async def _enforce(t, on: bool):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :x, true)"), {"x": t["org"]})
        await s.execute(text(
            "UPDATE projects SET enforce_artifact_publication = :v "
            "WHERE id = CAST(:p AS uuid)"
        ), {"v": on, "p": t["project"]})
        await s.commit()


async def _publish(t, stage: str, payload: dict):
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :x, true)"), {"x": t["org"]})
        ref = await snapshot_stage_payload(
            s, tenant_id=t["org"], project_id=t["project"], stage=stage,
            payload=payload, produced_by=PRODUCER)
        await publish_version(
            s, tenant_id=t["org"], project_id=t["project"], stage=stage,
            version=ref.version, published_by=OWNER)
        await s.commit()


def _bind(module_session_getter, clear, sid: str, t):
    clear(sid)
    set_session_id(sid)
    s = module_session_getter(sid)
    s.tenant_id = t["org"]
    s.project_id = t["project"]
    return s


# -- documentation ------------------------------------------------------------


async def test_documentation_reads_the_draft_when_the_flag_is_off(seeded):
    from agents_orchestrator.documentation_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.documentation_agent.tools import doc_tools

    _bind(get_session, clear_session, "doc-flag-off", seeded)
    out = json.loads(await doc_tools.read_upstream_artifacts.ainvoke({}))
    assert out["design"] == DRAFT_DESIGN
    assert out["testing"] == DRAFT_TESTING
    clear_session("doc-flag-off")


async def test_documentation_refuses_the_draft_when_the_flag_is_on(seeded):
    """AND SAYS WHY. Documentation compiles what it is handed; a bare null would put
    out a document with a section silently missing that reads as complete."""
    from agents_orchestrator.documentation_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.documentation_agent.tools import doc_tools

    await _enforce(seeded, True)
    _bind(get_session, clear_session, "doc-flag-on", seeded)
    out = json.loads(await doc_tools.read_upstream_artifacts.ainvoke({}))

    assert out["design"] is None
    assert "not an error" in out["design_status"]
    assert "the unapproved working draft" not in json.dumps(out)
    clear_session("doc-flag-on")


async def test_documentation_reads_the_published_version_when_there_is_one(seeded):
    from agents_orchestrator.documentation_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.documentation_agent.tools import doc_tools

    await _enforce(seeded, True)
    await _publish(seeded, "design", PUBLISHED_DESIGN)
    _bind(get_session, clear_session, "doc-published", seeded)
    out = json.loads(await doc_tools.read_upstream_artifacts.ainvoke({}))

    assert out["design"] == PUBLISHED_DESIGN
    assert "design_status" not in out
    clear_session("doc-published")


# -- security -----------------------------------------------------------------


async def test_security_reads_the_draft_when_the_flag_is_off(seeded):
    from agents_orchestrator.security_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.security_agent.tools import security_tools

    _bind(get_session, clear_session, "sec-flag-off", seeded)
    out = await security_tools.read_design_artifacts.ainvoke({})
    assert "the unapproved working draft" in out
    clear_session("sec-flag-off")


async def test_security_refuses_the_draft_when_the_flag_is_on(seeded):
    from agents_orchestrator.security_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.security_agent.tools import security_tools

    await _enforce(seeded, True)
    _bind(get_session, clear_session, "sec-flag-on", seeded)
    out = await security_tools.read_design_artifacts.ainvoke({})
    assert "the unapproved working draft" not in out
    assert "No approved design" in out
    clear_session("sec-flag-on")


async def test_security_reads_the_published_version(seeded):
    from agents_orchestrator.security_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.security_agent.tools import security_tools

    await _enforce(seeded, True)
    await _publish(seeded, "design", PUBLISHED_DESIGN)
    _bind(get_session, clear_session, "sec-published", seeded)
    out = await security_tools.read_design_artifacts.ainvoke({})
    assert "the approved one" in out
    clear_session("sec-published")


# -- code review --------------------------------------------------------------


async def test_code_review_reads_the_draft_when_the_flag_is_off(seeded):
    from agents_orchestrator.code_review_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.code_review_agent.tools import review_tools

    _bind(get_session, clear_session, "cr-flag-off", seeded)
    out = await review_tools.read_design_artifacts.ainvoke({})
    assert "the unapproved working draft" in out
    clear_session("cr-flag-off")


async def test_code_review_refuses_the_draft_when_the_flag_is_on(seeded):
    """Reviewing against an UNAPPROVED design is worse than reviewing without one:
    findings get raised against a contract nobody has agreed to."""
    from agents_orchestrator.code_review_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.code_review_agent.tools import review_tools

    await _enforce(seeded, True)
    _bind(get_session, clear_session, "cr-flag-on", seeded)
    out = await review_tools.read_design_artifacts.ainvoke({})
    assert "the unapproved working draft" not in out
    assert "No approved design" in out
    clear_session("cr-flag-on")


# -- deployment ---------------------------------------------------------------


async def test_deployment_reads_the_draft_when_the_flag_is_off(seeded):
    from agents_orchestrator.deployment_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.deployment_agent.tools import deploy_tools

    _bind(get_session, clear_session, "dep-flag-off", seeded)
    out = json.loads(await deploy_tools.read_upstream_artifacts.ainvoke({}))
    assert out["testing"] == DRAFT_TESTING
    clear_session("dep-flag-off")


async def test_deployment_refuses_unapproved_gate_evidence(seeded):
    """THE ONE WITH TEETH. Deploying on unapproved test and security evidence is the
    exact failure the gate exists to prevent."""
    from agents_orchestrator.deployment_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.deployment_agent.tools import deploy_tools

    await _enforce(seeded, True)
    _bind(get_session, clear_session, "dep-flag-on", seeded)
    out = json.loads(await deploy_tools.read_upstream_artifacts.ainvoke({}))

    assert out["testing"] is None and out["security"] is None
    assert "not an error" in out["testing_status"]
    assert "unapproved" not in json.dumps(out)
    clear_session("dep-flag-on")


async def test_deployment_reads_published_gate_evidence(seeded):
    from agents_orchestrator.deployment_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.deployment_agent.tools import deploy_tools

    await _enforce(seeded, True)
    await _publish(seeded, "testing", {"suites": "approved"})
    _bind(get_session, clear_session, "dep-published", seeded)
    out = json.loads(await deploy_tools.read_upstream_artifacts.ainvoke({}))

    assert out["testing"] == {"suites": "approved"}
    # security is still unpublished, so it must still refuse — a partial gate is not
    # a passed gate.
    assert out["security"] is None and "security_status" in out
    clear_session("dep-published")


# -- the evidence trail, end to end -------------------------------------------


async def test_a_tool_read_is_recorded_as_a_consumption(seeded):
    """Proves the wiring writes evidence, not just that the helper can."""
    from agents_orchestrator.security_agent.config.session_state import (
        clear_session, get_session,
    )
    from agents_orchestrator.security_agent.tools import security_tools

    await _enforce(seeded, True)
    await _publish(seeded, "design", PUBLISHED_DESIGN)
    _bind(get_session, clear_session, "sec-evidence", seeded)
    await security_tools.read_design_artifacts.ainvoke({})

    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :x, true)"),
            {"x": seeded["org"]})
        rows = (await s.execute(text(
            "SELECT producing_stage, consumer_stage, version FROM artifact_consumptions "
            "WHERE project_id = CAST(:p AS uuid)"
        ), {"p": seeded["project"]})).fetchall()
    assert [tuple(r) for r in rows] == [("design", "security", 1)]
    clear_session("sec-evidence")
