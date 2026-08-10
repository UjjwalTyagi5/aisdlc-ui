import os
import subprocess
from typing import TypedDict, List

from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# --- 1. Define the State ---
class GraphState(TypedDict, total=False):
    project_path: str
    linting_results: str
    test_results_xml: str
    coverage_xml: str
    summary: str
    error: str
    # Phase 8.10e — narrative hints passed in by the outer
    # Nodes/execute.py:run_code_testing_agent caller so summarize_node can
    # render the same rich format the .NET / React paths produce. All
    # optional — sub-agent stays usable without them.
    input_filename: str
    function_names: list
    plan_test_case_count: int
    plan_summary: str

# --- 2. Utility Function to run shell commands ---
def run_command(command: List[str], cwd: str) -> tuple[bool, str, str]:
    """Runs a shell command and captures its output."""
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        success = process.returncode == 0
        return success, process.stdout, process.stderr
    except Exception as e:
        return False, "", str(e)

# --- 3. Agent's Nodes (Steps) ---

def setup_environment_node(state: GraphState) -> GraphState:
    print("--- Setting up test environment ---")
    project_path = state['project_path']
    reports_dir = os.path.join(project_path, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    
    test_results_xml = os.path.join(reports_dir, "results.xml")
    coverage_xml = os.path.join(reports_dir, "coverage.xml")
    
    return {
        "test_results_xml": test_results_xml,
        "coverage_xml": coverage_xml
    }

def static_analysis_node(state: GraphState) -> GraphState:
    print("--- Running Static Analysis (pylint) ---")
    project_path = state['project_path']
    _, stdout, stderr = run_command(["pylint", "."], cwd=project_path)
    
    linting_report = stdout if stdout else "Pylint ran successfully with no issues."
    if stderr:
        linting_report += f"\n\nERRORS:\n{stderr}"
        
    return {"linting_results": linting_report}

def run_tests_node(state: GraphState) -> GraphState:
    print("--- Running Tests (pytest) ---")
    project_path = state['project_path']
    test_xml = state['test_results_xml']
    cov_xml = state['coverage_xml']

    print("Installing dependencies from requirements.txt...")
    success, _, stderr = run_command(["pip", "install", "-r", "requirements.txt"], cwd=project_path)
    if not success:
        return {"error": f"Failed to install dependencies: {stderr}"}

    command = [
        "pytest",
        f"--junitxml={test_xml}",
        f"--cov=.",
        f"--cov-report=xml:{cov_xml}",
    ]
    
    success, stdout, stderr = run_command(command, cwd=project_path)
    
    print(stdout)
    if not success and "no tests collected" not in stderr:
        print("Pytest finished with test failures or errors.")
    return {}

def summarize_node(state: GraphState) -> GraphState:
    """Phase 8.6 — deterministic QA-style summary mirroring Nodes/execute.py.

    Was: feed Claude raw pylint stdout + cobertura XML + junit XML and ask
    for a "concise markdown summary". Result was advisory text like
    "Add a trailing newline at end of test_generated_by_agent.py" that
    confused users + made every run inconsistent.

    Now: parse the XMLs deterministically and render via the shared
    `build_qa_summary` helper — same shape as the .NET / React paths.
    No LLM in the summary path.
    """
    print("--- Building deterministic QA summary (Python) ---")
    import os
    from agents_orchestrator.testing_agent.tools.artifact_builder import (
        _parse_coverage_xml, _parse_junit_xml, _parse_test_failures,
    )
    from agents_orchestrator.testing_agent.tools.qa_summary import build_qa_summary

    cov_path = state.get('coverage_xml')
    junit_path = state.get('test_results_xml')
    cov = _parse_coverage_xml(cov_path) if cov_path and os.path.exists(cov_path) else None
    exec_ = _parse_junit_xml(junit_path) if junit_path and os.path.exists(junit_path) else None
    if exec_:
        # Python path uses pytest, override the default
        exec_.framework = "pytest"

    linting_results = state.get('linting_results', '') or ''
    # pylint exits 0 when score == 10/10 and no message types are issued;
    # otherwise non-zero. Parse from output to keep the existing semantics.
    if "Your code has been rated at 10.00/10" in linting_results:
        lint_exit = 0
    elif linting_results.strip():
        lint_exit = 1
    else:
        lint_exit = 0

    # Phase 8.10e — per-failure detail
    failures_list = []
    if exec_ and (exec_.failed or exec_.errors) and junit_path:
        try:
            failures_list = _parse_test_failures(junit_path)
        except Exception:
            failures_list = []

    # Phase 8.10g — evidence-bound narrative (mirrors Nodes/execute.py).
    # This sub-agent doesn't have run_res in scope, so the execution-failed
    # signal is purely `exec_ is None` (junit XML missing or unparseable).
    # Was a real bug pre-8.10g: claimed "Ran pytest with coverage analysis"
    # even when execution failed before results were produced.
    input_filename = state.get("input_filename") or ""
    fn_names = state.get("function_names") or []
    fn_str = ", ".join(f"`{n}()`" for n in fn_names[:3])
    if fn_str and len(fn_names) > 3:
        fn_str += "…"
    plan_count = int(state.get("plan_test_case_count") or 0)
    plan_summary = state.get("plan_summary") or None

    parts = []
    # Subject — only claim "analyzed" when fn_names threaded in (means the
    # outer run_code_testing_agent had a code_analysis to share).
    if fn_names:
        label = f"`{input_filename}`" if input_filename else "your code"
        parts.append(f"I analyzed {label}")
    elif input_filename:
        parts.append(f"I prepared tests for `{input_filename}`")
    else:
        parts.append("I prepared tests")

    # Plan
    if plan_count and fn_str:
        parts.append(f", generated {plan_count} test case{'s' if plan_count != 1 else ''} covering {fn_str}")
    elif plan_count:
        parts.append(f", generated {plan_count} test case{'s' if plan_count != 1 else ''}")
    elif fn_str:
        parts.append(f" covering {fn_str}")

    # Outcome — exec_ is parsed from junit XML; None == execution failed
    if exec_ is not None and exec_.total > 0 and cov is not None:
        parts.append(". Ran pytest with coverage analysis. Here's what happened:")
    elif exec_ is not None and exec_.total > 0:
        parts.append(". Ran pytest. Test results were produced, but coverage was not collected.")
    elif exec_ is None:
        parts.append(". I prepared the test run, but execution failed before results were produced.")
    else:
        parts.append(". Here's what happened:")

    narrative_intro = "".join(parts)

    summary = build_qa_summary(
        lang="python",
        runner_command="pytest --junitxml=results.xml --cov=. --cov-report=xml --cov-report=term",
        exec_=exec_,
        cov=cov,
        pr_cov=None,                         # Python path doesn't use PR scope (pre-Phase-7)
        lint_exit=lint_exit,
        lint_tool="pylint",
        plan_test_case_count=plan_count,
        generated_test_file="test_generated_by_agent.py",
        test_failures=failures_list or None,
        narrative_intro=narrative_intro,
        test_plan_summary=plan_summary,
    )
    print("\n--- FINAL SUMMARY ---")
    print(summary)
    return {"summary": summary}

def error_node(state: GraphState) -> GraphState:
    print(f"--- A critical error occurred in the testing agent: {state['error']} ---")
    return {}

# --- 4. Define the Graph and its Edges ---

def decide_after_tests(state: GraphState):
    if state.get("error"):
        return "error_handler"
    return "summarize"

workflow = StateGraph(GraphState)

workflow.add_node("setup", setup_environment_node)
workflow.add_node("static_analysis", static_analysis_node)
workflow.add_node("run_tests", run_tests_node)
workflow.add_node("summarize", summarize_node)
workflow.add_node("error_handler", error_node)

workflow.set_entry_point("setup")
workflow.add_edge("setup", "static_analysis")
workflow.add_edge("static_analysis", "run_tests")
workflow.add_conditional_edges(
    "run_tests",
    decide_after_tests,
    {
        "summarize": "summarize",
        "error_handler": "error_handler",
    }
)
workflow.add_edge("summarize", END)
workflow.add_edge("error_handler", END)

app = workflow.compile()

# --- 5. Run the Agent ---

if __name__ == "__main__":
    project_to_test = os.path.abspath("sample_project")

    initial_state = {"project_path": project_to_test}

    # The 'stream' method lets us see the output of each node as it runs
    for output in app.stream(initial_state):
        # The 'stream' method returns a dictionary with the node name as the key
        for key, value in output.items():
            print(f"Output from node '{key}':")
            # You can inspect the intermediate state here if you wish
            # print(f"--- State after node {key} ---\n{value}\n---")
        print("\n---\n")