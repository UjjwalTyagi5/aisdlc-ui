"""Who is on this project, what they may touch, and what they have actually done.

WHY THE PROJECT MANAGER NEEDS THIS AND COULD NOT GET IT. Every other question the PM
agent answers — sprints, capacity, work items — comes off the board. The people do not.
The board knows a display name on a ticket; it does not know that this person is the
project's Architect, that they were granted the Security agent last week, or that they
uploaded the design document waiting for approval. That lives in `role_bindings`,
`agent_access_overrides` and `audit_events`, and no tool reached any of it.

TWO SOURCES, DELIBERATELY KEPT APART IN THE OUTPUT.

  · The PLATFORM knows roles, agent access and every recorded action, and it is
    authoritative — it is the system doing the granting.
  · The BOARD knows assigned tickets, and it is the same board the rest of this agent
    already reads, through the same provider-agnostic connector. Jira and Azure DevOps
    both expose `assigned_to` on a canonical item, so this works on either without
    knowing which is connected.

Merging them into one number would invent a fact neither system holds. A person with no
platform activity and four tickets in progress is doing their job on the board; one with
no tickets and six approvals is doing it here. Reporting "activity: 6" for either is how
a summary becomes fiction.

THERE IS NO SKILLS TABLE, and this does not pretend otherwise. Asked "what are their
skills", the honest answer is built from two things this platform really knows: the ROLE
they hold, which is its own statement of what someone is here to do, and the WORK they
have actually done. Anything beyond that would be invention dressed as data, which on a
question about a colleague's competence is worse than no answer.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

#: How far back `member_activity` looks when the caller does not say.
_DEFAULT_DAYS = 7
#: Kept small: this is a summary for a person to read, not an export.
_MAX_EVENTS_PER_MEMBER = 12


def _context() -> tuple[str, str]:
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    return (get_tenant_id() or ""), (get_project_id() or "")


async def _members(tenant_id: str, project_id: str) -> list[dict[str, Any]]:
    """The project's roster, from the same `role_bindings` rows the Members page reads."""
    from sqlalchemy import text  # noqa: PLC0415

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as db:
        rows = (await db.execute(
            text(
                "SELECT rb.user_id, rb.role_name, rb.status, rb.extra_agents, "
                "       rb.created_at, u.email "
                "FROM role_bindings rb "
                "LEFT JOIN users u ON u.id = rb.user_id "
                "WHERE rb.scope_kind = 'project' AND rb.scope_id = CAST(:p AS uuid) "
                "ORDER BY rb.created_at ASC"
            ),
            {"p": project_id},
        )).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        # TOLERANT, NOT LOAD-BEARING. Through SQLAlchemy's asyncpg dialect `jsonb`
        # arrives already decoded as a list — verified against the live database — so
        # this branch does not fire on this path. It is kept because RAW asyncpg (used
        # elsewhere in this repo) hands the same column back as a string, and `list("[]")`
        # would then report every member as holding two agents named "[" and "]".
        extra = r.extra_agents
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except ValueError:
                extra = []
        out.append({
            "user_id": str(r.user_id),
            "name": r.email or str(r.user_id),
            "role": r.role_name,
            "status": r.status,
            "extra_agents": list(extra or []),
            "joined": r.created_at.date().isoformat() if r.created_at else "",
        })
    return out


def _agents_for(role: str, extra: list[str]) -> dict[str, list[str]]:
    """Which agents this role owns and which it may merely use.

    Read from `AGENT_DEFAULT_REACH`, the same table `check_agent_access` falls back to,
    rather than a second copy of the matrix — the two disagreeing is exactly the failure
    a person asking "what can they touch" would never spot.
    """
    from shared.authz.agent_access import AGENT_DEFAULT_REACH  # noqa: PLC0415

    owns, uses = [], []
    for agent, by_role in AGENT_DEFAULT_REACH.items():
        reach = by_role.get(role or "", "none")
        if reach in ("owner", "primary"):
            owns.append(agent)
        elif reach != "none":
            uses.append(agent)
    granted = [a for a in extra if a not in owns and a not in uses]
    return {"owns": sorted(owns), "uses": sorted(uses), "granted": sorted(granted)}


async def _platform_activity(
    tenant_id: str, project_id: str, user_ids: list[str], days: int,
) -> dict[str, list[dict[str, str]]]:
    """Recorded actions per person, from the append-only audit trail.

    Scoped to this project through the payload, because `audit_events` is tenant-wide:
    without it a Project Manager asking about their own team would be shown work done
    somewhere else entirely.
    """
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    from sqlalchemy import text  # noqa: PLC0415

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    if not user_ids:
        return {}
    since = datetime.now(tz=timezone.utc) - timedelta(days=max(1, days))

    async with get_db_session_for_tenant(tenant_id) as db:
        rows = (await db.execute(
            text(
                "SELECT actor_id, event_type, resource_type, created_at, payload "
                "FROM audit_events "
                "WHERE actor_id = ANY(CAST(:u AS text[])) AND created_at >= :since "
                "ORDER BY created_at DESC"
            ),
            {"u": user_ids, "since": since},
        )).fetchall()

    by_user: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        payload = r.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}
        if isinstance(payload, dict):
            row_project = str(payload.get("project_id") or "")
            # Keep events that name THIS project, and events that name no project at all
            # (a run decision, say) — dropping the latter would silently hide most of
            # what an approver does.
            if row_project and row_project != project_id:
                continue
        bucket = by_user.setdefault(str(r.actor_id), [])
        if len(bucket) < _MAX_EVENTS_PER_MEMBER:
            bucket.append({
                "what": r.event_type,
                "on": r.resource_type or "",
                "when": r.created_at.isoformat() if r.created_at else "",
            })
    return by_user


async def _board_items_by_assignee() -> tuple[dict[str, list[dict[str, str]]], str]:
    """Open board work per assignee, or ({}, reason) when no board answers.

    Uses the agent's own board connector, so whichever provider the project has —
    Jira, Azure DevOps — is the one read. Both expose `assigned_to` on a canonical
    item, which is why this needs no per-provider branch.
    """
    try:
        from agents_orchestrator.pm_agent.agents.schedule import (  # noqa: PLC0415
            _board_project,
        )
        from agents_orchestrator.requirements_agent.agents.planning import (  # noqa: PLC0415
            _board_connector, _board_error,
        )
    except Exception as exc:  # noqa: BLE001
        return {}, f"the board tools are unavailable ({type(exc).__name__})"

    connector, err = await _board_connector(mode="read")
    if err:
        return {}, err
    try:
        items = await connector.read_adapter("list_all_items", project=await _board_project())
    except Exception as exc:  # noqa: BLE001
        return {}, _board_error(exc)

    by_person: dict[str, list[dict[str, str]]] = {}
    for it in items or []:
        who = (it.get("assigned_to") or "").strip()
        if not who:
            continue
        by_person.setdefault(who, []).append({
            "key": it.get("source_key") or str(it.get("id") or ""),
            "title": it.get("title") or "",
            "state": it.get("state") or "",
            "type": it.get("work_item_type") or "",
        })
    return by_person, ""


def _matches(member: dict[str, Any], who: str) -> bool:
    """Is this the person the caller named? Email, local part, or user id.

    People are named in conversation the way they are addressed — "ana", not
    "ana@abcbank.com" and certainly not a UUID — so an exact-email match would refuse
    almost every real question.
    """
    needle = who.strip().lower()
    if not needle:
        return True
    name = (member.get("name") or "").lower()
    return (
        needle == name
        or needle == name.split("@", 1)[0]
        or needle == str(member.get("user_id", "")).lower()
        or needle in name
    )


def make_team_tools(stage: str) -> list:
    """The two team tools, bound to the agent that registers them."""

    @tool
    async def list_project_members() -> str:
        """Who is on this project: their role, their standing, and which agents they may use.

        Use this before answering anything about the team — who does what, who could
        take a piece of work, who to route an approval to. The roster is the platform's
        own record, not a guess from the board.

        Returns a JSON array. `owns` are the agents whose gates that role approves;
        `uses` are the ones it may run but not sign off; `granted` are agents added for
        that person specifically, beyond their role's default.
        """
        tenant_id, project_id = _context()
        if not tenant_id or not project_id:
            return "ERROR: this conversation is not attached to a project."
        try:
            members = await _members(tenant_id, project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_project_members failed: %s", type(exc).__name__)
            return f"ERROR reading the project's members: {type(exc).__name__}"

        if not members:
            return (
                "This project has no members assigned yet. A Project Admin adds them on "
                "the project's Members page."
            )
        for m in members:
            m["agents"] = _agents_for(m["role"], m.pop("extra_agents", []))
        return json.dumps(members, indent=2, default=str)

    @tool
    async def member_activity(member: str = "", days: int = _DEFAULT_DAYS) -> str:
        """What the team has actually been doing — recently, or for one person.

        Answers "what did they do today", "how is everyone doing", "who has been quiet".
        Two sources are reported SEPARATELY and must stay that way in your answer:

          platform — runs, approvals, uploads and other recorded actions here. Complete
                     and authoritative; this system recorded them.
          board    — work items currently assigned to them on the connected Jira or
                     Azure DevOps board.

        Somebody with no platform activity and four tickets in progress is working on
        the board; do not report either number as "their activity". Say which is which.

        ON SKILLS AND PERFORMANCE, asked often and easy to answer badly: this platform
        holds no skills table and no performance rating. What it holds is the ROLE
        somebody was given and the WORK they have done. Answer from those two, name them
        as the basis, and say plainly that anything more is not recorded — do not infer
        competence from a count of events.

        Args:
            member: a name, email or the local part of one. Omit for the whole team.
            days:   how far back to look for platform activity. Defaults to a week.
        """
        tenant_id, project_id = _context()
        if not tenant_id or not project_id:
            return "ERROR: this conversation is not attached to a project."
        try:
            members = await _members(tenant_id, project_id)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading the project's members: {type(exc).__name__}"

        wanted = [m for m in members if _matches(m, member)]
        if not wanted:
            known = ", ".join(m["name"] for m in members) or "nobody"
            return (
                f"No member of this project matches {member!r}. On the project: {known}."
            )

        events = await _platform_activity(
            tenant_id, project_id, [m["user_id"] for m in wanted], days)
        board, board_note = await _board_items_by_assignee()

        report = []
        for m in wanted:
            mine = events.get(m["user_id"], [])
            # Matched on the display name the board carries, which is the only handle
            # the two systems share. An unmatched person is reported as unmatched rather
            # than as having no work — those are different facts.
            local = (m["name"] or "").split("@", 1)[0].lower()
            tickets = [
                t for who, ts in board.items()
                if local and (local in who.lower() or who.lower() in (m["name"] or "").lower())
                for t in ts
            ]
            report.append({
                "name": m["name"],
                "role": m["role"],
                "status": m["status"],
                "platform": {"days": days, "count": len(mine), "recent": mine},
                "board": (
                    {"assigned": tickets}
                    if not board_note
                    else {"unavailable": board_note}
                ),
            })
        return json.dumps(report, indent=2, default=str)

    return [list_project_members, member_activity]
