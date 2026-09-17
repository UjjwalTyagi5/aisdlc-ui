"""The work a suite job does: generating suites, and running one.

Each workflow runs inside a background job (`jobs.start_job`) and binds the request's
tenant, project and user into the context itself: a background task does not inherit
contextvars set after it was created, and `register_generated_file` reads them.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile

from agents_orchestrator.testing_agent.suites import jobs
from agents_orchestrator.testing_agent.suites.context import build_repo_context
from agents_orchestrator.testing_agent.suites.excel import now_label, suite_filename, write_suite
from agents_orchestrator.testing_agent.suites.generate import generate_suites
from agents_orchestrator.testing_agent.suites.models import KIND_LABEL, SuiteMeta
from agents_orchestrator.testing_agent.suites.page import suite_markdown
from agents_orchestrator.testing_agent.suites.store import file_document, output_dir

logger = logging.getLogger(__name__)


def bind_context(job: jobs.Job) -> None:
    from config.ws_helper import set_project_id, set_session_id, set_tenant_id, set_user_id  # noqa: PLC0415

    set_tenant_id(job.tenant_id or None)
    set_project_id(job.project_id or None)
    set_user_id(job.user_id or None)
    set_session_id(job.id)


async def resolve_model(job: jobs.Job, offering_id: str | None) -> None:
    """Resolve the run's model (the page's picker) — raises with the reason when none is usable."""
    from shared.services.model_resolver import resolve_model_for_run, set_resolved_model  # noqa: PLC0415

    resolved = await resolve_model_for_run(job.tenant_id, None, offering_id=offering_id or None,
                                           project_id=job.project_id)
    set_resolved_model(resolved)


async def clone_target(job: jobs.Job, target: dict, work_dir: str) -> str:
    """Clone the branch as the requesting user; returns the commit sha ("" when unknown)."""
    from shared.services import repo_source  # noqa: PLC0415

    await jobs.log(job, f"Checking out {target['repo']} @ {target['branch']}")
    result = await repo_source.clone(
        job.tenant_id, target["ado_project"], target["repo"], target["branch"], work_dir,
        project_id=job.project_id, owner_id=job.user_id, provider=target.get("provider") or None,
    )
    return result.get("commit_sha") or ""


async def generate_workflow(job: jobs.Job, *, target: dict, kinds: list[str], offering_id: str | None) -> dict:
    from agents_orchestrator.testing_agent.config.shared import build_llm  # noqa: PLC0415
    from agents_orchestrator.testing_agent.project_record import _project_display_name, approved_documents_text  # noqa: PLC0415

    bind_context(job)
    await resolve_model(job, offering_id)
    work_dir = tempfile.mkdtemp(prefix="testing_suites_")
    try:
        commit = await clone_target(job, target, work_dir)

        await jobs.log(job, "Reading the project's approved documents")
        try:
            documents_text, sources = await approved_documents_text(job.tenant_id, job.project_id)
        except Exception:  # noqa: BLE001 — the code alone still yields cases; say so
            logger.warning("approved documents unavailable for suite generation", exc_info=True)
            documents_text, sources = "", ""
        project = ""
        try:
            project = await _project_display_name(job.tenant_id, job.project_id)
        except Exception:  # noqa: BLE001
            pass
        await jobs.log(job, f"Approved documents: {sources.split(': ', 1)[-1] if sources else 'none on file — cases come from the code'}")

        repo = build_repo_context(work_dir)
        if not repo.files:
            raise RuntimeError(f"no source files were found on {target['repo']} @ {target['branch']}")
        await jobs.log(job, f"Read {len(repo.shown)} of {len(repo.files)} source files" + (f" · {repo.app_notes}" if repo.app_notes else ""))

        llm = build_llm(max_tokens=16_000)
        results = await generate_suites(kinds, llm, repo=repo, documents_text=documents_text,
                                        project=project or target["repo"], report=lambda m: jobs.log(job, m))

        derived = "; ".join(p for p in (sources.split(": ", 1)[-1] if sources else "", "the code on the branch") if p)
        documents, failures = [], []
        for kind in kinds:
            outcome = results.get(kind) or {"error": "not generated"}
            if outcome.get("error"):
                failures.append({"kind": kind, "error": outcome["error"]})
                continue
            meta = SuiteMeta(kind=kind, project=project, source_project=target["ado_project"],
                             repository=target["repo"], branch=target["branch"],
                             commit=commit, generated_at=now_label(), sources=derived, app_notes=repo.app_notes)
            cases = outcome["cases"]
            filed = await file_document(
                write_suite(meta, cases), suite_markdown(meta, cases), suite_filename(kind, target["repo"]),
                user_id=job.user_id, job_id=job.id,
                note=f"{KIND_LABEL[kind]} test cases generated by the Testing agent from {derived}.",
            )
            await jobs.log(job, f"Filed {filed['name']} ({len(cases)} cases) as a draft")
            documents.append({"kind": kind, "cases": len(cases), "skipped": outcome.get("problems") or [], **filed})

        if not documents:
            raise RuntimeError("no suite could be generated: " + "; ".join(f"{KIND_LABEL[f['kind']]}: {f['error']}" for f in failures))
        return {"documents": documents, "failures": failures, "commit": commit}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def run_workflow(job: jobs.Job, *, document_id: str, base_url: str | None, headless: bool,
                       offering_id: str | None) -> dict:
    """Run the suite in a stored workbook and file its report."""
    import dataclasses
    import time

    from agents_orchestrator.testing_agent.suites import reports
    from agents_orchestrator.testing_agent.suites.excel import read_suite
    from agents_orchestrator.testing_agent.suites.store import document_bytes

    bind_context(job)
    row, data = await document_bytes(job.tenant_id, job.project_id, document_id)
    meta, cases, problems = read_suite(data)
    suite_name = (row.blob_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not cases:
        raise RuntimeError(f"{suite_name} has no runnable test case" + (f": {'; '.join(problems[:5])}" if problems else ""))
    await jobs.log(job, f"Running {len(cases)} {KIND_LABEL[meta.kind].lower()} test case(s) from {suite_name}")

    report_meta = reports.ReportMeta(
        kind=meta.kind, project=meta.project, repository=meta.repository, branch=meta.branch,
        suite_document=suite_name, suite_status=row.approval_status or "draft", started_at=now_label(),
        target_url=base_url or "",
    )
    if problems:
        report_meta.notes.append(f"{len(problems)} row(s) in the workbook could not run and are not in this report: "
                                 + "; ".join(problems[:10]))
    started = time.monotonic()
    extra: dict = {}

    async def say(message: str) -> None:
        await jobs.log(job, message)

    if meta.kind == "unit":
        from agents_orchestrator.testing_agent.config.shared import build_llm  # noqa: PLC0415
        from agents_orchestrator.testing_agent.suites import unit_run  # noqa: PLC0415

        await resolve_model(job, offering_id)
        work_dir = tempfile.mkdtemp(prefix="testing_unit_")
        try:
            target = {"ado_project": meta.source_project or meta.repository, "repo": meta.repository, "branch": meta.branch}
            report_meta.commit = await clone_target(job, target, work_dir)
            try:
                rows, files, runner, notes = await unit_run.execute(cases, work_dir, build_llm(max_tokens=16_000), say)
            except unit_run.UnitRunError as exc:
                raise RuntimeError(str(exc)) from exc
            report_meta.runner = runner
            report_meta.notes += notes
            extra["Generated tests"] = ([("File", 40), ("Code", 140)], [[p, c] for p, c in files.items()])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    elif meta.kind == "api":
        from agents_orchestrator.testing_agent.suites import api_run  # noqa: PLC0415

        rows, responses = await api_run.execute(cases, base_url or "", say)
        report_meta.runner = "HTTP client"
        extra["Responses"] = ([("ID", 10), ("Request", 50), ("Status", 10), ("Response", 100)], responses)
    else:
        from agents_orchestrator.testing_agent.suites import functional_run  # noqa: PLC0415

        shots_dir = os.path.join(output_dir(job.user_id, job.id), "screenshots")
        rows, steps = await functional_run.execute(cases, base_url or "", headless=headless, screenshots_dir=shots_dir, report=say)
        report_meta.runner = "Chrome (Selenium)" + (" · headless" if headless else "")
        extra["Steps"] = ([("ID", 10), ("Step", 6), ("Action", 50), ("Result", 10), ("Details", 80)], steps)

    report_meta.duration_s = time.monotonic() - started
    t = reports.totals(rows)
    await jobs.log(job, f"{t['Passed']} passed · {t['Failed']} failed · {t['Error']} could not run · {t['Not run']} not run")
    filed = await file_document(
        reports.write_report(report_meta, rows, extra), reports.report_markdown(report_meta, rows),
        reports.report_filename(meta.kind, meta.repository), user_id=job.user_id, job_id=job.id,
        note=f"{KIND_LABEL[meta.kind]} test report: {reports.verdict(rows)} — {t['Passed']} of {t['total']} passed.",
    )
    await jobs.log(job, f"Filed {filed['name']} as a draft")
    return {
        "documents": [{"kind": meta.kind, **filed}], "failures": [],
        "suite_kind": meta.kind, "suite_document_id": document_id, "suite_document": suite_name,
        "verdict": reports.verdict(rows), "totals": t, "rows": [dataclasses.asdict(r) for r in rows],
        "commit": report_meta.commit, "target_url": base_url or "",
    }
