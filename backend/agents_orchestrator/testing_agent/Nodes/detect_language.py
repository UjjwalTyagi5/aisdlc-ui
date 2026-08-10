"""Language-detection node — Phase M.5.

Runs after `setup_workspace` / `setup_single_file_workspace` and before
`find_and_analyze_code`. Sets `state["language"]` and
`state["runner_command"]` so the downstream nodes can dispatch through
`get_runner(state["language"])` instead of hard-coding pytest.

Single-file uploads dispatch by extension; full workspaces dispatch via
`tools/runners.detect_runner(work_dir)` (priority order: dotnet → react → python).
"""
from __future__ import annotations

import os

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog, logger
from agents_orchestrator.testing_agent.tools.runners import detect_runner, get_runner


_EXT_TO_LANG = {
    ".py": "python",
    ".cs": "dotnet",
    ".jsx": "react",
    ".tsx": "react",
    ".js": "react",   # treat loose JS as react for MVP — narrows scope
    ".ts": "react",
}


async def detect_language(state: SuperAgentState):
    work_dir = state.get("work_dir")
    if not work_dir or not os.path.isdir(work_dir):
        logger.info("detect_language: no work_dir on state, defaulting language=unknown")
        return {"language": "unknown", "runner_command": None}

    # Single-file fast path: classify by uploaded extension if known.
    input_file = state.get("input_file_path")
    if input_file:
        ext = os.path.splitext(input_file)[1].lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            try:
                runner = get_runner(lang)
            except KeyError:
                runner = None
            cmd = runner.runner_command if runner else None
            logger.info(f"detect_language: single-file extension {ext!r} → language={lang}")
            blog(f"Detected language: {lang}")
            return {"language": lang, "runner_command": cmd}

    # Workspace fast path: walk and pick the highest-priority match.
    runner = detect_runner(work_dir)
    if runner is None:
        logger.info("detect_language: no language detected in work_dir, falling back to python")
        blog("Could not detect language — falling back to python", level="WARNING")
        py = get_runner("python")
        return {"language": "python", "runner_command": py.runner_command}

    logger.info(f"detect_language: detected language={runner.name} via workspace scan")
    blog(f"Detected language: {runner.name}")
    return {"language": runner.name, "runner_command": runner.runner_command}
