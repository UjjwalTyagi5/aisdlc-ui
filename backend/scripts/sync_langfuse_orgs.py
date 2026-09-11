"""Converge every business unit's Langfuse organization with this platform's RBAC.

WHY A RECONCILER EXISTS AT ALL. Every Langfuse call in the business-unit lifecycle is
fail-soft and backgrounded, because an unreachable Langfuse must never stop somebody
creating a unit or appointing its admin. That trade buys availability and pays for it in
drift: a unit created while Langfuse was down has no organization, and a unit admin
appointed in the same window holds no grant. This is what closes that gap.

It is also the BACKFILL. Units that predate migration 0058 have `langfuse_org_id = NULL`
and were never provisioned eagerly; running this once gives each of them its organization
and its access.

USAGE
    python -m scripts.sync_langfuse_orgs --dry-run     # report drift, change nothing
    python -m scripts.sync_langfuse_orgs               # converge it
    python -m scripts.sync_langfuse_orgs --unit <uuid> # one unit only

WHAT IT REPORTS AND WHY. Grants are split into members and PENDING INVITATIONS. An
invitation is a grant that has not taken effect: that person has never signed in to
Langfuse, so they hold no access today, and if they cannot authenticate against the
instance's Entra tenant at all they never will. Collapsing the two would hide precisely
the failure an operator needs to see — "I appointed them and they still cannot get in".
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Importable as a script from the backend root, like the other files in here.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sync_langfuse_orgs")


async def _units(session, tenant_id: str, only_unit: str | None):
    sql = (
        "select id, display_name, status, langfuse_org_id, langfuse_org_name "
        "from workspaces where organization_id = cast(:t as uuid)"
    )
    params: dict = {"t": tenant_id}
    if only_unit:
        sql += " and id = cast(:w as uuid)"
        params["w"] = only_unit
    sql += " order by display_name"
    return (await session.execute(text(sql), params)).all()


async def _tenants(session) -> list[str]:
    rows = await session.execute(text("select id from organizations order by created_at"))
    return [str(r[0]) for r in rows.all()]


async def _reconcile_projects(
    session, provisioner, org_sync, tenant_id: str, workspace_id: str, org_id: str,
    *, dry_run: bool,
) -> tuple[int, int]:
    """Check every live project in one unit. Returns (drifted, checked).

    A project is drifted when somebody who should hold a role does not, or somebody holds
    one nothing entitles them to. Two states are reported but NOT counted, because both
    are correct and re-running changes nothing:

      * `via org` — a unit admin reaches the project through their organization role and
        holds no project row. That is the design: a project row would REPLACE their
        organization role and cap them below what they have.
      * `invited` — a grant awaiting that person's first Langfuse sign-in.
    """
    rows = (
        await session.execute(
            text(
                "select p.id, p.display_name, b.langfuse_project_id "
                "from projects p "
                "left join langfuse_bindings b "
                "  on b.project_id = p.id and b.is_active = true "
                "where p.workspace_id = cast(:w as uuid) "
                "  and p.tenant_id = cast(:t as uuid) "
                "  and p.archived = false "
                "order by p.display_name"
            ),
            {"w": workspace_id, "t": tenant_id},
        )
    ).all()
    if not rows:
        return 0, 0

    drifted = 0
    for pid, pname, lf_project in rows:
        wanted = await org_sync.desired_project_access(
            session, tenant_id=tenant_id, project_id=str(pid)
        )
        label = f"project {pname!r}"

        if not lf_project:
            if wanted:
                drifted += 1
                print(f"    DRIFT {label}: no Langfuse project, {len(wanted)} grant(s) pending")
                if not dry_run:
                    await org_sync.sync_project(
                        session, tenant_id=tenant_id, project_id=str(pid)
                    )
            continue

        current = await provisioner.project_access(
            org_id=org_id, project_id=str(lf_project)
        )
        held, pending, via_org = current["members"], current["invitations"], current["via_org"]

        issues: list[str] = []
        notes: list[str] = []
        for email, role in wanted.items():
            if held.get(email) == role:
                continue
            if pending.get(email) == role:
                notes.append(f"{email} -> {role} invited, awaiting first sign-in")
                continue
            if email in via_org:
                notes.append(f"{email} reaches it via org {via_org[email]} (stronger)")
                continue
            issues.append(f"{email} should be {role}, is {held.get(email) or 'nothing'}")

        for email in set(held) | set(pending):
            if email not in wanted:
                issues.append(f"{email} holds a project role nothing entitles them to")

        if issues:
            drifted += 1
            print(f"    DRIFT {label}")
            for i in issues:
                print(f"          - {i}")
        elif notes:
            print(f"    ok {label}")
        for n in notes:
            print(f"          . {n}")

        if issues and not dry_run:
            rep = await org_sync.sync_project(
                session, tenant_id=tenant_id, project_id=str(pid)
            )
            if rep:
                print(f"          -> access={rep['access']}")
                if rep["revoked"]:
                    print(f"             revoked={rep['revoked']}")
    return drifted, len(rows)


async def run(*, dry_run: bool, only_unit: str | None) -> int:
    from config.env import ENABLE_LANGFUSE, LANGFUSE_MANAGE_MEMBERSHIPS
    from shared.db import get_db_session_for_tenant, get_db_session_superuser
    from shared.observability import org_sync
    from shared.observability.provisioning import (
        ORG_ROLE_OWNER,
        LangfuseProvisioner,
        strongest_role,
    )

    if not ENABLE_LANGFUSE:
        print("ENABLE_LANGFUSE is false — nothing to reconcile.")
        return 0
    if not LANGFUSE_MANAGE_MEMBERSHIPS and not dry_run:
        print(
            "LANGFUSE_MANAGE_MEMBERSHIPS is false — organizations will be provisioned but "
            "no access will be granted."
        )

    async with get_db_session_superuser() as s:
        tenant_ids = await _tenants(s)

    provisioner = LangfuseProvisioner()
    drift = 0
    total = 0
    projects_checked = 0

    for tenant_id in tenant_ids:
        # Tenant-scoped: `workspaces` is under FORCE RLS, so without the GUC this reads
        # zero rows and reports "no units" instead of failing.
        async with get_db_session_for_tenant(tenant_id) as session:
            units = await _units(session, tenant_id, only_unit)
            if not units:
                continue
            print(f"\n=== organization {tenant_id} — {len(units)} unit(s) ===")

            for uid, name, status, org_id, org_name in units:
                total += 1
                label = f"{name!r} ({uid})"

                if status == "archived":
                    print(f"  - {label}: archived, skipped")
                    continue

                wanted = await org_sync.desired_access(
                    session, tenant_id=tenant_id, workspace_id=str(uid)
                )
                issues: list[str] = []
                # Reported but NOT counted as drift. A pending invitation is the correct,
                # fully-applied state for somebody who has never signed in to Langfuse —
                # there is nothing to converge and re-running would change nothing. Counting
                # it would make --dry-run exit non-zero forever on any healthy deployment
                # whose admins have not happened to log in, which is a CI signal that means
                # nothing. It is still printed, because "I appointed them and they still
                # cannot get in" is exactly what an operator needs to see.
                pending_notes: list[str] = []

                if not org_id:
                    issues.append("no Langfuse organization")
                else:
                    current = await provisioner.org_access(org_id=str(org_id))
                    held = dict(current["members"])
                    pending = dict(current["invitations"])
                    bootstrap = {e.strip().lower() for e in provisioner._bootstrap_emails()}

                    # THE BOOTSTRAP FLOOR IS PART OF THE DESIRED STATE, not drift against
                    # it. A bootstrap address is always OWNER, so if that address is also
                    # this unit's admin the applied role is OWNER, not the ADMIN that
                    # `desired_access` alone implies. Comparing without this reported
                    # "should be ADMIN, is OWNER" on a perfectly converged unit — and
                    # worse, a non-dry run would have tried to "fix" it on every pass.
                    wanted = {
                        email: (
                            strongest_role(role, ORG_ROLE_OWNER)
                            if email in bootstrap else role
                        )
                        for email, role in wanted.items()
                    }
                    for email in bootstrap:
                        wanted.setdefault(email, ORG_ROLE_OWNER)

                    for email, role in wanted.items():
                        if held.get(email) == role:
                            continue
                        if pending.get(email) == role:
                            pending_notes.append(
                                f"{email} -> {role} invited, awaiting their first "
                                f"Langfuse sign-in"
                            )
                            continue
                        issues.append(f"{email} should be {role}, is {held.get(email) or 'nothing'}")

                    for email in set(held) | set(pending):
                        if email not in wanted:
                            issues.append(f"{email} holds access nothing entitles them to")

                    if org_name and str(org_name) != str(name):
                        issues.append(f"name drift: Langfuse has {org_name!r}")

                if not issues:
                    print(f"  ok {label}")
                    for note in pending_notes:
                        print(f"        . {note}")
                    continue

                drift += 1
                print(f"  DRIFT {label}")
                for issue in issues:
                    print(f"        - {issue}")
                for note in pending_notes:
                    print(f"        . {note}")

                if not dry_run:
                    report = await org_sync.sync_unit(
                        session, tenant_id=tenant_id, workspace_id=str(uid)
                    )
                    if report is None:
                        print("        -> sync did not run (disabled or Langfuse unreachable)")
                    else:
                        print(f"        -> org {report['org_id']} access={report['access']}")
                        if report["revoked"]:
                            print(f"           revoked={report['revoked']}")

            # ── per-project grants ────────────────────────────────────────────
            # Checked for every unit, drifted or not: a unit can be perfectly converged at
            # the organization level while a project admin inside it holds nothing.
            for uid, name, status, org_id, _org_name in units:
                if status == "archived" or not org_id:
                    continue
                project_drift, project_total = await _reconcile_projects(
                    session, provisioner, org_sync, tenant_id, str(uid), str(org_id),
                    dry_run=dry_run,
                )
                projects_checked += project_total
                drift += project_drift

    print(f"\n{total} unit(s) and {projects_checked} project(s) checked, {drift} with drift.")
    if dry_run and drift:
        print("Dry run — nothing changed. Re-run without --dry-run to converge.")
    return 1 if (dry_run and drift) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report drift and exit non-zero if any; change nothing",
    )
    parser.add_argument("--unit", default=None, help="reconcile one business unit by id")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(dry_run=args.dry_run, only_unit=args.unit)))


if __name__ == "__main__":
    main()
