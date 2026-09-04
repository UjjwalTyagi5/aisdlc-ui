"""Project Manager agent — work breakdown and estimation (PM agent, phase 2).

Sits between Design and Development. It reads what was asked for and what was
designed, turns that into estimable tasks, sizes them, and records the result as
`plan_artifacts` for Development to read.

NAMED schedule.py, NOT planning.py. Both the Requirements and Design packages already
ship an `agents/planning.py`, and under pytest the dotted module name resolved to the
wrong one — a latent shadowing hazard already hit once in this codebase. A third copy of
that filename would make it a coin toss.

WHAT THIS PHASE DOES NOT DO. Scheduling into sprints and levelling against capacity are
phase 3; the connector reads they need landed in phase 0b but the tools here stop at a
sized backlog. The prompt says so, because an agent that implies it produced a schedule
when it produced a task list is the same class of false success as an upload that
reports success into an empty container.
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, Any, List, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from config.checkpoint import build_checkpointer as _build_checkpointer
from shared.tools.mcp_runtime import MCP_TOOLS_PROMPT_NOTE, get_mcp_tools

logger = logging.getLogger(__name__)


# ── Reading what the plan is built from ──────────────────────────────────────


@tool
async def read_project_inputs() -> str:
    """Load this project's requirements and its accepted design.

    Call this FIRST, before proposing any breakdown. Planning from memory when the
    project has stored requirements produces a plan for a system nobody asked for.

    Returns both upstream artifacts as text, or says plainly which are missing.
    """
    from config.context_broker import build_context_for_project  # noqa: PLC0415
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    project_id, tenant_id = get_project_id(), get_tenant_id()
    if not project_id or not tenant_id:
        return (
            "This conversation is not attached to a project, so there are no stored "
            "requirements or design to read. Plan from what the user describes."
        )

    context = await build_context_for_project(project_id, tenant_id, "plan")
    if not context:
        return (
            "This project has no requirements or design recorded yet. Ask the user what "
            "is being built, or plan from what they describe."
        )
    return context


@tool
async def list_sprints(provider: str = "") -> str:
    """List the board's sprints or iterations, with their dates.

    `provider` picks the board when a project has more than one ("jira", "ado"); omit it
    for the stage's default.

    A Kanban-only board has no sprints, and that is a legitimate setup rather than an
    error — plan in dated phases instead.
    """
    from agents_orchestrator.requirements_agent.agents.planning import (  # noqa: PLC0415
        _board_connector, _board_error,
    )

    connector, err = await _board_connector(provider=provider)
    if err:
        return err
    try:
        sprints = await connector.read_adapter("list_sprints", project=await _board_project())
    except Exception as exc:  # noqa: BLE001
        return _board_error(exc)
    if not sprints:
        return (
            "This board has no sprints — it is Kanban, or none have been created. "
            "Plan in dated phases rather than iterations."
        )
    return json.dumps(sprints, indent=2, default=str)


@tool
async def read_team_capacity(iteration_id: str, provider: str = "") -> str:
    """Per-person capacity for one sprint: hours a day, net of days off.

    NOT AVAILABLE ON EVERY BOARD. Azure DevOps exposes capacity; Jira has no capacity
    API at all — there it lives in a plugin or in people's calendars. On Jira this
    returns a refusal, and the right response is to ask the user for the team's
    availability rather than to assume it.
    """
    from agents_orchestrator.requirements_agent.agents.planning import (  # noqa: PLC0415
        _board_connector, _board_error,
    )

    connector, err = await _board_connector(provider=provider)
    if err:
        return err
    try:
        rows = await connector.read_adapter(
            "team_capacity", project=await _board_project(), iteration_id=iteration_id
        )
    except Exception as exc:  # noqa: BLE001
        return _board_error(exc)
    if not rows:
        return (
            "No capacity is recorded for that sprint. Ask the user how much time each "
            "person has rather than assuming a full sprint each."
        )
    return json.dumps(rows, indent=2, default=str)


async def _board_project() -> str:
    """Which board project this plan is against, from the requirements payload.

    `ingest_board` records `board_project` there when stories are pulled, and that is
    the only place it is written — there is no context variable for it. Returning "" is
    correct when nothing has been pulled: the connector then answers for its default
    project, which is right for a single-project board and wrong to guess at otherwise.
    """
    from config.context_broker import _fetch_artifacts_for_project  # noqa: PLC0415
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    project_id, tenant_id = get_project_id(), get_tenant_id()
    if not project_id or not tenant_id:
        return ""
    try:
        artifacts = await _fetch_artifacts_for_project(project_id, tenant_id) or {}
        payload = artifacts.get("requirements_payload") or {}
        return str(payload.get("board_project") or "")
    except Exception:  # noqa: BLE001
        # A board name is a convenience, not a precondition — never fail a tool for it.
        return ""


# ── Producing the plan ───────────────────────────────────────────────────────


@tool
async def save_plan(
    tasks_json: str = "",
    schedule_json: str = "",
    assignments_json: str = "",
    risks_json: str = "",
    board_project: str = "",
) -> str:
    """Record the plan on the project so Development can read it.

    Call this once the user is satisfied with the breakdown. Each argument is a JSON
    array and every one is optional — a breakdown with no schedule yet is a legitimate
    intermediate state, and refusing to save it would lose work the user just approved.

    THE FIRST SAVE SETS THE BASELINE, and later saves do not move it. "How far have we
    slipped" is answerable only against what was originally committed to, and a baseline
    that follows the current plan always reports zero slip.
    """
    from config.ws_helper import get_project_id, get_session_id, get_tenant_id  # noqa: PLC0415
    from shared.models.artifacts import PlanArtifact  # noqa: PLC0415

    tenant_id, project_id = get_tenant_id(), get_project_id()
    if not tenant_id or not project_id:
        return "This conversation is not attached to a project, so there is nowhere to save the plan."

    def _load(raw: str, field: str) -> Optional[list]:
        if not raw or not raw.strip():
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} is not valid JSON: {exc}") from exc
        return value if isinstance(value, list) else [value]

    try:
        tasks = _load(tasks_json, "tasks")
        schedule = _load(schedule_json, "schedule")
        assignments = _load(assignments_json, "assignments")
        risks = _load(risks_json, "risks")
    except ValueError as exc:
        return f"Could not save: {exc}"

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.services.artifact_service import persist_artifact  # noqa: PLC0415
    from shared.services.chat_artifacts import _get_or_create_chat_run  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as session:
        run_id = await _get_or_create_chat_run(session, tenant_id, project_id, "plan")
        await session.commit()
    if not run_id:
        return "Could not resolve a run to attach the plan to."

    existing = await _existing_plan(tenant_id, run_id)
    baseline = (existing or {}).get("baseline")
    if baseline is None and schedule is not None:
        baseline = {"schedule": schedule}

    artifact = PlanArtifact(
        agent_session_id=get_session_id() or "",
        tasks=tasks,
        schedule=schedule,
        assignments=assignments,
        risks=risks,
        baseline=baseline,
        board_project=board_project or await _board_project() or None,
    )
    await persist_artifact(run_id, "plan", artifact.model_dump(), tenant_id=tenant_id)

    counts = ", ".join(
        f"{len(v)} {name}"
        for name, v in (("tasks", tasks), ("scheduled items", schedule),
                        ("assignments", assignments), ("risks", risks))
        if v
    )
    return (
        f"Plan saved to the project ({counts or 'no items yet'}). "
        "It is awaiting a project admin's approval before it becomes the committed plan."
    )


async def _existing_plan(tenant_id: str, run_id: str) -> Optional[dict]:
    """The plan already on this run, so a re-save keeps the original baseline."""
    from sqlalchemy import select  # noqa: PLC0415

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import Run  # noqa: PLC0415

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            return (
                await session.execute(select(Run.plan_artifacts).where(Run.id == run_id))
            ).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        return None



# ── Sequencing and levelling ─────────────────────────────────────────────────


@tool
async def build_schedule(tasks_json: str, sprints_json: str = "", team_json: str = "") -> str:
    """Sequence estimated tasks into sprints, respecting dependencies and capacity.

    `tasks_json`   [{"id", "title", "estimate", "depends_on": [ids]}]
    `sprints_json` [{"id", "name", "start_date", "finish_date", "capacity"}] — pass the
                   output of list_sprints, or your own dated phases.
    `team_json`    [{"name", "capacity_per_day", "days_off"}] from read_team_capacity,
                   used to work out each sprint's ceiling when the sprint has none.

    THE ARITHMETIC IS DONE HERE, NOT BY YOU. Do not compute the packing yourself and
    describe it — call this and report what it returns.

    Anything it could not place comes back under `unscheduled` WITH A REASON: no
    estimate, larger than any sprint, or stuck in a dependency cycle. Relay those to the
    user; they are the decisions only a person can make.
    """
    from agents_orchestrator.pm_agent import scheduling  # noqa: PLC0415

    try:
        tasks = _as_list(tasks_json, "tasks")
        sprints = _as_list(sprints_json, "sprints") or []
        team = _as_list(team_json, "team") or []
    except ValueError as exc:
        return f"Could not build a schedule: {exc}"

    if not tasks:
        return "No tasks were given, so there is nothing to schedule."

    result = scheduling.build_schedule(tasks, sprints, team)
    return json.dumps(result, indent=2, default=str)


@tool
async def allocate_resources(schedule_json: str, team_json: str) -> str:
    """Assign a schedule's work to people and report who is over-allocated.

    `schedule_json` the `schedule` array build_schedule returned.
    `team_json`     [{"name", "capacity_per_day", "sprint_days", "days_off"}] or
                    [{"name", "hours"}].

    Work already carrying `assigned_to` keeps that person — somebody decided it, and
    reshuffling real assignments is how a plan stops being trusted.

    OVER-ALLOCATION IS REPORTED, NOT SILENTLY AVOIDED. Tell the user who is over and by
    how much; moving work or extending a sprint is their call, not yours.
    """
    from agents_orchestrator.pm_agent import scheduling  # noqa: PLC0415

    try:
        schedule = _as_list(schedule_json, "schedule") or []
        team = _as_list(team_json, "team") or []
    except ValueError as exc:
        return f"Could not allocate: {exc}"

    if not team:
        return (
            "No team capacity was given, so nothing can be assigned against it. On Azure "
            "DevOps call read_team_capacity; on Jira ask the user who is available and "
            "for how long — Jira has no capacity API."
        )

    return json.dumps(scheduling.allocate(schedule, team), indent=2, default=str)


def _as_list(raw: str, field: str) -> Optional[list]:
    """Parse a JSON array argument the model wrote, or say which one was malformed."""
    if not raw or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is not valid JSON: {exc}") from exc
    if isinstance(value, dict):
        # list_sprints and build_schedule both return objects with the array inside.
        for key in ("schedule", "sprints", "tasks", "assignments", "value", "items"):
            if isinstance(value.get(key), list):
                return value[key]
        return [value]
    return value if isinstance(value, list) else [value]



# ── Risk and status ──────────────────────────────────────────────────────────


@tool
async def track_risks(risks_json: str, schedule_json: str = "") -> str:
    """Attach risks to the work they threaten and total the exposure.

    `risks_json`    [{"title", "impact", "owner", "threatens": [task ids]}]
    `schedule_json` the `schedule` array from build_schedule, so exposure can be summed.

    A risk that names no specific work is KEPT, not dropped — "the vendor contract is
    unsigned" threatens the project without pointing at a task. Its exposure just cannot
    be quantified, and the output says so.

    `unknown_links` names ids a risk points at that are not in the plan. Relay them:
    usually a typo, sometimes work nobody scheduled.
    """
    from agents_orchestrator.pm_agent import reporting  # noqa: PLC0415

    try:
        risks = _as_list(risks_json, "risks") or []
        schedule = _as_list(schedule_json, "schedule") or []
    except ValueError as exc:
        return f"Could not assess risks: {exc}"

    if not risks:
        return "No risks were given, so there is nothing to assess."
    return json.dumps(reporting.assess_risks(risks, schedule), indent=2, default=str)


@tool
async def status_report(
    schedule_json: str, board_items_json: str = "", baseline_json: str = ""
) -> str:
    """Progress, velocity, a forecast, and slippage against the baseline.

    `schedule_json`     the `schedule` array from build_schedule or the saved plan.
    `board_items_json`  the CURRENT board items, so completion is read from what the
                        team actually finished rather than from the plan. Without it,
                        the report says so.
    `baseline_json`     the plan's `baseline`, for slippage.

    DO NOT COMPUTE ANY OF THIS YOURSELF. "Are we on track" has a numeric answer, and a
    sentence estimating it is one nobody can check.

    Several fields can come back null WITH A REASON — velocity from a single sprint, a
    forecast with nothing completed, slippage with no baseline. Relay the reason rather
    than filling the gap: each of those has a plausible wrong answer that reads as fact.
    """
    from agents_orchestrator.pm_agent import reporting  # noqa: PLC0415

    try:
        schedule = _as_list(schedule_json, "schedule") or []
        items = _as_list(board_items_json, "board items") or []
        baseline_raw = _as_list(baseline_json, "baseline")
    except ValueError as exc:
        return f"Could not build a status report: {exc}"

    if not schedule:
        return "No schedule was given, so there is no progress to report."

    progress = reporting.summarise_progress(schedule, items)
    velocity = reporting.velocity_from(progress["sprints"])
    forecast = reporting.forecast_completion(progress["total_remaining"], velocity.get("velocity"))

    baseline = None
    if baseline_raw:
        first = baseline_raw[0] if isinstance(baseline_raw, list) else baseline_raw
        baseline = first if isinstance(first, dict) else None
    slippage = reporting.compare_to_baseline(schedule, baseline)

    return json.dumps(
        {"progress": progress, "velocity": velocity, "forecast": forecast, "baseline": slippage},
        indent=2, default=str,
    )



# ── Re-planning and budget ───────────────────────────────────────────────────


@tool
async def replan(
    schedule_json: str, changes_json: str, sprints_json: str = "",
    team_json: str = "", baseline_json: str = "",
) -> str:
    """Re-sequence after a change and report WHAT MOVED.

    `changes_json` [{"op": "add"|"remove"|"reestimate", "id", "estimate", "depends_on"}]

    Returns the new schedule plus the delta: which work slipped a sprint (`moved`),
    what fell out entirely (`no_longer_scheduled`), and how much the total grew
    (`committed_change`).

    RELAY THE DELTA, NOT JUST THE NEW PLAN. "What does this change cost us" is the
    question being asked; handing back a fresh schedule and leaving the user to diff
    two lists is how a re-plan gets waved through without anyone seeing what it did.

    `changes_rejected` names operations on work that is not in the plan — usually a
    typo. A rejected change is NOT applied, so say so rather than letting the user
    believe it took effect.
    """
    from agents_orchestrator.pm_agent import scheduling  # noqa: PLC0415

    try:
        current = _as_list(schedule_json, "schedule") or []
        changes = _as_list(changes_json, "changes") or []
        sprints = _as_list(sprints_json, "sprints") or []
        team = _as_list(team_json, "team") or []
        baseline = _as_list(baseline_json, "baseline") or []
    except ValueError as exc:
        return f"Could not re-plan: {exc}"

    if not current:
        return "No current schedule was given, so there is nothing to re-plan."
    if not changes:
        return "No changes were given, so the plan is unchanged."

    if not sprints:
        # Reuse the shape the current plan already has rather than inventing sprints.
        sprints = [
            {k: s.get(k) for k in ("id", "name", "start_date", "finish_date", "capacity")}
            for s in current
        ]

    return json.dumps(
        scheduling.replan(current, changes, sprints, team, baseline), indent=2, default=str
    )


@tool
async def budget_status() -> str:
    """This project's LLM budget and what it has spent so far.

    WHAT THIS BUDGET IS. The platform meters LLM spend — the cost of running the agents
    — against a cap set per project. It is NOT a labour budget and knows nothing about
    what the team costs.

    So do not compare it with a plan's effort, add the two together, or answer "can we
    afford this plan" from it. If the user wants the plan costed, use `cost_plan` with
    rates they supply, and keep the two figures apart.
    """
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    project_id, tenant_id = get_project_id(), get_tenant_id()
    if not project_id or not tenant_id:
        return "This conversation is not attached to a project, so there is no budget to read."

    try:
        from sqlalchemy import select  # noqa: PLC0415

        from shared.db import get_db_session_for_tenant  # noqa: PLC0415
        from shared.models.orm import Project  # noqa: PLC0415
        from shared.services.budget_store import read_scope_spend  # noqa: PLC0415

        async with get_db_session_for_tenant(tenant_id) as session:
            budget = (await session.execute(
                select(Project.monthly_budget_usd).where(Project.id == project_id)
            )).scalar_one_or_none()
        spent = await read_scope_spend("project", str(project_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("budget_status failed: %s", type(exc).__name__)
        return f"Could not read the budget ({type(exc).__name__})."

    budget = float(budget) if budget is not None else None
    spent = round(float(spent or 0), 4)
    note = (
        "This is the LLM budget for running the agents, not a labour budget. It is a "
        "lifetime total, not a monthly allowance."
    )
    if budget is None:
        note += " No cap is set on this project, so nothing limits spend here."

    return json.dumps({
        "kind": "llm_spend",
        "budget_usd": budget,
        "spent_usd": spent,
        "remaining_usd": round(budget - spent, 4) if budget is not None else None,
        "note": note,
    }, indent=2, default=str)


@tool
async def cost_plan(schedule_json: str, rates_json: str) -> str:
    """Cost planned effort at rates the USER supplies.

    `rates_json` [{"name": "Ana", "rate": 85}] or [{"role": "developer", "rate": 70}],
    plus optionally {"default": 75}.

    THE PLATFORM STORES NO LABOUR RATE, and this will not invent one. Effort with no
    matching rate is reported as uncosted rather than charged at a guess — a total built
    on an assumed day rate is a number somebody will put in front of a client.

    The result is a labour cost. Keep it separate from `budget_status`, which is LLM
    spend; adding them together compares two different things.
    """
    try:
        schedule = _as_list(schedule_json, "schedule") or []
        rates_raw = _as_list(rates_json, "rates") or []
    except ValueError as exc:
        return f"Could not cost the plan: {exc}"

    if not schedule:
        return "No schedule was given, so there is nothing to cost."
    if not rates_raw:
        return (
            "No rates were given. The platform stores no labour rate, so tell me what "
            "each person or role costs per unit of effort and I will apply it."
        )

    by_key: dict = {}
    default: Optional[float] = None
    for row in rates_raw:
        if not isinstance(row, dict):
            continue
        if "default" in row:
            try:
                default = float(row["default"])
            except (TypeError, ValueError):
                pass
        key = row.get("name") or row.get("role")
        if key is None:
            continue
        try:
            by_key[str(key).lower()] = float(row.get("rate"))
        except (TypeError, ValueError):
            continue

    total = 0.0
    uncosted_effort = 0.0
    uncosted: set = set()
    per_sprint = []
    for slot in schedule:
        sprint_cost = 0.0
        for task in slot.get("items", []):
            try:
                effort = float(task.get("estimate") or 0)
            except (TypeError, ValueError):
                effort = 0.0
            who = str(task.get("assigned_to") or task.get("role") or "").lower()
            rate = by_key.get(who, default)
            if rate is None:
                uncosted_effort += effort
                if who:
                    uncosted.add(task.get("assigned_to") or task.get("role"))
                continue
            sprint_cost += effort * rate
        total += sprint_cost
        per_sprint.append({"sprint": slot.get("name") or slot.get("id"), "cost": round(sprint_cost, 2)})

    notes = []
    if uncosted_effort:
        detail = f" (no rate for: {', '.join(sorted(map(str, uncosted)))})" if uncosted else ""
        notes.append(
            f"{uncosted_effort} units of effort had no matching rate and are NOT in this "
            f"total{detail}. Supply a rate or a default to include them."
        )
    notes.append("Labour cost. Not comparable with budget_status, which is LLM spend.")

    return json.dumps({
        "kind": "labour_cost",
        "total": round(total, 2),
        "per_sprint": per_sprint,
        "uncosted_effort": uncosted_effort,
        "notes": notes,
    }, indent=2, default=str)


# ── Tools ────────────────────────────────────────────────────────────────────

_SHARED_TOOLS: List[Any] = []
try:  # pragma: no cover - import guard only
    from agents_orchestrator.design_architecture_agent.agents.architecture import (
        export_document,
        generate_diagram,
    )

    _SHARED_TOOLS = [export_document, generate_diagram]
except Exception:  # noqa: BLE001
    logger.warning("PM agent: shared document tools unavailable")

tools = [
    read_project_inputs,
    list_sprints,
    read_team_capacity,
    build_schedule,
    allocate_resources,
    track_risks,
    status_report,
    replan,
    budget_status,
    cost_plan,
    save_plan,
    *_SHARED_TOOLS,
]


PM_SYS_MESSAGE = """\
You are the Project Manager Agent. You turn an accepted design into a plan: a work
breakdown, estimates, and the risks that threaten them.

── ACT, DON'T NARRATE (CRITICAL) ─────────────────────────────────────────────
When the user asks you to break work down, estimate, or save, emit the tool call in the
SAME response. A turn that announces an action without calling the tool is a failure.

THE USER ASKING IS THE TRIGGER. If they greet you or ask a question, reply to what they
said. Do not start planning because context exists.

── READ BEFORE YOU PLAN ──────────────────────────────────────────────────────
Call `read_project_inputs` when the user asks you to plan THIS PROJECT, or refers to
"the requirements", "the design", "the backlog". Planning from memory when stored
requirements exist produces a plan for a system nobody asked for.

Do NOT call it when the user describes the work themselves — their words are the input
then.

── WHAT YOU DO ───────────────────────────────────────────────────────────────
WORK BREAKDOWN, ESTIMATION, SCHEDULING, RESOURCE PLANNING, RISK TRACKING, STATUS
REPORTING, RE-PLANNING and COSTING. You turn the design's components into tasks traced
back to the requirements that motivated them, size them, sequence them into sprints,
assign them against real capacity, track what threatens them, report where the work
actually is, and re-sequence when things change.

── THE ARITHMETIC IS NOT YOURS TO DO (CRITICAL) ──────────────────────────────
Call `build_schedule`, `allocate_resources`, `track_risks`, `status_report`, `replan`
and `cost_plan`. Do NOT work out the packing, the capacity sums, who is over-allocated,
a velocity, a forecast or a cost yourself and describe the result.

A schedule you compute in your head LOOKS right — plausible sprint names, confident
dates — and is wrong in ways nobody can see without redoing the arithmetic. The tools
do the ordering and the sums; your job is to supply the inputs and explain what comes
back.

RELAY `unscheduled`, `over_allocated` AND EVERY `notes` ENTRY VERBATIM. They are the
decisions only a person can make: an item with no estimate, an item too big for any
sprint, a dependency cycle, somebody committed past their capacity. A schedule presented
as complete while these are hidden is worse than no schedule.

── A NULL IS AN ANSWER, NOT A GAP TO FILL ────────────────────────────────────
`status_report` returns null with a REASON where a number would be a guess: a velocity
from one sprint, a forecast with nothing completed yet, slippage with no baseline.

Say the reason. Do NOT estimate the missing figure yourself — each of those has a
plausible wrong answer that reads as fact and gets planned around. "We cannot forecast
from one sprint" is a useful thing to tell a manager; an invented date is not.

LABEL ANYTHING YOU SUPPLIED YOURSELF. When you invent sprint boundaries, dates or a
capacity because the user did not give them, say so — "assuming two 2-week sprints
starting Monday" — and say it in the same breath as the schedule. Presenting an
assumption alongside real tool output, in the same list and the same tone, is how a
reader takes a date you chose for one the plan is committed to.

Watch for `unrecognised_states` in particular: if a board's final column is called
something this does not know, finished work is counted as outstanding and the project
looks stalled when it is not. Ask which states mean done.

── ESTIMATING HONESTLY ───────────────────────────────────────────────────────
- Say what each estimate assumes. An unexplained number cannot be challenged.
- An item you cannot size confidently should be SPLIT, not guessed at. Say why.
- NEVER invent an estimate for an item you have no information about. "Needs sizing with
  the team" is a useful answer; a fabricated 5 is not.
- If the board has estimates already, use them and say so rather than replacing them.

── CAPACITY IS NOT ALWAYS AVAILABLE ──────────────────────────────────────────
Azure DevOps exposes team capacity. Jira has NO capacity API — it lives in a plugin or
in calendars. On Jira, ask the user for the team's availability rather than assuming a
full sprint each. Never present an assumed capacity as if it were read from the board.

── WHEN SOMETHING CHANGES ────────────────────────────────────────────────────
Call `replan`, and REPORT THE DELTA — what moved sprint, what fell out, how much the
total grew. "What does this change cost us" is the question actually being asked, and
handing back a fresh schedule for the user to diff against the old one is how a re-plan
gets waved through without anyone seeing what it did.

`changes_rejected` names operations on work that is not in the plan. Those changes were
NOT applied. Say so — a user who believes a rejected change took effect is planning
against something that does not exist.

── TWO BUDGETS, NEVER ADDED TOGETHER ─────────────────────────────────────────
`budget_status` reports the LLM budget: what running the agents costs, metered against a
per-project cap. `cost_plan` reports LABOUR cost from rates the user supplies.

They measure different things. Do NOT sum them, compare them, or answer "can we afford
this plan" from the LLM budget — it knows nothing about what the team costs.

The platform stores NO labour rate. If the user wants a plan costed, ask what people or
roles cost; do not assume a day rate. Effort with no matching rate comes back as
uncosted, and that is the honest answer — a total built on an invented rate is a number
somebody will put in front of a client.

── SAVING ────────────────────────────────────────────────────────────────────
Call `save_plan` when the user is satisfied. The plan is recorded as AWAITING APPROVAL:
a project admin decides whether it becomes the project's committed plan. Tell the user
that, and do NOT say it has been "committed" or "approved" — that is not yours to say.

The FIRST save sets the baseline and later saves do not move it, so slippage stays
measurable against what was originally agreed.
"""

PM_SYS_MESSAGE = PM_SYS_MESSAGE + MCP_TOOLS_PROMPT_NOTE


# ── Graph ────────────────────────────────────────────────────────────────────


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    tenant_id: str
    model_id: str | None
    offering_id: str | None
    resolved_model: Any


_ORCHESTRATOR_CACHE: dict[tuple, object] = {}


def _build_orchestrator(model: str, litellm_provider: str, api_key: str,
                        base_url: str | None, alias: str) -> object:
    """Build (or return cached) ChatLiteLLM orchestrator. Keyed by (alias, model)."""
    # The credential is part of what this instance IS, so it belongs in the key —
    # the alias alone is stable across a key rotation and kept handing back a
    # client built with the old secret for the life of the process. See
    # shared/services/model_resolver.credential_fingerprint.
    from shared.services.model_resolver import credential_fingerprint  # noqa: PLC0415
    cache_key = (alias, model, credential_fingerprint(api_key, base_url), base_url or "")
    if cache_key in _ORCHESTRATOR_CACHE:
        return _ORCHESTRATOR_CACHE[cache_key]
    from langchain_litellm import ChatLiteLLM  # noqa: PLC0415

    from shared.services.model_resolver import temperature_kwargs  # noqa: PLC0415

    instance = ChatLiteLLM(
        model=model,
        custom_llm_provider=litellm_provider,
        api_base=base_url,
        api_key=api_key,
        max_tokens=8192,
        **temperature_kwargs(model, 0.2),
        # 0, not the library default: guarded_completion already retries the whole
        # call, and ChatLiteLLM's own tenacity layer underneath it retries again,
        # uncoordinated — the double-retry that amplified Azure rate limits.
        max_retries=0,
        streaming=True,
    )
    _ORCHESTRATOR_CACHE[cache_key] = instance
    return instance


async def agent(state: AgentState):
    from shared.services.model_resolver import (  # noqa: PLC0415
        ModelNotEnabledError, NoModelConfiguredError, resolve_model_for_run,
        set_resolved_model,
    )

    tenant_id = state.get("tenant_id", "")
    try:
        resolved = await resolve_model_for_run(
            tenant_id, state.get("model_id"), offering_id=state.get("offering_id")
        )
    except (NoModelConfiguredError, ModelNotEnabledError) as e:
        logger.warning("PM agent model resolution failed (tenant=%s): %s", tenant_id, type(e).__name__)
        return {"messages": [AIMessage(content=(
            "No usable model is configured for your organization. An administrator must "
            "add and verify a model provider in Org Settings → Model Providers."))]}
    except Exception as e:  # noqa: BLE001
        logger.error("PM agent model resolution error (tenant=%s): %s", tenant_id, type(e).__name__)
        from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

        return {"messages": [AIMessage(content=friendly_model_error(e))]}

    set_resolved_model(resolved)
    try:
        from shared.services.message_pairing import sanitize_tool_call_pairing  # noqa: PLC0415
        from shared.services.model_call_wrapper import guarded_completion  # noqa: PLC0415

        clean = sanitize_tool_call_pairing(state["messages"])
        orch = _build_orchestrator(
            resolved.model, resolved.litellm_provider, resolved.api_key,
            resolved.base_url, resolved.alias,
        )
        # Bound HERE, not in the cached builder, so per-run MCP tools never leak across
        # runs through the shared orchestrator cache.
        orch = orch.bind_tools(tools + get_mcp_tools())
        response = await guarded_completion(
            resolved, orch, clean, tenant_id=tenant_id, agent_type="plan",
            config={"metadata": {"user_api_key_alias": resolved.alias}},
        )
        return {"messages": [response], "resolved_model": resolved}
    except Exception as e:  # noqa: BLE001
        # NEVER str(exc): a BYOK provider error can echo the tenant's own API key.
        logger.error("PM agent error (tenant=%s alias=%s): %s", tenant_id, resolved.alias, type(e).__name__)
        from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

        return {"messages": [AIMessage(content=friendly_model_error(e))]}


async def action(state: AgentState):
    last = state["messages"][-1]
    results = []
    available = {t.name: t for t in tools + get_mcp_tools()}
    for tc in getattr(last, "tool_calls", []) or []:
        try:
            fn = available.get(tc["name"])
            obs = f"Unknown tool: {tc['name']}" if fn is None else await fn.ainvoke(tc["args"])
        except Exception as e:  # noqa: BLE001
            import traceback  # noqa: PLC0415

            # The real error goes to the log; the chat gets a short one so a large
            # exception cannot flood the transcript as message content.
            logger.error("PM tool %s failed: %s\n%s", tc["name"], type(e).__name__, traceback.format_exc())
            short = str(e)
            if len(short) > 400:
                short = short[:400] + "… (truncated — see server logs)"
            obs = f"Tool '{tc['name']}' failed: {type(e).__name__}: {short}"
        results.append(ToolMessage(content=str(obs), tool_call_id=tc["id"], name=tc["name"]))
    return {"messages": results}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "action" if getattr(last, "tool_calls", None) else False


workflow = StateGraph(AgentState)
workflow.add_node("agent", agent)
workflow.add_node("tools", action)
workflow.add_edge("tools", "agent")
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"action": "tools", False: END})

app = workflow.compile(checkpointer=_build_checkpointer("plan"))
