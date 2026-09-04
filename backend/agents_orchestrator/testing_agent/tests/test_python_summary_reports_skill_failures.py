"""A skill that crashes before writing a test file must say so in the report.

The .NET/React path (Nodes/execute.py) passes skill_failures into build_qa_summary;
the Python sub-agent path built its own summary and did not. So an Azure 429 during
functional/API generation was shown to the user as "No tests collected" — an empty
suite where the real answer (the provider rate-limited us) was sitting in state the
whole time.
"""
from agents_orchestrator.testing_agent.tools.code_testing_agent import summarize_node


def test_python_summary_names_the_skill_failure():
    state = {
        "project_path": ".",
        "input_filename": "your code",
        "skill_failures": [
            "functional_api: RateLimitError: litellm.RateLimitError: rate limit exceeded"
        ],
    }

    summary = summarize_node(state)["summary"]

    assert "Skill generation failures" in summary
    assert "functional_api" in summary
    assert "RateLimitError" in summary


def test_python_summary_omits_the_block_when_every_skill_succeeded():
    summary = summarize_node({"project_path": ".", "input_filename": "your code"})

    assert "Skill generation failures" not in summary
