"""ADO project and repo names are free text; the Testing agent's clone URL must encode them.

LIVE, 17 Sep 2026: every Testing run on "Url Shortner 1" ended "The run did not complete".
The ADO project and repo are both named "QUICKLINK(Url shortner)"; the clone URL carried the
space and parentheses raw, git rejected it ("URL rejected: Malformed input to a URL
function"), and the runner had no code to test.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_the_clone_url_encodes_a_project_and_repo_with_spaces_and_parentheses(tmp_path):
    from agents_orchestrator.testing_agent.tools import ado_clone

    seen = {}

    def fake_run(cmd, **_kw):
        seen["url"] = cmd[-2]
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    with patch.object(ado_clone.subprocess, "run", fake_run):
        ado_clone.clone_branch("https://dev.azure.com/srk02804", "QUICKLINK(Url shortner)",
                               "QUICKLINK(Url shortner)", "feature/x", "p@t/#", str(tmp_path / "w"))
    assert seen["url"] == ("https://anything:p%40t%2F%23@dev.azure.com/srk02804/"
                           "QUICKLINK%28Url%20shortner%29/_git/QUICKLINK%28Url%20shortner%29")


def test_the_remote_added_to_a_reused_clone_is_encoded_the_same_way():
    from agents_orchestrator.testing_agent.Nodes.workspace import _build_ado_remote_url

    url = _build_ado_remote_url("https://dev.azure.com/srk02804", "QUICKLINK(Url shortner)", "QUICKLINK(Url shortner)", "p@t")
    assert url == "https://anything:p%40t@dev.azure.com/srk02804/QUICKLINK%28Url%20shortner%29/_git/QUICKLINK%28Url%20shortner%29"
