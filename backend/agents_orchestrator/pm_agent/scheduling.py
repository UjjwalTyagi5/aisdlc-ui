"""Sequencing and resource levelling — the arithmetic, kept out of the model.

WHY THIS IS CODE AND NOT A PROMPT. Everything here is exactly what a language model does
badly and a function does reliably: topological ordering, adding hours against a
capacity ceiling, spotting that one person is booked for 60 hours in a 40-hour sprint. A
model asked to "schedule these" produces something that LOOKS like a plan — plausible
sprint names, confident dates — and is wrong in ways nobody can see without redoing the
arithmetic. The model's job is to supply the inputs and explain the output; the numbers
are computed here.

WHAT IT REFUSES TO GUESS. Three situations have no correct answer and are reported
rather than papered over:

  - an item with no estimate. Assuming a default silently invents scope.
  - an item larger than a whole sprint. It can never fit; splitting it is a decision.
  - a dependency cycle. There is no valid order, and a scheduler that quietly drops one
    edge produces a plan that cannot be executed.

Every one of them ends up in `unscheduled` with a reason, because a plan that is honest
about what it could not place is more useful than one that placed everything.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: A working week. Used only when a sprint gives dates but no capacity — the fallback is
#: stated rather than hidden, because a schedule built on an assumed team size should be
#: recognisable as one.
WORKING_DAYS_PER_WEEK = 5


def _estimate_of(task: Dict[str, Any]) -> Optional[float]:
    """The task's size, or None. NEVER a default.

    An unestimated item and a zero-point item are different facts. Substituting a
    default here would let an unsized backlog produce a confident schedule."""
    raw = task.get("estimate")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def order_by_dependencies(
    tasks: Sequence[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Topologically order tasks, returning (ordered, cyclic).

    Kahn's algorithm. Anything left when no node has zero in-degree is in a cycle, and
    is returned separately rather than appended in arbitrary order — a plan whose order
    is arbitrary in one place cannot be trusted anywhere.

    A dependency on an id that is not in the set is IGNORED rather than treated as
    unmet: it usually means the prerequisite is already done or lives on another team's
    board, and blocking the whole schedule on it would be wrong.
    """
    by_id = {str(t.get("id") or t.get("title") or i): t for i, t in enumerate(tasks)}
    known = set(by_id)

    deps: Dict[str, set] = {}
    for tid, task in by_id.items():
        raw = task.get("depends_on") or task.get("dependencies") or []
        if isinstance(raw, (str, int)):
            raw = [raw]
        deps[tid] = {str(d) for d in raw if str(d) in known and str(d) != tid}

    ordered: List[Dict[str, Any]] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(tid for tid, d in remaining.items() if not d)
        if not ready:
            break                      # everything left is in a cycle
        for tid in ready:
            ordered.append(by_id[tid])
            remaining.pop(tid)
        for d in remaining.values():
            d.difference_update(ready)

    return ordered, [by_id[tid] for tid in sorted(remaining)]


def sprint_capacity(sprint: Dict[str, Any], team: Optional[Sequence[Dict[str, Any]]] = None) -> Optional[float]:
    """Hours available in a sprint.

    Prefers a capacity the caller supplied, then the team's real per-person capacity
    (hours a day x working days, minus days off), and finally None — NOT a guess. A
    schedule built against an invented ceiling is worse than one that says it does not
    know the ceiling.
    """
    explicit = sprint.get("capacity")
    if explicit not in (None, ""):
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass

    if not team:
        return None

    days = _working_days(sprint)
    if days is None:
        return None

    total = 0.0
    for member in team:
        try:
            per_day = float(member.get("capacity_per_day") or 0)
        except (TypeError, ValueError):
            per_day = 0.0
        try:
            off = float(member.get("days_off") or 0)
        except (TypeError, ValueError):
            off = 0.0
        # Days off can exceed the sprint if the data is odd; a negative contribution
        # would silently give capacity back to the team.
        total += per_day * max(0.0, days - off)
    return total or None


def _working_days(sprint: Dict[str, Any]) -> Optional[float]:
    """Working days between a sprint's dates, or None when it has none."""
    from datetime import datetime

    start, finish = sprint.get("start_date"), sprint.get("finish_date")
    if not start or not finish:
        return None
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00")).date()
        f = datetime.fromisoformat(str(finish).replace("Z", "+00:00")).date()
    except ValueError:
        return None
    if f < s:
        return None
    calendar_days = (f - s).days + 1          # both ends inclusive
    return calendar_days * WORKING_DAYS_PER_WEEK / 7.0


def build_schedule(
    tasks: Sequence[Dict[str, Any]],
    sprints: Sequence[Dict[str, Any]],
    team: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Pack ordered tasks into sprints, respecting dependencies and capacity.

    A task goes into the FIRST sprint that has room and that starts no earlier than
    every prerequisite's sprint — otherwise a dependent could be committed alongside
    work it needs to have finished.

    Returns `{"schedule": [...], "unscheduled": [...], "notes": [...]}`. Nothing is
    silently dropped: every task appears in one list or the other, and each unscheduled
    entry carries the reason.
    """
    ordered, cyclic = order_by_dependencies(tasks)

    plan: List[Dict[str, Any]] = []
    for sprint in sprints:
        cap = sprint_capacity(sprint, team)
        plan.append({
            "id": str(sprint.get("id") or ""),
            "name": sprint.get("name") or sprint.get("path") or "",
            "start_date": sprint.get("start_date", ""),
            "finish_date": sprint.get("finish_date", ""),
            "capacity": cap,
            "committed": 0.0,
            "items": [],
        })

    unscheduled: List[Dict[str, Any]] = []
    notes: List[str] = []
    placed_at: Dict[str, int] = {}

    for task in cyclic:
        unscheduled.append({
            "task": task,
            "reason": "in a dependency cycle — there is no valid order until it is broken",
        })
    if cyclic:
        notes.append(
            f"{len(cyclic)} task(s) form a dependency cycle and were not scheduled."
        )

    for task in ordered:
        tid = str(task.get("id") or task.get("title") or "")
        estimate = _estimate_of(task)
        if estimate is None:
            unscheduled.append({"task": task, "reason": "no estimate — size it before scheduling"})
            continue

        earliest = 0
        raw_deps = task.get("depends_on") or task.get("dependencies") or []
        if isinstance(raw_deps, (str, int)):
            raw_deps = [raw_deps]
        for dep in raw_deps:
            if str(dep) in placed_at:
                earliest = max(earliest, placed_at[str(dep)])
            elif any(str(dep) == str(u["task"].get("id") or u["task"].get("title")) for u in unscheduled):
                # A prerequisite that could not be scheduled blocks its dependents;
                # placing this anyway would produce a plan that cannot be executed.
                earliest = len(plan)

        for index in range(earliest, len(plan)):
            slot = plan[index]
            cap = slot["capacity"]
            if cap is not None and slot["committed"] + estimate > cap:
                continue
            slot["items"].append(task)
            slot["committed"] = round(slot["committed"] + estimate, 2)
            placed_at[tid] = index
            break
        else:
            biggest = max((s["capacity"] or 0) for s in plan) if plan else 0
            if plan and estimate > biggest:
                reason = (
                    f"larger than any sprint's capacity ({estimate} vs {biggest}) — "
                    "split it rather than stretching a sprint"
                )
            elif not plan:
                reason = "no sprints to schedule into"
            else:
                reason = "no sprint has room for it after its prerequisites"
            unscheduled.append({"task": task, "reason": reason})

    for slot in plan:
        if slot["capacity"] is None:
            notes.append(
                f"{slot['name'] or slot['id']}: no capacity known, so nothing limited "
                "what was committed to it."
            )

    return {"schedule": plan, "unscheduled": unscheduled, "notes": notes}


def allocate(
    schedule: Sequence[Dict[str, Any]],
    team: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assign each sprint's items to people, least-loaded first.

    OVER-ALLOCATION IS REPORTED, NOT PREVENTED. Refusing to assign the last task would
    leave a plan that looks complete while the work has nobody on it; assigning it
    silently would commit somebody to more hours than they have. Both are worse than
    saying who is over and by how much.

    Existing `assigned_to` on a task is respected — somebody already decided that, and
    a scheduler that reshuffles real assignments is one people stop trusting.
    """
    people = [
        {
            "name": m.get("name") or m.get("member_id") or "unassigned",
            "capacity": _member_hours(m),
            "assigned": 0.0,
            "items": [],
        }
        for m in team
    ]

    if not people:
        return {
            "assignments": [],
            "over_allocated": [],
            "notes": ["No team capacity is known, so nothing was assigned."],
        }

    by_name = {p["name"]: p for p in people}
    for slot in schedule:
        for task in slot.get("items", []):
            estimate = _estimate_of(task) or 0.0
            existing = task.get("assigned_to")
            person = by_name.get(existing) if existing else None
            if person is None:
                person = min(people, key=lambda p: (p["assigned"], p["name"]))
            person["assigned"] = round(person["assigned"] + estimate, 2)
            person["items"].append({
                "sprint": slot.get("name") or slot.get("id"),
                "task": task.get("title") or task.get("id"),
                "estimate": estimate,
                # Say when the assignment was already made, so a reader can tell what
                # this function decided from what it merely carried through.
                "preassigned": bool(existing),
            })

    over = [
        {
            "name": p["name"],
            "assigned": p["assigned"],
            "capacity": p["capacity"],
            "over_by": round(p["assigned"] - p["capacity"], 2),
        }
        for p in people
        if p["capacity"] is not None and p["assigned"] > p["capacity"]
    ]

    notes: List[str] = []
    unknown = [p["name"] for p in people if p["capacity"] is None]
    if unknown:
        notes.append(
            "No capacity known for " + ", ".join(sorted(unknown))
            + " — their load was not checked against anything."
        )
    if over:
        notes.append(
            f"{len(over)} person(s) are committed beyond their capacity; "
            "move work out or extend the sprint."
        )

    return {"assignments": people, "over_allocated": over, "notes": notes}


def _member_hours(member: Dict[str, Any]) -> Optional[float]:
    """A person's hours for the sprint, or None when the board did not say.

    None rather than 0: zero means "available for nothing", which would mark them
    over-allocated by their first task, and unknown means the plan cannot check.
    """
    explicit = member.get("hours")
    if explicit not in (None, ""):
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    per_day, days = member.get("capacity_per_day"), member.get("sprint_days")
    if per_day in (None, "") or days in (None, ""):
        return None
    try:
        off = float(member.get("days_off") or 0)
        return float(per_day) * max(0.0, float(days) - off)
    except (TypeError, ValueError):
        return None


def apply_changes(
    tasks: Sequence[Dict[str, Any]], changes: Sequence[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply add / remove / re-estimate operations to a task set.

    Returns (tasks, rejected) — a change naming work that is not there is REJECTED with
    a reason rather than silently creating it. "Re-estimate T7" when there is no T7 is
    usually a typo, and inventing T7 would put phantom work in the plan.
    """
    by_id = {str(t.get("id") or t.get("title")): dict(t) for t in tasks}
    rejected: List[str] = []

    for change in changes:
        op = str(change.get("op") or change.get("operation") or "").strip().lower()
        tid = str(change.get("id") or change.get("title") or "")

        if op in ("add", "added", "new"):
            if tid in by_id:
                rejected.append(f"add {tid}: already in the plan")
                continue
            by_id[tid] = {k: v for k, v in change.items() if k not in ("op", "operation")}
            by_id[tid].setdefault("id", tid)
        elif op in ("remove", "removed", "delete", "drop"):
            if by_id.pop(tid, None) is None:
                rejected.append(f"remove {tid}: not in the plan")
        elif op in ("reestimate", "re-estimate", "resize", "update"):
            if tid not in by_id:
                rejected.append(f"{op} {tid}: not in the plan")
                continue
            for field in ("estimate", "depends_on", "title", "assigned_to"):
                if field in change:
                    by_id[tid][field] = change[field]
        else:
            rejected.append(f"{op or '(no op)'} {tid}: unknown operation")

    return list(by_id.values()), rejected


def tasks_from_schedule(schedule: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every task a schedule holds, in order — the input a re-plan starts from."""
    return [task for slot in schedule for task in slot.get("items", [])]


def replan(
    current: Sequence[Dict[str, Any]],
    changes: Sequence[Dict[str, Any]],
    sprints: Sequence[Dict[str, Any]],
    team: Optional[Sequence[Dict[str, Any]]] = None,
    baseline: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Re-sequence after a change and say WHAT MOVED.

    The new schedule alone is not the answer. A manager asking "what does this change
    cost us" needs the delta: which work slipped a sprint, what fell out entirely, how
    much the total grew. Handing back a fresh plan and leaving them to diff two lists is
    how a re-plan gets rubber-stamped without anyone seeing what it did.

    `baseline` is compared against separately when given, because drift from the
    original commitment and drift since last week are different questions.
    """
    before = {
        str(t.get("id") or t.get("title")): i
        for i, slot in enumerate(current)
        for t in slot.get("items", [])
    }
    tasks, rejected = apply_changes(tasks_from_schedule(current), changes)
    result = build_schedule(tasks, sprints, team)

    after = {
        str(t.get("id") or t.get("title")): i
        for i, slot in enumerate(result["schedule"])
        for t in slot.get("items", [])
    }
    names = [s.get("name") or s.get("id") or f"#{i}" for i, s in enumerate(result["schedule"])]

    def _name(index: Optional[int]) -> Optional[str]:
        return names[index] if index is not None and index < len(names) else None

    moved = [
        {"task": tid, "from": _name(before[tid]), "to": _name(after[tid])}
        for tid in sorted(set(before) & set(after))
        if before[tid] != after[tid]
    ]
    dropped_out = [
        {"task": tid, "was": _name(before[tid])}
        for tid in sorted(set(before) - set(after))
    ]
    newly_placed = sorted(set(after) - set(before))

    total_before = round(sum(float(s.get("committed") or 0) for s in current), 2)
    total_after = round(sum(s["committed"] for s in result["schedule"]), 2)

    summary: Dict[str, Any] = {
        **result,
        "changes_rejected": rejected,
        "moved": moved,
        "no_longer_scheduled": dropped_out,
        "newly_scheduled": newly_placed,
        "committed_before": total_before,
        "committed_after": total_after,
        "committed_change": round(total_after - total_before, 2),
    }
    if baseline:
        summary["vs_baseline"] = round(
            total_after - sum(float(s.get("committed") or 0) for s in baseline), 2
        )
    return summary
