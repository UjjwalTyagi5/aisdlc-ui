"""Risk exposure and progress reporting — the parts a manager acts on.

Same rule as `scheduling`: the arithmetic is here, not in the prompt. "Are we on track"
is a question with a numeric answer, and a model that estimates it produces a confident
sentence nobody can check.

THE HARD PART IS NOT THE MATHS, IT IS KNOWING WHEN NOT TO ANSWER. Four cases return an
explicit "cannot say" instead of a number, because each has a plausible wrong answer
that reads as fact:

  - velocity from a single sprint. One data point is not a trend, and forecasting from
    it produces a date somebody will plan around.
  - a forecast when velocity is zero. Dividing by it is a crash; reporting "0 sprints"
    is worse.
  - slippage with no baseline. Comparing the plan to itself always reports zero slip.
  - completion when the board uses a state this does not recognise. Treating an
    unrecognised state as "not done" silently understates progress, so the unknown
    states are named instead.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

#: States that mean the work is finished, lowercased. Deliberately a starting point,
#: not an authority — a board can call it anything, and `summarise_progress` reports
#: every state it did not recognise rather than assuming those are unfinished.
DEFAULT_DONE_STATES = frozenset({
    "done", "closed", "resolved", "completed", "complete", "shipped", "released",
    "accepted", "verified",
})

#: The minimum completed sprints before an average deserves the word "velocity".
MIN_SPRINTS_FOR_VELOCITY = 2


def _estimate_of(item: Dict[str, Any]) -> float:
    """Size, treating unknown as 0 for SUMS ONLY.

    Safe here in a way it is not in `scheduling`: a total is reported alongside a count
    of unestimated items, so nothing is hidden. Scheduling refuses instead, because
    there a default silently invents scope.
    """
    raw = item.get("estimate")
    if raw in (None, ""):
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _is_done(item: Dict[str, Any], done_states: Iterable[str]) -> bool:
    return str(item.get("state") or "").strip().lower() in set(done_states)


def summarise_progress(
    schedule: Sequence[Dict[str, Any]],
    board_items: Optional[Sequence[Dict[str, Any]]] = None,
    done_states: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Committed, completed and remaining work per sprint and overall.

    Completion comes from the BOARD's state, not from the plan — a plan does not know
    what got finished, and asking the user is not a substitute for reading it.

    `unrecognised_states` lists every state that was neither recognised as done nor
    obviously in progress. A board calling its final column "Shipped to prod" would
    otherwise report every finished item as outstanding, and the plan would look stalled
    while the team was done.
    """
    states = set(done_states) if done_states else set(DEFAULT_DONE_STATES)
    by_key: Dict[str, Dict[str, Any]] = {}
    for item in board_items or []:
        for key in (item.get("id"), item.get("source_key"), item.get("title")):
            if key:
                by_key[str(key)] = item

    sprints: List[Dict[str, Any]] = []
    unknown_states: set = set()
    total_committed = total_done = 0.0
    unestimated = 0

    for slot in schedule:
        committed = done = 0.0
        items_done = 0
        for task in slot.get("items", []):
            estimate = _estimate_of(task)
            committed += estimate
            if task.get("estimate") in (None, ""):
                unestimated += 1

            live = None
            for key in (task.get("id"), task.get("source_key"), task.get("title")):
                if key and str(key) in by_key:
                    live = by_key[str(key)]
                    break
            state = str((live or task).get("state") or "").strip()
            if state and state.lower() not in states:
                unknown_states.add(state)
            if _is_done(live or task, states):
                done += estimate
                items_done += 1

        sprints.append({
            "name": slot.get("name") or slot.get("id"),
            "committed": round(committed, 2),
            "completed": round(done, 2),
            "remaining": round(committed - done, 2),
            "items": len(slot.get("items", [])),
            "items_completed": items_done,
        })
        total_committed += committed
        total_done += done

    notes: List[str] = []
    if unknown_states:
        notes.append(
            "These board states were not recognised as finished, so their work counts as "
            "outstanding: " + ", ".join(sorted(unknown_states))
            + ". Say which of them mean done if that is wrong."
        )
    if unestimated:
        notes.append(
            f"{unestimated} scheduled item(s) have no estimate and contribute 0 to these "
            "totals, so the real remaining work is higher."
        )
    if not board_items:
        notes.append(
            "No board items were supplied, so completion was read from the plan itself "
            "rather than from what the team has actually finished."
        )

    return {
        "sprints": sprints,
        "total_committed": round(total_committed, 2),
        "total_completed": round(total_done, 2),
        "total_remaining": round(total_committed - total_done, 2),
        "unrecognised_states": sorted(unknown_states),
        "notes": notes,
    }


def velocity_from(sprints: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Average completed work per FINISHED sprint, or an explicit refusal.

    Only sprints with work committed to them count: an empty sprint would drag the
    average toward zero and understate what the team does.

    ONE SPRINT IS NOT A VELOCITY. Averaging a single number produces something that
    looks like a trend and will be planned around, so below MIN_SPRINTS_FOR_VELOCITY
    this returns `velocity: None` with the reason.
    """
    completed = [
        float(s.get("completed") or 0)
        for s in sprints
        if float(s.get("committed") or 0) > 0
    ]
    if len(completed) < MIN_SPRINTS_FOR_VELOCITY:
        return {
            "velocity": None,
            "sprints_measured": len(completed),
            "reason": (
                f"only {len(completed)} sprint(s) with committed work — "
                f"{MIN_SPRINTS_FOR_VELOCITY} are needed before an average means anything"
            ),
        }

    mean = sum(completed) / len(completed)
    spread = max(completed) - min(completed)
    out: Dict[str, Any] = {
        "velocity": round(mean, 2),
        "sprints_measured": len(completed),
        "range": [round(min(completed), 2), round(max(completed), 2)],
    }
    # A mean over wildly different sprints is arithmetically fine and practically
    # misleading; saying so is cheaper than a forecast nobody can rely on.
    if mean > 0 and spread > mean:
        out["reason"] = (
            "sprint-to-sprint variation is larger than the average itself, so treat any "
            "forecast built on this as a wide range rather than a date"
        )
    return out


def forecast_completion(remaining: float, velocity: Optional[float]) -> Dict[str, Any]:
    """How many sprints the remaining work needs, or why that cannot be said."""
    if velocity is None:
        return {"sprints_needed": None, "reason": "no velocity established yet"}
    if velocity <= 0:
        # Not a division-by-zero guard for its own sake: "0 sprints" would read as
        # "done", which is the opposite of what a zero velocity means.
        return {
            "sprints_needed": None,
            "reason": "nothing has been completed, so there is no rate to project from",
        }
    if remaining <= 0:
        return {"sprints_needed": 0, "reason": "no work remaining"}
    import math

    return {"sprints_needed": math.ceil(remaining / velocity)}


def compare_to_baseline(
    schedule: Sequence[Dict[str, Any]],
    baseline: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """What moved since the plan was first committed to.

    NO BASELINE MEANS NO ANSWER. Comparing the current plan to itself always reports
    zero slip, which is the most confidently wrong number this module could produce.
    """
    if not baseline or not baseline.get("schedule"):
        return {
            "comparable": False,
            "reason": "no baseline was recorded, so there is nothing to compare against",
        }

    def _index(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
        return {
            str(r.get("name") or r.get("id")): float(r.get("committed") or 0)
            for r in rows
        }

    now, then = _index(schedule), _index(baseline["schedule"])
    moved = [
        {"sprint": name, "baseline": then.get(name, 0.0), "now": now.get(name, 0.0),
         "change": round(now.get(name, 0.0) - then.get(name, 0.0), 2)}
        for name in sorted(set(now) | set(then))
        if round(now.get(name, 0.0) - then.get(name, 0.0), 2) != 0
    ]
    return {
        "comparable": True,
        "baseline_total": round(sum(then.values()), 2),
        "current_total": round(sum(now.values()), 2),
        "change": round(sum(now.values()) - sum(then.values()), 2),
        "sprints_changed": moved,
    }


def assess_risks(
    risks: Sequence[Dict[str, Any]],
    schedule: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Attach each risk to the work it threatens and total the exposure.

    A risk with no linked work is kept, not dropped — "the vendor contract is unsigned"
    threatens the project without pointing at a task id, and a register that silently
    discards those is one people stop filling in.

    `unknown_links` names ids a risk points at that are not in the plan. Those are the
    interesting ones: usually a typo, sometimes a dependency on work nobody scheduled.
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for slot in schedule or []:
        for task in slot.get("items", []):
            for key in (task.get("id"), task.get("title")):
                if key:
                    by_id[str(key)] = {"task": task, "sprint": slot.get("name") or slot.get("id")}

    assessed: List[Dict[str, Any]] = []
    unknown_links: set = set()
    total_exposure = 0.0

    for risk in risks:
        raw = risk.get("threatens") or risk.get("blocks") or risk.get("task_ids") or []
        if isinstance(raw, (str, int)):
            raw = [raw]

        linked, exposure = [], 0.0
        for ref in raw:
            hit = by_id.get(str(ref))
            if hit is None:
                unknown_links.add(str(ref))
                continue
            exposure += _estimate_of(hit["task"])
            linked.append({
                "task": hit["task"].get("title") or hit["task"].get("id"),
                "sprint": hit["sprint"],
            })

        total_exposure += exposure
        assessed.append({
            **{k: v for k, v in risk.items() if k not in ("threatens", "blocks", "task_ids")},
            "threatens": linked,
            "exposure": round(exposure, 2),
            "unlinked": not linked,
        })

    notes: List[str] = []
    if unknown_links:
        notes.append(
            "These risks point at work that is not in the plan: "
            + ", ".join(sorted(unknown_links))
            + " — either a typo, or work nobody has scheduled."
        )
    unlinked = sum(1 for r in assessed if r["unlinked"])
    if unlinked:
        notes.append(
            f"{unlinked} risk(s) name no specific work. They are kept, but their exposure "
            "cannot be quantified."
        )

    return {
        "risks": assessed,
        "total_exposure": round(total_exposure, 2),
        "notes": notes,
    }
