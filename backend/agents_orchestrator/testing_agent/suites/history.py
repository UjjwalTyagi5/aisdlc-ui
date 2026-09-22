"""The Testing page's history: each generation with the runs of the test cases it wrote.

The page opens empty. What was generated and run before is listed here and opened from
here, instead of being poured into the page on arrival. One entry is one generation — the
branch, who asked, the workbooks it filed — together with every run of those workbooks,
newest first. A run of a workbook no generation on record filed (one uploaded by hand) is
an entry of its own.

Entries are ordered by their latest activity, so a generation run again today sits above
one generated yesterday and never run since.
"""
from __future__ import annotations

from typing import Iterable, Optional

from agents_orchestrator.testing_agent.suites.jobs import Job


def _activity(job: Job) -> str:
    return max(filter(None, (job.created_at, job.started_at, job.finished_at)))


def _entry(generation: Optional[Job], runs: list[Job]) -> dict:
    runs = sorted(runs, key=lambda j: j.created_at, reverse=True)
    jobs = ([generation] if generation else []) + runs
    return {
        "id": generation.id if generation else runs[0].id,
        "updated_at": max(_activity(j) for j in jobs),
        "active": any(j.status in ("queued", "running") for j in jobs),
        "generation": generation.public() if generation else None,
        "runs": [r.public() for r in runs],
    }


def build_history(jobs: Iterable[Job]) -> list[dict]:
    """Every entry for these jobs (one project's), latest activity first."""
    jobs = list(jobs)
    generations = [j for j in jobs if j.kind == "generate"]
    filed_by: dict[str, Job] = {}
    # Oldest first, so a document id a later generation somehow also names stays with the first.
    for gen in sorted(generations, key=lambda j: j.created_at):
        for doc in (gen.result or {}).get("documents") or []:
            doc_id = str((doc or {}).get("artifact_id") or "")
            if doc_id:
                filed_by.setdefault(doc_id, gen)

    runs_of: dict[str, list[Job]] = {g.id: [] for g in generations}
    unfiled: list[Job] = []
    for run in (j for j in jobs if j.kind != "generate"):
        gen = filed_by.get(str((run.params or {}).get("document_id") or ""))
        if gen is None:
            unfiled.append(run)
        else:
            runs_of[gen.id].append(run)

    entries = [_entry(g, runs_of[g.id]) for g in generations] + [_entry(None, [r]) for r in unfiled]
    return sorted(entries, key=lambda e: e["updated_at"], reverse=True)
