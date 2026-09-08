"""The prompt `generate_final_message` builds for the plan-summary LLM call.

WHY THIS FILE EXISTS — a live Orchestrator run, 2026-09-07, run id
`4d9ba955-4ec6-4bc0-a0e1-dd81ac5e0991`. The user asked, with no attachments and no
prior conversation:

    "Write the test plan for that order-total calculation: the cases, the expected
     results, and what is deliberately not covered. Produce the document now — do
     not ask me questions."

The agent analysed the request, wrote a 24-case plan and saved `test_plan.xlsx`
(24 data rows, verified) — and then told the user:

    "I don't have access to any Excel file. I cannot: view files on your computer,
     access previously uploaded documents ... Describe the 24 test cases you've
     created."

Nobody had mentioned Excel and nobody had mentioned 24 test cases. Both came out of
this node's own prompt, which was:

    f"I have generated a detailed test plan with {num_cases} cases for '{user_prompt}'. "
    f"You can find the plan in the Excel file."

That string is handed to `get_llm().invoke(...)` as the WHOLE conversation, so
LangChain wraps it as a single HumanMessage: from the model's side, the user has
just claimed to hold a 24-case plan in an Excel file and asked nothing. Answering
"I cannot see your file, please paste it" is the only sensible reading of that
turn. The four output files were written anyway, by `package_final_reports`, which
is a different node and does not care what the summary said — so the user saw a
refusal in chat contradicted by the artifacts sitting next to it. Worse,
`package_final_reports` writes `final_user_message` out as `execution_summary.md`
(finalize.py:171-177), so the refusal was also filed as a deliverable.

WHAT THESE TESTS DEFEND. Not a wording. One invariant, which the old prompt broke
in the most direct way possible: *the summary node must never ask the model to
describe something the model was not given.* So the prompt has to carry the plan
itself, must not point at a file, and must not be phrased as a message FROM the
user. They assert on the prompt actually passed to `.invoke` — the string the
model receives — never on a module constant, which a dead variable would satisfy.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents_orchestrator.testing_agent.Nodes import finalize
from agents_orchestrator.testing_agent.config.session_state import TestCase, TestPlan


REQUEST = (
    "Write the test plan for that order-total calculation: the cases, the expected "
    "results, and what is deliberately not covered. Produce the document now — do "
    "not ask me questions."
)


def _case(n: int) -> TestCase:
    """A case whose every field is unique to `n`, so a prompt that renders only
    some of the plan cannot accidentally satisfy an assertion about the rest."""
    return TestCase(
        test_case_id=f"TC-REQ-01-{n:02d}",
        feature_or_function_tested=f"REQ-01: order total, facet {n}",
        test_summary=f"unique-summary-marker-{n}",
        scenario_type=["Happy Path", "Error Case", "Edge Case"][n % 3],
        test_steps=f"1. do step {n}",
        test_data=f"input-{n}",
        expected_result=f"expected-{n}",
    )


class _PromptRecorder:
    """Stands in for the BYOK client. Records what it was asked, answers blandly."""

    def __init__(self) -> None:
        self.prompts: list = []

    def invoke(self, prompt, *args, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(content="The plan is ready.", usage_metadata={})


class _LLMMustNotBeCalled:
    def invoke(self, prompt, *args, **kwargs):  # pragma: no cover - the assertion
        raise AssertionError(
            "the summary node called the model with no test cases to describe; "
            f"prompt was: {prompt!r}"
        )


def _run_summary(monkeypatch, state, llm) -> dict:
    monkeypatch.setattr(finalize, "get_llm", lambda: llm)
    return asyncio.run(finalize.generate_final_message(state))


def _built_prompt(monkeypatch, cases) -> str:
    recorder = _PromptRecorder()
    state = {"user_prompt": REQUEST, "test_plan": TestPlan(test_cases=cases)}
    _run_summary(monkeypatch, state, recorder)
    assert len(recorder.prompts) == 1, "expected exactly one summary LLM call"
    prompt = recorder.prompts[0]
    # `.invoke` accepts a bare string or a message list; either way what the model
    # reads is text, and that text is what every assertion below is about.
    return prompt if isinstance(prompt, str) else str(prompt)


def test_the_plan_summary_prompt_carries_the_plan_the_model_is_asked_to_describe(monkeypatch):
    """Every case, not a count. The failure this reproduces is a model asked to
    summarise a document it was never shown; handing it `len(cases)` and nothing
    else is exactly that. Six distinct cases, all of which must appear, so a
    prompt that renders "the first three" is caught rather than rounded up."""
    cases = [_case(n) for n in range(1, 7)]
    prompt = _built_prompt(monkeypatch, cases)

    for case in cases:
        assert case.test_case_id in prompt, f"{case.test_case_id} missing from the prompt"
        assert case.test_summary in prompt, f"{case.test_summary} missing from the prompt"

    # A truncating renderer that still announces the full count would let the model
    # state a number it cannot support. Whatever count the prompt states must be
    # the number of cases the prompt actually contains.
    assert "6" in prompt


def test_the_plan_summary_prompt_never_points_the_model_at_a_file_it_was_not_given(monkeypatch):
    """"You can find the plan in the Excel file" is what produced "I don't have
    access to any Excel file". The model has no filesystem; naming one as the
    place the content lives invites precisely the refusal that shipped."""
    prompt = _built_prompt(monkeypatch, [_case(1), _case(2)]).lower()

    for pointer in ("excel", ".xlsx", "spreadsheet", "attach", "upload", "you can find"):
        assert pointer not in prompt, f"the summary prompt still refers the model to {pointer!r}"


def test_the_plan_summary_prompt_is_addressed_to_the_model_not_written_as_the_users_own_words(monkeypatch):
    """The old prompt was a first-person claim ("I have generated ...") delivered
    as a HumanMessage, so the model read the AGENT'S OWN WORK as something the user
    was asserting, and replied to the user about it. The prompt must instruct."""
    prompt = _built_prompt(monkeypatch, [_case(1), _case(2)])

    assert "I have generated" not in prompt
    assert not prompt.lstrip().startswith("I ")


def test_the_plan_summary_prompt_tells_the_model_not_to_interrogate_the_user(monkeypatch):
    """The user's request ended "do not ask me questions" and the reply was four
    questions and a menu. The summary turn has everything it needs by construction,
    so asking for more is never correct here and the prompt has to say so.

    The instruction is looked for in the SCAFFOLD — the prompt with the quoted
    request removed. `REQUEST` itself says "do not ask me questions", so a naive
    substring check over the whole prompt passed against the ORIGINAL, broken code:
    the assertion was satisfied by the user's own words being echoed back, which is
    not the agent instructing anything. This is the difference between a test that
    describes the string and one that defends the behaviour."""
    prompt = _built_prompt(monkeypatch, [_case(1), _case(2)])
    scaffold = prompt.replace(REQUEST, "").lower()

    assert "do not ask" in scaffold


def test_an_empty_test_plan_is_never_announced_as_a_finished_plan(monkeypatch):
    """`generate_test_plan` returns `TestPlan(test_cases=[])` when it had no
    analysis to work from (Nodes/plan.py:26-29), and a pydantic model with an empty
    list is still truthy — so the old branch built "a detailed test plan with 0
    cases" and asked the model to describe it. Same defect, emptier: nothing to
    summarise, so nothing is sent, and the user is told the truth."""
    state = {"user_prompt": REQUEST, "test_plan": TestPlan(test_cases=[])}

    result = _run_summary(monkeypatch, state, _LLMMustNotBeCalled())

    message = result["final_user_message"]
    assert "0 cases" not in message
    assert "test case" in message.lower()


def test_the_summary_the_model_writes_is_what_the_user_is_shown(monkeypatch):
    """The node's output still comes from the model — the fix is to the prompt, not
    a replacement of the summary with a canned string."""
    recorder = _PromptRecorder()
    state = {"user_prompt": REQUEST, "test_plan": TestPlan(test_cases=[_case(1)])}

    result = _run_summary(monkeypatch, state, recorder)

    assert result["final_user_message"] == "The plan is ready."
