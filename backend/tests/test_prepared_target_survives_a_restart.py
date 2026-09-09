"""The prepared target survives the process that prepared it.

THE BUG, as reported: "the deployment chat says the workspace is not prepared, right
after preparing it". The clone was there. The branch was right. The agent's own
`inspect_repo` said "no workspace prepared" and the page said "No deployment yet".

WHAT WAS HAPPENING. The record of what was prepared lived in a module-level dict, and
the clone itself restarted the server: `uvicorn --reload` watches the backend tree, the
workspace lives inside it, and cloning a repository full of `.py` files reloads the
process that just cloned it. The dict came back empty; the checkout stayed on disk. In
production the same record is invisible to every other worker, so which pod answers the
chat decides whether the agent can see the repo at all.

The fix writes the record beside the checkout — WITHOUT the credential. That exclusion
is not incidental: the product's rule is one token, per person, per project, held in the
credential store. A copy in a JSON file would be a second one, and it would outlive the
revocation of the first.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.services import prepared_targets  # noqa: E402

pytestmark = pytest.mark.unit

TENANT = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A workspace root of our own, so the test never touches a real checkout."""
    monkeypatch.setattr(prepared_targets, "_ROOT", tmp_path)
    work_dir = tmp_path / TENANT / PROJECT / "deployment"
    work_dir.mkdir(parents=True)
    return work_dir


def _record(work_dir: pathlib.Path) -> dict:
    return {
        "work_dir": str(work_dir),
        "repo_url": "https://x-token:ghp_SECRET@github.com/acme/web.git",
        "pat": "ghp_SECRET",
        "provider": "github",
        "repo_name": "web",
        "source_branch": "main",
        "environment": "staging",
        "deploy_via": "github_actions",
    }


def test_the_record_comes_back_after_the_process_forgets(workspace):
    """THE HEADLINE. Nothing else in this file matters if this does not hold."""
    prepared_targets.save("deployment", TENANT, PROJECT, _record(workspace), owner_id="u-1")

    restored = prepared_targets.load("deployment", TENANT, PROJECT)

    assert restored is not None
    assert restored["work_dir"] == str(workspace)
    assert restored["source_branch"] == "main"
    assert restored["deploy_via"] == "github_actions"
    assert restored["owner_id"] == "u-1"


def test_the_credential_is_never_written_down(workspace):
    """The token, and the URL that carries it. Asserted against the FILE, not the
    returned dict — a redaction that only applies on the way out would still have put
    the secret on disk."""
    prepared_targets.save("deployment", TENANT, PROJECT, _record(workspace), owner_id="u-1")

    raw = (workspace.parent / "deployment.prepared.json").read_text(encoding="utf-8")

    assert "ghp_SECRET" not in raw
    assert "pat" not in json.loads(raw)
    assert "repo_url" not in json.loads(raw)


def test_a_record_whose_checkout_is_gone_is_not_offered(workspace):
    """Worse than no record: the page would say a target is prepared and every read
    the agent attempted would fail on a directory that is not there."""
    import shutil

    prepared_targets.save("deployment", TENANT, PROJECT, _record(workspace), owner_id="u-1")
    shutil.rmtree(workspace)

    assert prepared_targets.load("deployment", TENANT, PROJECT) is None


def test_agents_do_not_read_each_others_targets(workspace):
    """One project, several agents, one directory. A Security scan binding the
    Deployment agent's checkout would review the wrong thing quietly."""
    prepared_targets.save("deployment", TENANT, PROJECT, _record(workspace), owner_id="u-1")

    assert prepared_targets.load("security", TENANT, PROJECT) is None
    assert prepared_targets.load("deployment", TENANT, "33333333-3333-3333-3333-333333333333") is None


def test_nothing_prepared_reads_as_nothing_prepared(workspace):
    assert prepared_targets.load("deployment", TENANT, PROJECT) is None


def test_a_half_written_file_is_not_a_target(workspace, monkeypatch):
    """The write is whole-then-move for this reason; a reader arriving mid-write must
    not decide a target is absent OR read a truncated one as present."""
    path = workspace.parent / "deployment.prepared.json"
    path.write_text('{"work_dir": "', encoding="utf-8")

    assert prepared_targets.load("deployment", TENANT, PROJECT) is None
    assert not list(workspace.parent.glob("*.tmp")), "no temp file is left behind"


def test_saving_never_fails_the_prepare(tmp_path, monkeypatch):
    """A target that cannot be written down still works in this process. Failing the
    prepare over it would trade a recoverable problem for an immediate one."""
    monkeypatch.setattr(prepared_targets, "_ROOT", tmp_path)

    def _explode(*_a, **_k):
        raise OSError("disk is full")

    monkeypatch.setattr(pathlib.Path, "mkdir", _explode)

    prepared_targets.save("deployment", TENANT, PROJECT, {"work_dir": "x"}, owner_id="u-1")


@pytest.mark.asyncio
async def test_no_owner_borrows_no_token():
    """A record prepared with nobody named must not fall back to whatever credential
    exists — that is the borrowed-token failure the per-person rule exists to stop."""
    got = await prepared_targets.resolve_secret(
        tenant_id=TENANT, project_id=PROJECT, owner_id="", provider="github",
    )

    assert got == ""


@pytest.mark.asyncio
async def test_an_unresolvable_credential_is_empty_not_an_exception(monkeypatch):
    """Reading a checked-out repo needs no token. Raising here would stop the agent
    inspecting code it can already see."""
    from shared.services import repo_source

    async def _no(*_a, **_k):
        raise RuntimeError("Azure DevOps is not configured for this project.")

    monkeypatch.setattr(repo_source, "resolve", _no)

    got = await prepared_targets.resolve_secret(
        tenant_id=TENANT, project_id=PROJECT, owner_id="u-1",
    )

    assert got == ""


@pytest.mark.asyncio
async def test_the_token_is_resolved_as_the_person_who_prepared_it(monkeypatch):
    """NON-VACUITY for the two above, and the property that matters: the owner recorded
    at prepare time is who the external system will see."""
    from shared.services import repo_source

    seen: dict = {}

    async def _resolve(tenant_id, *, project_id="", owner_id="", provider=None):
        seen.update(tenant=tenant_id, project=project_id, owner=owner_id, provider=provider)
        return ("github", "https://github.com", "ghp_FRESH")

    monkeypatch.setattr(repo_source, "resolve", _resolve)

    got = await prepared_targets.resolve_secret(
        tenant_id=TENANT, project_id=PROJECT, owner_id="u-1", provider="github",
    )

    assert got == "ghp_FRESH"
    assert seen == {"tenant": TENANT, "project": PROJECT, "owner": "u-1", "provider": "github"}


# -- through the agents' own stores, which is where the bug was visible ---------

#: Every agent that prepares a target and then binds it in chat. Each had its own copy
#: of the in-memory dict, so each had its own copy of the bug.
AGENT_STORES = [
    ("deployment", "agents_orchestrator.deployment_agent.config.session_state"),
    ("code_review", "agents_orchestrator.code_review_agent.config.session_state"),
    ("security", "agents_orchestrator.security_agent.config.session_state"),
    ("documentation", "agents_orchestrator.documentation_agent.config.session_state"),
]


@pytest.mark.parametrize("agent,module_name", AGENT_STORES)
def test_the_agent_still_finds_its_target_after_a_restart(agent, module_name, workspace):
    """WHAT THE USER SAW, reproduced through the code path they hit: prepare, lose the
    process, ask the agent. Before this, `get_prepared` returned None and the agent
    replied that no workspace was prepared — about a checkout that was right there."""
    import importlib

    store = importlib.import_module(module_name)
    store.set_prepared(TENANT, PROJECT, _record(workspace))
    prepared_targets.save(agent, TENANT, PROJECT, _record(workspace), owner_id="u-1")

    # The restart: the process keeps its files and forgets everything else.
    store._prepared.clear()

    found = store.get_prepared(TENANT, PROJECT)

    assert found is not None, "the agent cannot see the repository it just cloned"
    assert found["work_dir"] == str(workspace)
    assert not found.get("pat"), "a restored record must carry no credential"


@pytest.mark.parametrize("agent,module_name", AGENT_STORES)
def test_a_restored_target_is_cached_not_re_read_every_turn(agent, module_name, workspace):
    """Every message on the session goes through this. Reading the file each time would
    put a disk hit on the hot path of every turn."""
    import importlib

    store = importlib.import_module(module_name)
    store._prepared.clear()
    prepared_targets.save(agent, TENANT, PROJECT, _record(workspace), owner_id="u-1")

    first = store.get_prepared(TENANT, PROJECT)
    assert first is not None

    # Remove the file entirely: a second read that still answers came from memory.
    prepared_targets.forget(agent, TENANT, PROJECT)
    assert store.get_prepared(TENANT, PROJECT) is not None

    store._prepared.clear()
