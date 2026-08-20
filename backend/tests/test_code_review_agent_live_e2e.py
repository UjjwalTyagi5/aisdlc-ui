"""Proves the Code Review agent's actual tool loop end to end, without a live LLM key.

No provider is configured (dev environment) and the .env fallback ANTHROPIC_API_KEY is
dead (401), so a real model call is not possible right now. What CAN be proven without
one: every piece of the agent except the model's own judgment is real, wired together
correctly, and actually executes -- the LangGraph loop routes tool_calls to the real
tool implementations (including a genuine Semgrep subprocess run against a fixture file
with two real, known vulnerabilities), and a final structured review is built and
persisted exactly the way a real conversation would produce one. Only the model's
`ainvoke` call is scripted (three canned turns); everything downstream of it -- tool
dispatch, Semgrep execution, submit_code_review's parsing/persistence -- is the genuine
code path.

Fixture: a real shell=True subprocess call and a real md5-as-password-hash pattern,
written into a fresh OS temp directory at test time (not committed to the repo) --
Semgrep's own default ignore patterns exclude any path containing "test"/"fixtures" in
its name, which a committed tests/fixtures_.../vulnerable.py would silently hit
(discovered live: the very first standalone run of this tool against an *uncommitted*
scratch file outside the repo found both findings; committing an equivalent file under
backend/tests/ made semgrep report zero, silently, because of that default-ignore rule
-- not a bug in the tool, but exactly the kind of gotcha this pass exists to catch and
route around, hence generating the fixture into an unrelated temp dir here).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.asyncio

_VULNERABLE_SOURCE = '''\
import subprocess
import hashlib

def run_cmd(user_input):
    subprocess.run(user_input, shell=True)

def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()
'''


@pytest.fixture
def vulnerable_target_dir():
    d = tempfile.mkdtemp(prefix="cr_agent_live_e2e_")
    (Path(d) / "vulnerable.py").write_text(_VULNERABLE_SOURCE, encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


class _ScriptedModel:
    """Stands in for the real ChatAnthropic model. Each .ainvoke() call pops the next
    canned response off the script, so the test controls exactly what the "model"
    decides to do -- but the graph, the tool node, and the tools themselves are 100%
    real code, not mocks."""

    def __init__(self, script: list[AIMessage]):
        self._script = list(script)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return self._script.pop(0)


@pytest.mark.skipif(
    not shutil.which("semgrep"), reason="semgrep CLI not installed"
)
async def test_the_real_tool_loop_runs_semgrep_and_produces_a_persisted_review(vulnerable_target_dir):
    from agents_orchestrator.code_review_agent.agents import reviewer

    script = [
        # Turn 1: the "model" decides to actually scan the prepared target with
        # semgrep -- a real subprocess call, not a mock, against a real vulnerable file.
        AIMessage(
            content="",
            tool_calls=[{
                "name": "run_semgrep_scan",
                "args": {"target_path": vulnerable_target_dir},
                "id": "call_1",
            }],
        ),
        # Turn 2: having seen real findings come back, the "model" submits the
        # structured review -- this exercises submit_code_review's real JSON parsing,
        # Pydantic model construction, and artifact persistence.
        AIMessage(
            content="",
            tool_calls=[{
                "name": "submit_code_review",
                "args": {"review_json": json.dumps({
                    "summary": "Found a shell injection risk and a weak password hash.",
                    "merge_recommendation": "request_changes",
                    "findings": [
                        {
                            "id": "F1", "severity": "critical", "category": "security",
                            "file": "vulnerable.py", "line": 5,
                            "description": "subprocess.run with shell=True",
                            "recommendation": "Use shell=False with an argument list.",
                        },
                        {
                            "id": "F2", "severity": "medium", "category": "security",
                            "file": "vulnerable.py", "line": 8,
                            "description": "MD5 used for password hashing",
                            "recommendation": "Use hashlib.scrypt instead.",
                        },
                    ],
                    "requirements_coverage": [],
                    "design_conformance": [],
                    "metrics": {},
                })},
                "id": "call_2",
            }],
        ),
        # Turn 3: no more tool calls -- the graph's route_fn sends this straight to END.
        AIMessage(content="Review complete: 2 findings, recommend requesting changes."),
    ]
    model = _ScriptedModel(script)

    with patch.object(reviewer, "_resolve_model", return_value=model):
        result = await reviewer.app.ainvoke(
            {
                "messages": [HumanMessage(content="Please review the prepared change.")],
                "tenant_id": "test-tenant",
                "model_id": None,
                "offering_id": None,
            },
            config={"configurable": {"thread_id": "live-e2e-test"}},
        )

    # The model was actually driven through all 3 scripted turns (proves the
    # agent -> tools -> agent -> END loop actually executed, not short-circuited).
    assert model.calls == 3

    # Find the real ToolMessage the tool node produced for the semgrep call and
    # confirm it contains the ACTUAL findings semgrep found in the fixture file --
    # not anything the test scripted. This is the load-bearing assertion: it proves
    # a real `semgrep` subprocess ran against a real file with real vulnerabilities.
    tool_messages = [m for m in result["messages"] if getattr(m, "name", None) == "run_semgrep_scan"]
    assert len(tool_messages) == 1
    semgrep_output = json.loads(tool_messages[0].content)
    assert semgrep_output["status"] == "ok"
    assert semgrep_output["findings_count"] >= 2
    rule_ids = {f["rule_id"] for f in semgrep_output["findings"]}
    assert any("shell-true" in r for r in rule_ids)
    assert any("md5" in r for r in rule_ids)

    # And the submit_code_review tool call actually ran too, producing a real
    # ToolMessage confirmation (not just an unexecuted tool_call on the AIMessage).
    submit_messages = [m for m in result["messages"] if getattr(m, "name", None) == "submit_code_review"]
    assert len(submit_messages) == 1
    assert "Review submitted" in submit_messages[0].content
    assert "request_changes" in submit_messages[0].content
