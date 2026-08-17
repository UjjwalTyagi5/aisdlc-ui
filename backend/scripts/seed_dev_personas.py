"""Seed one signed-in-able account per platform role — LOCAL DEVELOPMENT ONLY.

WHY THIS EXISTS. Every screen in this product answers a different question depending on
who is looking at it, and until now the only way to see that was to read the routing
tables. One org admin cannot exercise a two-stage agent-access request, an escalation
ceiling, or "waiting on the Business Unit Admin" — those need two people with different
standing, at minimum.

WHAT IT IS NOT. This is not a fixture layer and nothing in the product reads it. It
writes ordinary rows through the ordinary paths — `grant_role` for every binding, so the
tier-conflict check and the audit trail apply exactly as they would to a real grant. If
this script can create a state, the product could have.

IDEMPOTENT. Re-running it adds nothing and changes nothing: users are matched on email,
units and projects on slug/name, bindings by `grant_role`'s own upsert. Safe to run after
a migration, or when you want to be sure the personas are still intact.

REFUSES TO RUN AGAINST ANYTHING THAT LOOKS LIKE A REAL DEPLOYMENT. See `_guard`. These
accounts share one weak, published password; the guard is what keeps that from being a
production incident rather than a convenience.

    python -m scripts.seed_dev_personas            # seed
    python -m scripts.seed_dev_personas --print    # just re-print the credentials

Writes DEV_LOGINS.txt at the repo root, which is gitignored.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import uuid as _uuid

from sqlalchemy import text

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.auth.passwords import hash_password  # noqa: E402
from shared.authz.grant import grant_role  # noqa: E402
from shared.db import get_db_session_for_tenant, get_db_session_superuser  # noqa: E402

# One password for every persona. Deliberately obvious rather than "secure-looking":
# a plausible-looking password in a checked-in seed script is the one that ends up
# reused somewhere real.
DEV_PASSWORD = "devpassword123"

ORG_SLUG = os.environ.get("DEFAULT_ORG_SLUG", "pwc")

# ── the shape of the test org ────────────────────────────────────────────────
#
# Two business units, because one unit cannot show you a boundary. Nearly every
# rule worth testing — a BU Admin who may read the org but write only their unit,
# a cross-unit borrow, a request that must NOT reach a sibling — is invisible with
# a single unit.

UNITS = [
    {"slug": "payments", "name": "Payments", "cost_center": "CC-4410", "budget": 12800},
    {"slug": "lending", "name": "Lending", "cost_center": "CC-4415", "budget": 9000},
]

# Two projects in different units, for the same reason.
PROJECTS = [
    {"name": "Core ledger — Java 8 to 21", "unit": "payments"},
    {"name": "Mobile onboarding journey", "unit": "lending"},
]

# (email, display purpose, role, scope kind, scope key)
#
# `scope_key` is a unit slug or a project name; `_resolve` turns it into an id.
# Roles are bound at the level `ROLE_SCOPE` says they belong at — a delivery role
# on a PROJECT, a bu_admin on a UNIT — because binding them anywhere else produces
# a working login that behaves nothing like the real thing.
PERSONAS = [
    # ── governance tier ──────────────────────────────────────────────────────
    ("orgadmin@abcbank.com", "Organization Admin — sees and governs everything",
     "org_admin", "organization", None),
    ("farah@abcbank.com", "Business Unit Admin — Payments",
     "bu_admin", "business_unit", "payments"),
    ("marcus@abcbank.com", "Business Unit Admin — Lending",
     "bu_admin", "business_unit", "lending"),

    # ── delivery tier, all on Core ledger so one project has a full roster ────
    ("ana@abcbank.com", "Project Admin — Core ledger",
     "project_admin", "project", "Core ledger — Java 8 to 21"),
    ("priya@abcbank.com", "Business Analyst — owns the Requirements agent",
     "ba", "project", "Core ledger — Java 8 to 21"),
    ("iris@abcbank.com", "Architect — owns Design, Development and Code Review",
     "architect", "project", "Core ledger — Java 8 to 21"),
    ("diego@abcbank.com", "Developer — builds, does not approve their own work",
     "developer", "project", "Core ledger — Java 8 to 21"),
    ("ingrid@abcbank.com", "QA — owns Testing and Validation",
     "qa", "project", "Core ledger — Java 8 to 21"),
    ("hana@abcbank.com", "Security Engineer — owns the Security gate",
     "security_engineer", "project", "Core ledger — Java 8 to 21"),
    ("lena@abcbank.com", "DevOps Engineer — owns Deployment",
     "devops_engineer", "project", "Core ledger — Java 8 to 21"),
    ("bruno@abcbank.com", "Data Engineer — owns Data Engineering",
     "data_engineer", "project", "Core ledger — Java 8 to 21"),
    ("luca@abcbank.com", "Scrum Master — delivery tier, owns no gate",
     "scrum_master", "project", "Core ledger — Java 8 to 21"),

    # ── the second project, so cross-project scope is visible ────────────────
    ("sofia@abcbank.com", "Project Admin — Mobile onboarding (a DIFFERENT project)",
     "project_admin", "project", "Mobile onboarding journey"),

    # ── the gap the onboarding queue exists to close ──────────────────────────
    ("amara@abcbank.com", "Contributor with NO role yet — placed in Payments, awaiting one",
     "contributor", "business_unit", "payments"),
]


def _guard() -> None:
    """Refuse to run anywhere that might not be a developer's machine.

    Three signals, any one of which stops it. Weak credentials are fine on a laptop
    and are an incident anywhere else, and a seed script is exactly the thing that
    gets run against the wrong DATABASE_URL at 6pm.
    """
    if os.environ.get("ALLOW_DEV_SEED") == "1":
        return

    dsn = os.environ.get("POSTGRES_CONN_STRING", "") or os.environ.get(
        "POSTGRES_SYNC_CONN_STRING", ""
    )
    env_name = (os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "").lower()

    problems = []
    if env_name and env_name not in ("local", "dev", "development", "test"):
        problems.append(f"ENVIRONMENT={env_name!r}")
    if dsn and not any(h in dsn for h in ("localhost", "127.0.0.1", "host.docker.internal")):
        problems.append("the database is not on localhost")
    if not dsn:
        problems.append("no POSTGRES_CONN_STRING is set, so the target is unknown")

    if problems:
        sys.exit(
            "Refusing to seed dev personas: "
            + "; ".join(problems)
            + ".\nThese accounts share one published password. If you are certain, "
            "re-run with ALLOW_DEV_SEED=1."
        )


async def _org_id() -> str:
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text("SELECT id FROM organizations WHERE slug = :s"), {"s": ORG_SLUG}
        )).first()
    if row is None:
        sys.exit(
            f"No organization with slug {ORG_SLUG!r}. Start the backend once so its "
            "bootstrap creates the org, then re-run."
        )
    return str(row.id)


async def _ensure_units(org_id: str) -> dict[str, str]:
    """Create the units if absent; return slug -> id for the ones now present."""
    ids: dict[str, str] = {}
    async with get_db_session_superuser() as s:
        for unit in UNITS:
            row = (await s.execute(
                text("SELECT id FROM workspaces WHERE organization_id = :o AND slug = :s"),
                {"o": org_id, "s": unit["slug"]},
            )).first()
            if row is not None:
                ids[unit["slug"]] = str(row.id)
                continue
            new_id = str(_uuid.uuid4())
            await s.execute(
                text(
                    "INSERT INTO workspaces "
                    "  (id, organization_id, slug, display_name, cost_center, monthly_budget_usd) "
                    "VALUES (:i, :o, :s, :n, :c, :b)"
                ),
                {
                    "i": new_id, "o": org_id, "s": unit["slug"], "n": unit["name"],
                    "c": unit["cost_center"], "b": unit["budget"],
                },
            )
            ids[unit["slug"]] = new_id
            print(f"  + business unit {unit['name']}")
    return ids


async def _ensure_projects(org_id: str, units: dict[str, str]) -> dict[str, str]:
    """Create the projects if absent; return name -> id.

    Written through a TENANT session, not the superuser one: `projects` is FORCE
    RLS and its WITH CHECK compares tenant_id against the app.current_tenant_id
    GUC, which only the tenant session sets.
    """
    ids: dict[str, str] = {}
    async with get_db_session_for_tenant(org_id) as s:
        for project in PROJECTS:
            row = (await s.execute(
                text("SELECT id FROM projects WHERE display_name = :n"),
                {"n": project["name"]},
            )).first()
            if row is not None:
                ids[project["name"]] = str(row.id)
                continue
            new_id = str(_uuid.uuid4())
            await s.execute(
                text(
                    "INSERT INTO projects "
                    "  (id, workspace_id, tenant_id, display_name, provider_kind) "
                    "VALUES (CAST(:i AS uuid), CAST(:w AS uuid), CAST(:t AS uuid), :n, 'github')"
                ),
                {
                    "i": new_id, "w": units[project["unit"]], "t": org_id, "n": project["name"],
                },
            )
            ids[project["name"]] = new_id
            print(f"  + project {project['name']}")
    return ids


async def _ensure_user(org_id: str, email: str) -> str:
    """Create the account if absent, and reset the password if it exists.

    The reset is the point of re-running: a persona whose password drifted is a
    persona nobody can sign in as, and hunting that down is worse than the one
    UPDATE it takes to rule it out.
    """
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text("SELECT id FROM users WHERE lower(email) = :e"), {"e": email}
        )).first()
        if row is not None:
            await s.execute(
                text("UPDATE users SET password_hash = :p, active = true WHERE id = :i"),
                {"p": hash_password(DEV_PASSWORD), "i": row.id},
            )
            return str(row.id)

        user_id = str(_uuid.uuid4())
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                "VALUES (:i, :e, :p, :t, true)"
            ),
            {"i": user_id, "e": email, "p": hash_password(DEV_PASSWORD), "t": org_id},
        )
        print(f"  + user {email}")
        return user_id


def _write_credentials_file(rows: list[tuple[str, str, str, str]]) -> pathlib.Path:
    """Write DEV_LOGINS.txt at the repo root. Gitignored — see .gitignore."""
    root = pathlib.Path(__file__).resolve().parents[2]
    path = root / "DEV_LOGINS.txt"

    width = max(len(r[0]) for r in rows)
    lines = [
        "SDLC Platform — local development logins",
        "=" * 60,
        "",
        f"Password for EVERY account below:  {DEV_PASSWORD}",
        "",
        "Sign in at http://localhost:3000/login.",
        "These exist only in your local Postgres. Re-create them any time with:",
        "    cd backend && python -m scripts.seed_dev_personas",
        "",
        "-" * 60,
        "",
    ]
    for email, role, scope, purpose in rows:
        lines.append(f"{email:<{width}}  {role}")
        lines.append(f"{'':<{width}}  {scope}")
        lines.append(f"{'':<{width}}  {purpose}")
        lines.append("")

    lines += [
        "-" * 60,
        "",
        "WHAT TO TRY",
        "",
        "  The queue needs two people. An Org Admin cannot raise a request — the",
        "  chain ends at them, so there would be nobody to decide it.",
        "",
        "  1. Sign in as diego@ (Developer) and raise a request from",
        "     Requests & Approvals. It routes to the Project Admin, because a",
        "     contributor's ask climbs exactly one rung.",
        "  2. Sign in as ana@ (Project Admin, Core ledger) — it is in her queue.",
        "     Approving or rejecting is hers; she cannot approve one she raised.",
        "  3. As ana@, raise a budget increase from Cost & Budget. It skips the",
        "     BU Admin and goes to the Org Admin, because a cap is theirs.",
        "     Approving it MOVES the cap — check the unit afterwards.",
        "  4. As diego@, raise an Agent access request for the Security agent.",
        "     It is answered TWICE: ana@ first (should this person do this work),",
        "     then hana@, the Security Engineer who owns that agent.",
        "  5. amara@ has no role. She appears on Users as 'No role yet' — farah@,",
        "     the Payments admin, is the one who gives her one.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def main(print_only: bool) -> None:
    org_id = await _org_id()

    rows: list[tuple[str, str, str, str]] = []

    if not print_only:
        _guard()
        print(f"Seeding personas into org {ORG_SLUG} ({org_id})")
        units = await _ensure_units(org_id)
        projects = await _ensure_projects(org_id, units)
    else:
        units = {}
        projects = {}
        async with get_db_session_superuser() as s:
            for unit in UNITS:
                r = (await s.execute(
                    text("SELECT id FROM workspaces WHERE organization_id = :o AND slug = :s"),
                    {"o": org_id, "s": unit["slug"]},
                )).first()
                if r:
                    units[unit["slug"]] = str(r.id)
        async with get_db_session_for_tenant(org_id) as s:
            for project in PROJECTS:
                r = (await s.execute(
                    text("SELECT id FROM projects WHERE display_name = :n"),
                    {"n": project["name"]},
                )).first()
                if r:
                    projects[project["name"]] = str(r.id)

    for email, purpose, role, scope_kind, scope_key in PERSONAS:
        if scope_kind == "organization":
            scope_id, scope_label = org_id, "the whole organization"
        elif scope_kind == "business_unit":
            scope_id = units.get(scope_key or "")
            scope_label = f"Business Unit: {scope_key}"
        else:
            scope_id = projects.get(scope_key or "")
            scope_label = f"Project: {scope_key}"

        if scope_id is None:
            print(f"  ! skipped {email}: no {scope_kind} {scope_key!r}")
            continue

        if not print_only:
            user_id = await _ensure_user(org_id, email)
            # Through grant_role, not a raw INSERT: the tier-conflict check and the
            # audit record apply exactly as they would to a grant made in the UI.
            await grant_role(
                user_id, scope_id, role,
                tenant_id=org_id, scope_kind=scope_kind, granted_by="dev-seed",
            )

        rows.append((email, role, scope_label, purpose))

    path = _write_credentials_file(rows)
    print(f"\n{len(rows)} personas ready. Credentials written to {path}")
    print(f"Password for all of them: {DEV_PASSWORD}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print", dest="print_only", action="store_true",
        help="re-write DEV_LOGINS.txt from what already exists, without seeding",
    )
    args = parser.parse_args()
    asyncio.run(main(args.print_only))
