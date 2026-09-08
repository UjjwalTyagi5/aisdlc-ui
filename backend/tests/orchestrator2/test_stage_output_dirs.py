"""Every agent that writes files has somewhere for the panel to find them.

FOUND BY RUNNING ALL NINE AGENTS AGAINST THE REAL STACK
(`scripts/live_all_agents_check.py`). The Project Manager agent produced
`Coffee_Ordering_App_Delivery_Plan.pdf` and wrote it to

    files/<user>/orchestrator/<run_id>/output/

— the SAME directory shape the Testing agent writes to and the panel resolves
correctly. But `_run_stage_output_dir` had no branch for `plan`, so it returned None
and the PDF was invisible in Deliverables. The Design agent writes its architecture
docx to that same path (`design_architecture_agent/agents/architecture.py`) and was
lost the same way.

The failure is silent by construction: a stage with no directory looks exactly like a
stage that generated nothing, which is why nine months of this went unnoticed and why
the mapping needs a test that enumerates rather than spot-checks.
"""
import os

import pytest

from agents_orchestrator.orchestrator2.registry import AGENT_IDS


#: Where each agent actually writes, read off the agents themselves:
#:   requirements  requirements_agent/agents/planning.py    -> requirements_agent/<run>/output
#:   plan          the Project Manager's export tool        -> orchestrator/<run>/output
#:   design        design_architecture_agent/.../architecture.py -> orchestrator/<run>/output
#:   testing       testing agent Nodes/finalize.py          -> orchestrator/<run>/output
#: Development has its own cloned workspace, and Documentation its own root, so both
#: are resolved by different branches and are not in this table.
_WRITES_UNDER = {
    "requirements": ("requirements_agent", "output"),
    "plan": ("orchestrator", "output"),
    "design": ("orchestrator", "output"),
    "testing": ("orchestrator", "output"),
}

_USER = "af57932d-f4f2-4673-aef7-d33b75c022f4"
_RUN = "4d0fa0d6-d38d-4b84-a743-51f27cfdb415"


@pytest.fixture()
def files_root(tmp_path, monkeypatch):
    """A tmp FILES root, so this never reads the developer's real output."""
    from shared.routers import runs

    monkeypatch.setattr(runs, "_stage_files_dir", lambda: str(tmp_path))
    return tmp_path


def _write(files_root, segment, rel, name="doc.pdf"):
    directory = files_root / _USER / segment / _RUN / rel
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("x", encoding="utf-8")
    return str(directory)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", sorted(_WRITES_UNDER))
async def test_the_directory_an_agent_writes_to_is_the_one_that_is_read(
    stage, files_root,
):
    """The whole bug in one assertion: a file written where the agent writes it must
    come back from the resolver the endpoint calls."""
    from shared.routers.runs import _run_stage_output_dir

    segment, rel = _WRITES_UNDER[stage]
    expected = _write(files_root, segment, rel)

    found = await _run_stage_output_dir(
        _RUN, stage, development_artifacts=None, tenant_id="t1", project_id="p1",
    )
    assert found is not None, (
        f"{stage} writes to {segment}/<run>/{rel} and nothing resolves it — its "
        f"documents cannot appear in Deliverables"
    )
    assert os.path.normcase(found) == os.path.normcase(expected)
    assert os.listdir(found) == ["doc.pdf"]


@pytest.mark.asyncio
async def test_a_stage_that_wrote_nothing_resolves_to_nothing(files_root):
    """An empty tree reads as a pull that failed. Better to show no tree at all."""
    from shared.routers.runs import _run_stage_output_dir

    assert await _run_stage_output_dir(
        _RUN, "plan", development_artifacts=None, tenant_id="t1", project_id="p1",
    ) is None


@pytest.mark.asyncio
async def test_one_stages_files_are_never_served_as_anothers(files_root):
    """`plan`, `design` and `testing` share a directory shape; only the run id and the
    stage's own branch separate them. A branch that fell through to a shared default
    would hand the Design agent's docx to the Testing agent's tree."""
    from shared.routers.runs import _run_stage_output_dir

    _write(files_root, "requirements_agent", "output", name="prd.docx")
    found = await _run_stage_output_dir(
        _RUN, "testing", development_artifacts=None, tenant_id="t1", project_id="p1",
    )
    assert found is None, "the Requirements agent's output was served as Testing's"


def test_one_directory_yields_one_tree_not_one_per_stage_that_resolves_it():
    """`plan`, `design` and `testing` all resolve the SAME directory.

    Mapping them (the fix above) made the panel show the identical files three times,
    once under each heading — so the Testing agent's `test_plan.xlsx` appeared under
    Design. Wrong attribution is worse than none: an empty heading reads as "nothing
    produced yet", a populated one reads as evidence.

    Nothing on disk says which agent wrote which file — the agents share the
    directory, `runs.stage` is set once at creation and orchestrator2 deliberately
    never writes a position back to a run. So the tree is emitted ONCE, under a
    documented priority, and exact per-agent attribution waits on the agents writing
    into per-agent subdirectories.
    """
    from shared.routers.runs import _dedupe_by_directory

    shared_dir = "/files/u1/orchestrator/r1/output"
    out = _dedupe_by_directory({
        "design": shared_dir,
        "plan": shared_dir,
        "testing": shared_dir,
        "requirements": "/files/u1/requirements_agent/r1/output",
    })
    assert out == {"design", "requirements"}, (
        "the shared directory must appear once, and a stage with its own directory "
        "must never be dropped with it"
    )


def test_the_run_s_own_stage_wins_the_shared_directory():
    """A run opened for the Project Manager must file its delivery plan under the
    Project Manager, not under whichever sharing stage happens to sort first."""
    from shared.routers.runs import _dedupe_by_directory

    shared_dir = "/files/u1/orchestrator/r1/output"
    kept = _dedupe_by_directory(
        {"design": shared_dir, "plan": shared_dir, "testing": shared_dir},
        prefer="plan",
    )
    assert kept == {"plan"}


def test_a_prefer_that_wrote_nothing_is_ignored():
    """`prefer` is the run's stage, which is set at creation and says nothing about
    who exported. A stage with no files must not be given a tree it did not fill."""
    from shared.routers.runs import _dedupe_by_directory

    shared_dir = "/files/u1/orchestrator/r1/output"
    kept = _dedupe_by_directory({"testing": shared_dir}, prefer="requirements")
    assert kept == {"testing"}


def test_the_priority_is_stable_so_the_tree_does_not_move_between_reads():
    """A set iterates in whatever order it likes. If the winner were picked from one,
    the same run would file its documents under a different agent on every refresh."""
    from shared.routers.runs import _dedupe_by_directory

    shared_dir = "/files/u1/orchestrator/r1/output"
    picks = {
        frozenset(_dedupe_by_directory(
            {s: shared_dir for s in ("testing", "plan", "design")}
        ))
        for _ in range(20)
    }
    assert len(picks) == 1


def test_paths_that_differ_only_in_case_or_separator_are_one_directory():
    """Windows. `_glob_user_scoped_dir` returns whatever `glob` produced, and the same
    directory reached through two stages can come back spelled differently."""
    from shared.routers.runs import _dedupe_by_directory

    out = _dedupe_by_directory({
        "design": r"C:\files\U1\orchestrator\r1\output",
        "plan": r"c:/files/u1/orchestrator/r1/output",
    })
    assert len(out) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["security", "code_review", "deployment"])
async def test_an_agent_with_no_output_of_its_own_is_not_handed_someone_elses(
    stage, files_root,
):
    """The shared `orchestrator/<run>/output/` directory belongs to whoever wrote it.

    Found by mutation: replacing the stage check with a bare `if stage:` broke no test,
    and it is not a hypothetical mis-write — it is what "just make them all use the
    shared directory" looks like. The Security agent would then show the Testing
    agent's `test_plan.xlsx` as its own review, which is worse than showing nothing:
    an empty tab reads as "nothing produced yet" and a wrong one reads as evidence.
    """
    from shared.routers.runs import _run_stage_output_dir

    _write(files_root, "orchestrator", "output", name="test_plan.xlsx")
    found = await _run_stage_output_dir(
        _RUN, stage, development_artifacts=None, tenant_id="t1", project_id="p1",
    )
    assert found is None, (
        f"{stage} was handed the shared output directory, whose files another agent "
        f"wrote"
    )


@pytest.mark.asyncio
async def test_every_agent_is_either_mapped_or_deliberately_unmapped(files_root):
    """The enumeration that stops the next agent being forgotten the way `plan` was.

    `_run_stage_output_dir` answers None for two DIFFERENT reasons — "this agent has
    no file output" and "nobody wrote the branch" — and they are indistinguishable at
    the call site. So the second reason has to be spelled out here, by name, and a new
    agent id fails this test until someone decides which it is.
    """
    from shared.routers import runs

    #: Agents that genuinely produce no files of their own yet. `security`,
    #: `code_review` and `deployment` write nothing today — `_GENERATED_STAGE_DIRS`
    #: reserves `generated/<stage>` for when they do — and all three were observed
    #: refusing outright in the live run rather than producing a document.
    no_file_output = {"security", "code_review", "deployment"}
    mapped = set(_WRITES_UNDER) | {"development", "documentation"}
    assert mapped | no_file_output == set(AGENT_IDS), (
        "an agent is neither mapped to an output directory nor listed as producing "
        "no files: " + ", ".join(sorted(set(AGENT_IDS) - (mapped | no_file_output)))
    )
    assert no_file_output == runs._GENERATED_STAGE_DIRS
