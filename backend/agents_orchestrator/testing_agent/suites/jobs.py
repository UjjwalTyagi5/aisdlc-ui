"""Background work for suites — generation and runs — with progress a page can follow.

A generation or a run takes minutes (clone, model calls, a browser). A request cannot wait
that long, and a chat turn is the wrong shape for it: the page needs to know what stage the
work is at and, afterwards, exactly what it produced or why it failed.

A job is recorded in memory and written to disk on every change (`files/testing-jobs/`), so
its result outlives the request and a restart; the page lists them in its history. A job
that was running when the backend stopped is reported as interrupted, never as still running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

JOBS_DIR = pathlib.Path(__file__).resolve().parents[3] / "files" / "testing-jobs"
MAX_PROGRESS = 200

Kind = str  # "generate" | "run_unit" | "run_functional" | "run_api"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    kind: Kind
    project_id: str
    tenant_id: str
    user_id: str
    #: Who started it, as the platform names them — shown in the page's history.
    user_name: str = ""
    status: str = "queued"               # queued | running | succeeded | failed | interrupted
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    progress: list[dict] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str = ""
    params: dict = field(default_factory=dict)   # never secrets

    def public(self) -> dict:
        data = asdict(self)
        data.pop("tenant_id", None)
        return data


_jobs: dict[str, Job] = {}
_tasks: dict[str, asyncio.Task] = {}


def _path(job: Job) -> pathlib.Path:
    return JOBS_DIR / job.project_id / f"{job.id}.json"


def _save(job: Job) -> None:
    try:
        p = _path(job)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(job), default=str), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        logger.warning("testing job %s could not be saved", job.id, exc_info=True)


def _load(project_id: str) -> list[Job]:
    folder = JOBS_DIR / project_id
    out: list[Job] = []
    if not folder.is_dir():
        return out
    for f in folder.glob("*.json"):
        try:
            job = Job(**json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
        if job.status in ("queued", "running") and job.id not in _tasks:
            job.status, job.error = "interrupted", "The backend restarted while this was running; start it again."
            job.finished_at = job.finished_at or _now()
            _save(job)
        out.append(job)
    return out


def get_job(project_id: str, job_id: str) -> Optional[Job]:
    job = _jobs.get(job_id)
    if job and job.project_id == project_id:
        return job
    return next((j for j in _load(project_id) if j.id == job_id), None)


def all_jobs(project_id: str) -> list[Job]:
    """Every job of the project, on disk or in flight."""
    merged = {j.id: j for j in _load(project_id)}
    merged.update({j.id: j for j in _jobs.values() if j.project_id == project_id})
    return list(merged.values())


def list_jobs(project_id: str, *, kind: Optional[str] = None, limit: int = 20) -> list[Job]:
    jobs = [j for j in all_jobs(project_id) if kind is None or j.kind == kind]
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)[:limit]


def active_job(project_id: str, kind: Kind) -> Optional[Job]:
    return next((j for j in _jobs.values()
                 if j.project_id == project_id and j.kind == kind and j.status in ("queued", "running")), None)


async def log(job: Job, message: str, level: str = "info") -> None:
    job.progress.append({"at": _now(), "level": level, "message": message})
    job.progress = job.progress[-MAX_PROGRESS:]
    _save(job)


def start_job(*, kind: Kind, project_id: str, tenant_id: str, user_id: str, params: dict,
              work: Callable[[Job], Awaitable[dict]], user_name: str = "") -> Job:
    """Create the job and run `work(job)` in the background. `work` returns the job's result;
    an exception fails the job with its message."""
    job = Job(id=uuid.uuid4().hex, kind=kind, project_id=project_id, tenant_id=tenant_id, user_id=user_id,
              user_name=user_name, params=params)
    _jobs[job.id] = job
    _save(job)

    async def runner() -> None:
        job.status, job.started_at = "running", _now()
        _save(job)
        try:
            job.result = await work(job) or {}
            job.status = "succeeded"
        except Exception as exc:  # noqa: BLE001 — recorded on the job, with the reason
            logger.exception("testing job %s (%s) failed", job.id, kind)
            job.status, job.error = "failed", str(exc) or type(exc).__name__
            await log(job, job.error, "error")
        finally:
            job.finished_at = _now()
            _save(job)
            _tasks.pop(job.id, None)

    _tasks[job.id] = asyncio.create_task(runner())
    return job
