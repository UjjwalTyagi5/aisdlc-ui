"""Dependency and Risk's tools: where it may clone from, that the clone cannot push,
and that an assessment becomes a report and an artifact."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agents_orchestrator.discovery_agent.tools import assessment_tools, repo_tools
from agents_orchestrator.discovery_agent.tools.repo_tools import validate_clone_url
from config.ws_helper import set_session_id, set_user_id
from tests.discovery.legacy_fixture import build_legacy_repo


@pytest.fixture(autouse=True)
def _session(tmp_path, monkeypatch):
    set_session_id(f"disc-{tmp_path.name}")
    set_user_id("tester")
    monkeypatch.setattr(repo_tools, "legacy_checkout_dir", lambda: str(tmp_path / "checkout"))
    yield
    repo_tools._SESSIONS.clear()


@pytest.mark.parametrize("url", [
    "http://github.com/org/repo.git",
    "file:///etc/passwd",
    "https://internal.corp.example/repo.git",
    "https://169.254.169.254/latest",
    "git@github.com:org/repo.git",
])
def test_clone_refuses_urls_off_the_allow_list(url):
    assert validate_clone_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com/dotnet-architecture/eShopModernizing.git",
    "https://dev.azure.com/org/proj/_git/repo",
    "https://org.visualstudio.com/proj/_git/repo",
])
def test_clone_accepts_known_hosts(url):
    assert validate_clone_url(url) is None


async def test_clone_by_unknown_name_asks_for_a_listing_first():
    out = await repo_tools.clone_legacy_repository.ainvoke({"repository": "billing"})
    assert "list_legacy_repositories" in out


def test_the_checkout_cannot_push(tmp_path):
    """Read-only by construction: whatever runs `git push` on this checkout fails."""
    src = tmp_path / "src"
    build_legacy_repo(src)
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(["git", *cmd], cwd=src, check=True, capture_output=True)
    facts = repo_tools._clone(str(src), str(tmp_path / "dest"), "", "")
    assert len(facts["commit"]) == 40
    push = subprocess.run(["git", "remote", "get-url", "--push", "origin"],
                          cwd=tmp_path / "dest", capture_output=True, text=True).stdout.strip()
    assert push == repo_tools._DISABLED_PUSH_URL
    assert (tmp_path / "dest" / "src" / "Billing.Web" / "Billing.Web.csproj").exists()


@pytest.fixture
def cloned(tmp_path, monkeypatch):
    """clone_legacy_repository with the network step replaced by a copy of the fixture."""
    fixture = build_legacy_repo(tmp_path / "fixture")

    def fake_clone(url, dest, branch, secret):
        shutil.copytree(fixture, dest)
        return {"commit": "a" * 40, "branch": branch or "main"}

    monkeypatch.setattr(repo_tools, "_clone", fake_clone)
    return fixture


async def test_assess_after_clone_returns_the_report_and_keeps_the_assessment(cloned, monkeypatch):
    monkeypatch.setattr(assessment_tools, "_scan_vulnerabilities",
                        lambda wd: ([], {"trivy": "unavailable", "note": "not installed"}))
    persisted: list[dict] = []

    async def fake_persist(artifacts):
        persisted.append(artifacts)
        return "Saved to the project as the current assessment."

    monkeypatch.setattr(assessment_tools, "_persist", fake_persist)

    out = json.loads(await repo_tools.clone_legacy_repository.ainvoke(
        {"repository": "https://github.com/acme/billing.git"}))
    assert out["read_only"] is True and out["branch"] == "main"

    report = await assessment_tools.assess_legacy_repository.ainvoke({"target_stack": ".NET 8"})
    assert report.startswith("# Dependency and Risk — billing")
    assert "## Module risk and migration tier" in report
    assert "Billing.Web" in report
    assert persisted and persisted[0]["target_stack"] == ".NET 8"
    assert persisted[0]["repository"]["url"] == "https://github.com/acme/billing.git"
    assert repo_tools.session().assessment is persisted[0]


async def test_assess_without_a_checkout_says_what_to_do():
    out = await assessment_tools.assess_legacy_repository.ainvoke({})
    assert "clone_legacy_repository" in out


async def test_module_detail_names_the_known_modules_on_a_miss(cloned, monkeypatch):
    monkeypatch.setattr(assessment_tools, "_scan_vulnerabilities", lambda wd: ([], {"trivy": "skipped"}))

    async def no_persist(artifacts):
        return "Not saved."

    monkeypatch.setattr(assessment_tools, "_persist", no_persist)
    await repo_tools.clone_legacy_repository.ainvoke({"repository": "https://github.com/acme/billing.git"})
    await assessment_tools.assess_legacy_repository.ainvoke({})
    miss = await assessment_tools.get_module_detail.ainvoke({"module": "Nope"})
    assert "Billing.Web" in miss
    hit = json.loads(await assessment_tools.get_module_detail.ainvoke({"module": "billing.web"}))
    assert hit["risk"]["tier"] == "manual"


async def test_export_writes_a_document(cloned, monkeypatch, tmp_path):
    from agents_orchestrator.modernization_common import files

    monkeypatch.setattr(assessment_tools, "_scan_vulnerabilities", lambda wd: ([], {"trivy": "skipped"}))

    async def no_persist(artifacts):
        return "Not saved."

    announced: list[tuple] = []

    async def fake_announce(segment, filename, path, *, stage):
        announced.append((segment, filename, stage))
        return f"http://x/generated/{filename}"

    monkeypatch.setattr(assessment_tools, "_persist", no_persist)
    monkeypatch.setattr(files, "output_dir", lambda segment: str(tmp_path / "out"))
    monkeypatch.setattr(files, "announce_generated_file", fake_announce)
    (tmp_path / "out").mkdir()
    await repo_tools.clone_legacy_repository.ainvoke({"repository": "https://github.com/acme/billing.git"})
    await assessment_tools.assess_legacy_repository.ainvoke({})
    out = await assessment_tools.export_assessment_report.ainvoke({"filename": "assessment.docx"})
    assert (tmp_path / "out" / "assessment.docx").stat().st_size > 1000
    assert announced == [("discovery_agent", "assessment.docx", "discovery")]
    assert "assessment.docx" in out


async def test_a_connector_the_stage_cannot_read_is_refused(monkeypatch):
    class _Unreadable:
        access_level = None
        display_name = "Azure DevOps"
        connector_name = "azure_devops"

        async def auth_adapter(self, tenant_id=""):
            raise AssertionError("credentials must not be read for a stage without access")

    monkeypatch.setattr("config.connectors.context.get_connector", lambda: _Unreadable())
    out = await repo_tools.list_legacy_repositories.ainvoke({})
    assert "not readable by the Dependency and Risk stage" in out


async def test_no_connector_explains_how_to_wire_one(monkeypatch):
    def none():
        raise RuntimeError("no connector")

    monkeypatch.setattr("config.connectors.context.get_connector", none)
    out = await repo_tools.list_legacy_repositories.ainvoke({})
    assert "project settings" in out and "public https clone URL" in out


def test_prompt_says_read_only_and_planning_baseline():
    from agents_orchestrator.discovery_agent.prompts.discovery_prompt import DISCOVERY_SYS_MESSAGE

    assert "READ-ONLY" in DISCOVERY_SYS_MESSAGE
    assert "planning baseline" in DISCOVERY_SYS_MESSAGE
    assert "never invent" in DISCOVERY_SYS_MESSAGE.lower()


def test_graph_compiles_with_every_tool():
    from agents_orchestrator.discovery_agent.agents.assessor import TOOLS, app

    assert app is not None
    assert {t.name for t in TOOLS} == {
        "list_legacy_repositories", "clone_legacy_repository", "assess_legacy_repository",
        "get_module_detail", "get_dependency_graph", "export_assessment_report",
        "get_legacy_code_profile", "list_legacy_files", "read_legacy_file", "search_legacy_code",
    }


async def test_a_public_clone_goes_ahead_when_the_stage_connection_cannot_authenticate(monkeypatch, tmp_path):
    """A misconfigured GitHub connection withholds its credential, not the clone: a
    public repository needs no credential."""
    class _Broken:
        access_level = "read_write"
        display_name = "GitHub"
        connector_name = "github"

        async def auth_adapter(self, tenant_id=""):
            raise RuntimeError("no GitHub credential saved")

    used: list[str] = []

    def fake_clone(url, dest, branch, secret):
        used.append(secret)
        (tmp_path / "checkout").mkdir(exist_ok=True)
        return {"commit": "b" * 40, "branch": "main"}

    monkeypatch.setattr("config.connectors.context.get_connector", lambda: _Broken())
    monkeypatch.setattr(repo_tools, "_clone", fake_clone)
    out = json.loads(await repo_tools.clone_legacy_repository.ainvoke(
        {"repository": "https://github.com/dotnet-architecture/eShopModernizing"}))
    assert out["cloned"] is True
    assert used == [""]


async def test_the_matching_connections_credential_is_used(monkeypatch, tmp_path):
    class _Ado:
        access_level = "read"
        display_name = "Azure DevOps"
        connector_name = "azure_devops"

        async def auth_adapter(self, tenant_id=""):
            return {"org_url": "https://dev.azure.com/acme", "pat": "s3cret"}

    used: list[str] = []

    def fake_clone(url, dest, branch, secret):
        used.append(secret)
        return {"commit": "c" * 40, "branch": "main"}

    monkeypatch.setattr("config.connectors.context.get_connector", lambda: _Ado())
    monkeypatch.setattr(repo_tools, "_clone", fake_clone)
    await repo_tools.clone_legacy_repository.ainvoke({"repository": "https://dev.azure.com/acme/billing/_git/billing"})
    await repo_tools.clone_legacy_repository.ainvoke({"repository": "https://github.com/acme/public.git"})
    assert used == ["s3cret", ""]  # the ADO PAT never goes to github.com
