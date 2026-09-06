"""Finalisation nodes — summary, packaging, cleanup.

Phase 4a — extracted from `super_agent.py`:
- generate_final_message    (line 667)
- package_final_reports     (line 696)
- _create_excel_bytes       (line 865)
- cleanup_workspace         (line 872)

Path layout in `package_final_reports` preserved EXACTLY (hardcoded `orchestrator/`
in the output dir) so existing UI URLs and the legacy router's behaviour match
byte-for-byte. Phase M will dispatch through `state["runner"]` for parts of this.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import tempfile
import shutil
import stat
from typing import Optional


def _rmtree(path: str) -> None:
    """shutil.rmtree that clears Windows read-only bits (e.g. .git objects)."""
    def _on_error(func, fpath, _exc_info):
        os.chmod(fpath, stat.S_IWRITE)
        func(fpath)
    try:
        shutil.rmtree(path, onexc=_on_error)   # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=_on_error)  # Python ≤ 3.11

import aiofiles
import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import (
    BROADCASTING_AVAILABLE,
    blog,
    broadcast_file_generated,
    esett,
    get_llm,
    get_session_id,
    get_user_id,
    logger,
    record_response_usage,
)
from agents_orchestrator.testing_agent.tools.artifact_builder import _build_testing_artifact


def _create_excel_bytes(df: pd.DataFrame) -> bytes:
    """Helper to create Excel bytes (runs in executor)."""
    output_buffer = io.BytesIO()
    with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='TestPlan')
    return output_buffer.getvalue()


async def generate_final_message(state: SuperAgentState):
    logger.info("Generating final user message...")
    blog("Generating final summary...")

    if state.get("final_user_message"):
        return {}

    message = ""
    user_prompt = state.get("user_prompt", "your request")

    if state.get("test_execution_summary"):
        message = state.get("test_execution_summary")
    elif state.get("test_plan"):
        num_cases = len(state["test_plan"].test_cases)
        prompt = (
            f"I have generated a detailed test plan with {num_cases} cases for '{user_prompt}'. "
            f"You can find the plan in the Excel file."
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, get_llm().invoke, prompt)
        record_response_usage(response)
        message = response.content
    else:
        message = "Processing is complete."

    history = state.get("chat_history", [])
    if not history or history[-1].content != user_prompt:
        history.append(HumanMessage(content=user_prompt))
    history.append(AIMessage(content=message))

    blog("Final message generated")
    return {"final_user_message": message, "chat_history": history}


async def package_final_reports(state: SuperAgentState):
    logger.info("Packaging all final reports into memory...")
    blog("Packaging final reports...")

    outputs: dict = {}
    session_id = get_session_id() if BROADCASTING_AVAILABLE else "default_session"

    output_dir: Optional[str] = None
    if BROADCASTING_AVAILABLE:
        user_id = get_user_id()
        output_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"
        os.makedirs(output_dir, exist_ok=True)

    # Test plan → Excel
    if state.get('test_plan') and state['test_plan'].test_cases:
        df = pd.DataFrame([tc.model_dump() for tc in state['test_plan'].test_cases])
        df = df[['test_case_id', 'feature_or_function_tested', 'scenario_type',
                 'test_summary', 'test_data', 'test_steps', 'expected_result']]
        loop = asyncio.get_running_loop()
        excel_bytes = await loop.run_in_executor(None, _create_excel_bytes, df)
        # Key MUST be `excel_plan_b64` to match the output_map at
        # testing_agent_api.py:632 — otherwise Generated_Test_Plan.xlsx never
        # gets written into the download zip.
        outputs['excel_plan_b64'] = base64.b64encode(excel_bytes).decode('utf-8')

        if BROADCASTING_AVAILABLE and output_dir:
            excel_path = os.path.join(output_dir, "test_plan.xlsx")
            async with aiofiles.open(excel_path, "wb") as f:
                await f.write(excel_bytes)
            await broadcast_file_generated(session_id, "test_plan.xlsx", excel_path)

    # UI test results → Excel + HTML
    if state.get('ui_test_results'):
        results = state['ui_test_results']
        df = pd.DataFrame(results)
        loop = asyncio.get_running_loop()
        excel_bytes = await loop.run_in_executor(None, _create_excel_bytes, df)
        outputs['ui_test_report_b64'] = base64.b64encode(excel_bytes).decode('utf-8')

        rows = "".join([
            f"<tr><td>{r.get('id','')}</td><td>{r.get('description','')}</td>"
            f"<td style='color:{'green' if r.get('status') == 'Pass' else 'red'};'>{r.get('status','')}</td>"
            f"<td>{r.get('detail','')}</td></tr>"
            for r in results
        ])
        html_content = (
            "<!DOCTYPE html><html><body><h2>UI Test Results</h2>"
            "<table><tr><th>ID</th><th>Description</th><th>Status</th><th>Details</th></tr>"
            f"{rows}</table></body></html>"
        )
        outputs['ui_test_report_html'] = html_content

        if BROADCASTING_AVAILABLE and output_dir:
            ui_excel_path = os.path.join(output_dir, "ui_test_results.xlsx")
            async with aiofiles.open(ui_excel_path, "wb") as f:
                await f.write(excel_bytes)
            await broadcast_file_generated(session_id, "ui_test_results.xlsx", ui_excel_path)

            ui_html_path = os.path.join(output_dir, "ui_test_results.html")
            async with aiofiles.open(ui_html_path, "w", encoding="utf-8") as f:
                await f.write(html_content)
            await broadcast_file_generated(session_id, "ui_test_results.html", ui_html_path)

            # Phase 8.10c — UI PDF + screenshots already exist on disk (written
            # directly by tools/ui_testing_agent.py). Broadcast the PDF so the
            # frontend file panel knows about it; screenshots get bundled into
            # the final zip by the API layer (see testing_agent_api.py zip
            # block) but we don't broadcast each screenshot individually to
            # avoid spamming the file-generated event channel.
            ui_pdf_path = os.path.join(output_dir, "ui_test_results.pdf")
            if os.path.isfile(ui_pdf_path):
                await broadcast_file_generated(session_id, "ui_test_results.pdf", ui_pdf_path)

    # Final summary as markdown
    if state.get('final_user_message'):
        outputs['final_summary_md'] = state['final_user_message']
        if BROADCASTING_AVAILABLE and output_dir:
            summary_path = os.path.join(output_dir, "execution_summary.md")
            async with aiofiles.open(summary_path, "w", encoding="utf-8") as f:
                await f.write(state['final_user_message'])
            await broadcast_file_generated(session_id, "execution_summary.md", summary_path)

    # Generated test code
    if state.get('generated_test_code'):
        outputs['generated_code_py'] = state['generated_test_code']
        if BROADCASTING_AVAILABLE and output_dir:
            test_code_path = os.path.join(output_dir, "generated_test_code.py")
            async with aiofiles.open(test_code_path, "w", encoding="utf-8") as f:
                await f.write(state['generated_test_code'])
            await broadcast_file_generated(session_id, "generated_test_code.py", test_code_path)

    # Coverage + other reports from workspace
    work_dir = state.get('work_dir')
    if work_dir:
        coverage_source_path = os.path.join(work_dir, 'reports', 'coverage.xml')
        if os.path.exists(coverage_source_path):
            async with aiofiles.open(coverage_source_path, 'r', encoding='utf-8') as f:
                coverage_content = await f.read()
            outputs['coverage_report_xml'] = coverage_content

            if BROADCASTING_AVAILABLE and output_dir:
                coverage_dest_path = os.path.join(output_dir, "coverage_report.xml")
                async with aiofiles.open(coverage_dest_path, "w", encoding="utf-8") as f:
                    await f.write(coverage_content)
                await broadcast_file_generated(session_id, "coverage_report.xml", coverage_dest_path)

                # Phase 11.1 — render the human-readable HTML alongside the raw
                # XML so the View Test Files panel includes a clickable
                # coverage.html. Best-effort: malformed XML produces a
                # placeholder div instead of crashing the report packaging.
                try:
                    from agents_orchestrator.testing_agent.tools.coverage_html import (
                        coverage_xml_to_html,
                    )
                    coverage_html_str = coverage_xml_to_html(coverage_source_path)
                    coverage_html_path = os.path.join(output_dir, "coverage.html")
                    async with aiofiles.open(coverage_html_path, "w", encoding="utf-8") as f:
                        await f.write(
                            "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
                            "<title>Coverage Report</title></head><body>"
                            + coverage_html_str
                            + "</body></html>"
                        )
                    await broadcast_file_generated(session_id, "coverage.html", coverage_html_path)
                except Exception as exc:
                    logger.warning(f"Phase 11.1: coverage HTML render failed (non-fatal): {exc}")

        reports_dir = os.path.join(work_dir, 'reports')
        if os.path.exists(reports_dir) and BROADCASTING_AVAILABLE and output_dir:
            for report_file in os.listdir(reports_dir):
                if report_file in ('coverage.xml',):
                    continue
                source_path = os.path.join(reports_dir, report_file)
                if not os.path.isfile(source_path):
                    continue
                dest_path = os.path.join(output_dir, report_file)
                try:
                    if report_file.endswith(('.txt', '.log', '.md', '.json', '.html', '.xml')):
                        async with aiofiles.open(source_path, 'r', encoding='utf-8') as src:
                            content = await src.read()
                        async with aiofiles.open(dest_path, 'w', encoding='utf-8') as dst:
                            await dst.write(content)
                    else:
                        async with aiofiles.open(source_path, 'rb') as src:
                            content = await src.read()
                        async with aiofiles.open(dest_path, 'wb') as dst:
                            await dst.write(content)
                    await broadcast_file_generated(session_id, report_file, dest_path)
                    logger.info(f"Broadcasted additional report: {report_file}")
                except Exception as exc:
                    logger.warning(f"Failed to copy and broadcast {report_file}: {exc}")

    # Code analysis JSON
    if state.get('code_analysis') and state['code_analysis'].functions:
        analysis_json = json.dumps(state['code_analysis'].model_dump(), indent=2)
        outputs['code_analysis_json'] = analysis_json
        if BROADCASTING_AVAILABLE and output_dir:
            analysis_path = os.path.join(output_dir, "code_analysis.json")
            async with aiofiles.open(analysis_path, "w", encoding="utf-8") as f:
                await f.write(analysis_json)
            await broadcast_file_generated(session_id, "code_analysis.json", analysis_path)

    # Requirement analysis JSON
    if state.get('requirement_analysis') and state['requirement_analysis'].requirements:
        req_analysis_json = json.dumps(state['requirement_analysis'].model_dump(), indent=2)
        outputs['requirement_analysis_json'] = req_analysis_json
        if BROADCASTING_AVAILABLE and output_dir:
            req_analysis_path = os.path.join(output_dir, "requirement_analysis.json")
            async with aiofiles.open(req_analysis_path, "w", encoding="utf-8") as f:
                await f.write(req_analysis_json)
            await broadcast_file_generated(session_id, "requirement_analysis.json", req_analysis_path)

    # Phase 3 — assemble TestingArtifact and add to outputs (consumed by handoff_router in the API layer)
    if BROADCASTING_AVAILABLE and output_dir and os.path.isdir(output_dir):
        saved_filenames = sorted(
            f for f in os.listdir(output_dir) if os.path.isfile(os.path.join(output_dir, f))
        )
    else:
        saved_filenames = []
    # Phase 8.4 — thread output_dir through state so _build_testing_artifact
    # can rewrite the artifact's coverage_xml_path / junit_xml_path to point
    # at durable output_dir copies (work_dir gets deleted by cleanup_workspace
    # → the original temp paths become stale).
    if output_dir:
        state["output_dir"] = output_dir
    try:
        artifact = _build_testing_artifact(state, saved_filenames)
        artifact_json = artifact.model_dump_json(indent=2)
        outputs['testing_artifact_json'] = artifact_json
        if BROADCASTING_AVAILABLE and output_dir:
            artifact_path = os.path.join(output_dir, "testing_artifact.json")
            async with aiofiles.open(artifact_path, "w", encoding="utf-8") as f:
                await f.write(artifact_json)
            await broadcast_file_generated(session_id, "testing_artifact.json", artifact_path)
            logger.info(
                f"TestingArtifact built — status={artifact.status}, "
                f"cases={artifact.plan_test_case_count}, "
                f"coverage={artifact.coverage.coverage_pct if artifact.coverage else 'n/a'}%"
            )
        # Capture the unit run (generated files + coverage + repo coords) so the
        # gated "Open tests PR" button can push them to ADO after the run. work_dir
        # still exists here (cleanup_workspace runs after this node).
        try:
            from agents_orchestrator.testing_agent.config import unit_pr_store
            unit_pr_store.capture(session_id, state, artifact)
        except Exception as exc:
            logger.warning(f"unit_pr_store capture failed (non-fatal): {exc}")
    except Exception as exc:
        logger.warning(f"Failed to build TestingArtifact: {exc}")

    blog("All reports packaged and broadcasted successfully")
    # output_dir must be in the return dict — LangGraph only persists state from
    # the node's return value, not from direct dict mutation on the input state.
    result: dict = {"final_outputs": outputs}
    if output_dir:
        result["output_dir"] = output_dir
    return result


def _workspace_is_ours_to_delete(state: SuperAgentState, work_dir: str) -> bool:
    """Only remove a workspace this agent created.

    This node used to rmtree whatever `work_dir` pointed at. But the highest-priority
    workspace source is one the agent did NOT create: the Copilot passes its shared
    run clone (`ps.work_dir`) — the single checkout Code Review, Security, Deployment
    and Documentation all read for that run — straight into this graph. Running the
    testing stage therefore deleted the repo every later stage still needed, and the
    stage that broke was never the stage that did it.

    setup_workspace states ownership explicitly. When the flag is absent (a session
    checkpointed before it existed), fall back to recognising this agent's own
    handiwork: a directory directly inside the system temp root whose name carries a
    prefix our `tempfile.mkdtemp` calls produce. "Somewhere under the temp root" is
    NOT enough on its own — plenty of other tools put real directories there, and a
    caller can legitimately hand us one. Anything unrecognised is left alone: a
    leaked temp directory is recoverable, someone else's checkout is not.
    """
    flag = state.get("workspace_is_ephemeral")
    if flag is not None:
        return bool(flag)
    try:
        tmp_root = os.path.realpath(tempfile.gettempdir())
        resolved = os.path.realpath(work_dir)
        if os.path.dirname(resolved) != tmp_root:
            return False
        # "testing_agent_*" covers the named clone/api workspaces; "tmp*" is
        # mkdtemp's own default prefix, used by the zip-extract path.
        name = os.path.basename(resolved)
        return name.startswith("testing_agent_") or name.startswith("tmp")
    except Exception:  # noqa: BLE001 — different drive, unreadable path: not ours
        return False


async def cleanup_workspace(state: SuperAgentState):
    logger.info("Cleaning up workspace...")

    work_dir = state.get('work_dir')
    if not work_dir or not os.path.exists(work_dir):
        return {}

    if not _workspace_is_ours_to_delete(state, work_dir):
        logger.info("Leaving caller-supplied workspace in place: %s", work_dir)
        blog("Left the shared run workspace in place (this run did not create it)")
        return {}

    blog("Cleaning up workspace...")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _rmtree, work_dir)
    logger.info("Workspace cleaned up.")
    blog("Workspace cleaned up successfully")
    return {}
