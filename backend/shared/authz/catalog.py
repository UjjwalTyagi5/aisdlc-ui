"""Static RBAC reference data — the roles and permissions catalogue.

This module is the ONE declarative description of the built-in roles: name, label,
description, tier, default scope and the permissions each grants. It also owns the
two operations on that data — seeding it into the database, and verifying the
database still matches it.

WHY A SEPARATE MODULE
---------------------
The same facts used to live in three places: permissions in
`shared/authz/permissions.py`, labels in `shared/routers/admin.py`, tier and scope in
a pair of parallel dicts. Adding a role meant editing three files and there was
nothing to catch you if you edited two. Here a role is one record, so a missing field
is a syntax error rather than a silent gap.

THE PERMISSION SETS ARE NOT RE-DECLARED HERE. They are imported from
`permissions.py`, which the request path also reads. Seeding from the same constant
the app enforces with is what makes drift detection meaningful — a catalogue seeded
from a second copy would only ever prove the two copies agree.

DATA ENTRY ORDER
----------------
Referential integrity dictates the order, and it is not optional:

    1. permissions        — no dependencies
    2. roles              — no dependencies
    3. role_permissions   — FK onto BOTH of the above, so it must come last

`role_permissions.role_name` references `roles.name` and `.permission_name`
references `permissions.name`, with a composite primary key over the pair. Inserting
an edge before its endpoints exist raises ForeignKeyViolation, which is the database
doing its job; the ordering below means it never gets the chance.

Deletes run before inserts within step 3 for the same reason in reverse: a permission
being retired must lose its edges before the row itself could ever be removed.

TAMPER DETECTION
----------------
`roles`, `permissions` and `role_permissions` are GLOBAL tables — no tenant_id, no
RLS. A direct `INSERT INTO role_permissions` therefore escalates every holder of that
role, in every tenant, with no application code involved. `verify_rbac_catalog()`
exists to make that loud: it is called at boot and, by default, refuses to start.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.permissions import (
    ALL_PERMISSIONS,
    ROLE_SCOPE,
    ROLE_TIER,
    _ROLE_PERMISSIONS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoleSpec:
    """One built-in role, whole. Frozen: this is reference data, not state."""

    name: str
    label: str
    description: str
    tier: str          # governance | delivery
    scope: str         # organization | business_unit | project | configurable
    permissions: tuple[str, ...]


# Human-facing names. Mirrors ROLE_META in frontend/lib/roles.ts so the same wording
# appears wherever a role is named, in either tier.
_ROLE_LABELS: dict[str, tuple[str, str]] = {
    "org_admin": (
        "Organization Admin",
        "Org-wide governance. Onboards business units and grants model and connector access.",
    ),
    "bu_admin": (
        "Business Unit Admin",
        "Runs one business unit: its projects, members, connections and budget.",
    ),
    "contributor": (
        "Contributor",
        "Onboarded into a unit, awaiting a real role from that unit's admin. Read-only.",
    ),
    "project_admin": (
        "Project Admin",
        "Owns a project, its members and its integrations. Approves where no specialist role exists.",
    ),
    "ba": ("Business Analyst", "Owns requirements and approves them."),
    "architect": ("Architect", "Owns design; approves design and development."),
    "developer": (
        "Developer",
        "Builds. Triggers runs, invokes agents and edits project skills.",
    ),
    "qa": ("QA Engineer", "Owns testing and approves it."),
    "security_engineer": (
        "Security Engineer",
        "Security review with audit, cost and trace visibility.",
    ),
    "devops_engineer": ("DevOps Engineer", "Owns deployment and approves it."),
    "data_engineer": ("Data Engineer", "Data pipelines and related delivery work."),
    "scrum_master": (
        "Scrum Master",
        "Process and visibility. Holds no approval authority.",
    ),
    "custom": (
        "Custom",
        "Tenant-defined role — permissions are attached separately.",
    ),
}


def _build_catalog() -> tuple[RoleSpec, ...]:
    """Compose one record per role, failing loudly if any facet is missing.

    Built at import time so an incomplete role is an ImportError at boot rather than
    a KeyError on whichever request first happens to need the missing field.
    """
    specs: list[RoleSpec] = []
    for name in sorted(_ROLE_PERMISSIONS):
        if name not in _ROLE_LABELS:
            raise RuntimeError(f"role '{name}' has permissions but no label/description")
        if name not in ROLE_TIER:
            raise RuntimeError(f"role '{name}' has no tier")
        if name not in ROLE_SCOPE:
            raise RuntimeError(f"role '{name}' has no default scope")
        label, description = _ROLE_LABELS[name]
        specs.append(
            RoleSpec(
                name=name,
                label=label,
                description=description,
                tier=ROLE_TIER[name],
                scope=ROLE_SCOPE[name],
                permissions=tuple(sorted(_ROLE_PERMISSIONS[name])),
            )
        )
    return tuple(specs)


ROLE_CATALOG: tuple[RoleSpec, ...] = _build_catalog()

# Every permission string that may legally appear in role_permissions. Deliberately a
# superset of the grantable leaf catalogue: role grants reference wildcards such as
# `admin:*`, which are NOT offered to the custom-role builder but must still satisfy
# the foreign key.
CATALOG_PERMISSIONS: tuple[str, ...] = tuple(
    sorted({p for spec in ROLE_CATALOG for p in spec.permissions} | set(ALL_PERMISSIONS))
)


# ── data entry ───────────────────────────────────────────────────────────────

async def seed_rbac_catalog(session: AsyncSession) -> dict[str, int]:
    """Bring the three catalogue tables in line with this module. Idempotent.

    Returns a count per table of rows actually changed, so a caller can tell the
    difference between "already correct" and "repaired".

    Safe to run on every boot: each step is an upsert or a reconcile, never a
    truncate. Truncating role_permissions would momentarily leave every role with no
    permissions, and a request served in that window would be denied everything.
    """
    changed = {"permissions": 0, "roles": 0, "role_permissions": 0}

    # ── step 1: permissions (no dependencies) ────────────────────────────────
    for name in CATALOG_PERMISSIONS:
        result = await session.execute(
            text("INSERT INTO permissions (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"),
            {"n": name},
        )
        changed["permissions"] += result.rowcount or 0

    # ── step 2: roles (no dependencies) ──────────────────────────────────────
    for spec in ROLE_CATALOG:
        result = await session.execute(
            text(
                "INSERT INTO roles (name, description) VALUES (:n, :d) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description "
                "WHERE roles.description IS DISTINCT FROM EXCLUDED.description"
            ),
            {"n": spec.name, "d": spec.description},
        )
        changed["roles"] += result.rowcount or 0

    # ── step 3: role_permissions (FK onto both tables above) ─────────────────
    # Reconciled, not replaced: remove edges this module no longer declares, then add
    # the ones it does. Removing first keeps a retired permission from blocking its
    # own deletion later.
    for spec in ROLE_CATALOG:
        result = await session.execute(
            text(
                "DELETE FROM role_permissions "
                "WHERE role_name = :r AND permission_name <> ALL(:keep)"
            ),
            {"r": spec.name, "keep": list(spec.permissions)},
        )
        changed["role_permissions"] += result.rowcount or 0

        for permission in spec.permissions:
            result = await session.execute(
                text(
                    "INSERT INTO role_permissions (role_name, permission_name) "
                    "VALUES (:r, :p) ON CONFLICT (role_name, permission_name) DO NOTHING"
                ),
                {"r": spec.name, "p": permission},
            )
            changed["role_permissions"] += result.rowcount or 0

    return changed


# ── verification ─────────────────────────────────────────────────────────────

async def verify_rbac_catalog(session: AsyncSession) -> list[str]:
    """Compare the database to this module. Returns a list of differences.

    An empty list means the database is exactly what this module declares. Every
    entry is phrased so it can be read straight out of a boot log without needing
    the code in front of you — a failure that does not say what differs costs an
    hour every time it fires.
    """
    drift: list[str] = []

    db_roles = {
        r[0] for r in (await session.execute(text("SELECT name FROM roles"))).fetchall()
    }
    expected_roles = {spec.name for spec in ROLE_CATALOG}

    for missing in sorted(expected_roles - db_roles):
        drift.append(f"role '{missing}' is missing from the database")
    for extra in sorted(db_roles - expected_roles):
        drift.append(f"role '{extra}' exists in the database but is not in the catalogue")

    rows = (await session.execute(
        text("SELECT role_name, permission_name FROM role_permissions")
    )).fetchall()
    db_grants: dict[str, set[str]] = {}
    for role_name, permission_name in rows:
        db_grants.setdefault(role_name, set()).add(permission_name)

    for spec in ROLE_CATALOG:
        expected = set(spec.permissions)
        actual = db_grants.get(spec.name, set())
        for extra in sorted(actual - expected):
            # The dangerous direction: something granted a permission the catalogue
            # does not, which escalates every holder of the role in every tenant.
            drift.append(f"role '{spec.name}' has EXTRA permission '{extra}' in the database")
        for missing in sorted(expected - actual):
            drift.append(f"role '{spec.name}' is missing permission '{missing}' in the database")

    return drift


class RbacCatalogDriftError(RuntimeError):
    """The database's RBAC catalogue does not match the code's."""


class AgentOwnershipError(RuntimeError):
    """An agent's named owner cannot approve that agent's stage."""


def verify_agent_ownership() -> list[str]:
    """Every pipeline stage's owning role must hold that stage's approve permission.

    THE FAILURE THIS CATCHES, which shipped and was live: the owner map was keyed on
    UI phase names while callers passed backend stage names, so `code_review` missed
    and fell through to a `"project_admin"` default. The product told users to get
    Code Review sign-off from a Project Admin, who does not hold
    `artifact:approve_code_review` and could not give it. Nothing errored, because a
    wrong-but-plausible answer looks exactly like a right one.

    Pure — no IO. Compares three maps that must agree:
      · progression.STAGE_ORDER      — the stages a run can sit at
      · routing.AGENT_OWNER_ROLE     — who owns each
      · permissions._PHASE_PERMISSION / _ROLE_PERMISSIONS — who may approve each

    Returns a list of human-readable problems; empty means consistent.
    """
    from shared.authz.permissions import (  # noqa: PLC0415 — import cycle
        _PHASE_PERMISSION, _ROLE_PERMISSIONS,
    )
    from shared.governance.routing import (  # noqa: PLC0415 — import cycle
        agent_owner_role_or_none,
    )
    from shared.services.orchestrator.progression import STAGE_ORDER  # noqa: PLC0415

    problems: list[str] = []
    for stage in STAGE_ORDER:
        permission = _PHASE_PERMISSION.get(stage)
        if permission is None:
            problems.append(
                f"stage {stage!r} is in STAGE_ORDER but has no entry in "
                f"_PHASE_PERMISSION, so its gate can never be approved"
            )
            continue
        owner = agent_owner_role_or_none(stage)
        if owner is None:
            problems.append(
                f"stage {stage!r} has no owner in routing.AGENT_OWNER_ROLE"
            )
            continue
        if permission not in _ROLE_PERMISSIONS.get(owner, ()):
            holders = sorted(
                r for r, ps in _ROLE_PERMISSIONS.items() if permission in ps
            )
            problems.append(
                f"stage {stage!r} is owned by {owner!r}, which does not hold "
                f"{permission!r} — the named owner cannot approve it "
                f"(holders: {holders or 'NOBODY'})"
            )
    return problems


def assert_agent_ownership() -> None:
    """Boot guard for `verify_agent_ownership`. Raises rather than logging.

    Fatal on purpose, and safe to be fatal: this compares code against code, so a
    failure is a bug a developer introduced, never a state a deployment can be in.
    """
    problems = verify_agent_ownership()
    if not problems:
        logger.info("agent ownership: verified, every stage owner can approve its gate")
        return
    for line in problems:
        logger.error("agent ownership: %s", line)
    raise AgentOwnershipError(
        f"{len(problems)} agent ownership problem(s): " + "; ".join(problems)
    )


async def warn_unheld_owner_roles(session: AsyncSession) -> list[str]:
    """Report owning roles that no active user holds, per tenant.

    A permission granted to nobody is indistinguishable from a bug. This session
    `artifact:approve_deployment` was correct and no user held `devops_engineer`, so
    no human could approve a deployment and the feature read as broken for days.

    WARNS, never raises — unlike `assert_agent_ownership` this compares code against
    *data*, and a fresh tenant that has not hired a DevOps engineer yet is a normal
    state, not a bug. Refusing to boot over it would be wrong.
    """
    from shared.governance.routing import (  # noqa: PLC0415 — import cycle
        agent_owner_role_or_none,
    )
    from shared.services.orchestrator.progression import STAGE_ORDER  # noqa: PLC0415

    owners: dict[str, list[str]] = {}
    for stage in STAGE_ORDER:
        owner = agent_owner_role_or_none(stage)
        if owner is not None:
            owners.setdefault(owner, []).append(stage)
    if not owners:
        return []

    # `role_bindings` has FORCE RLS keyed on app.current_tenant_id, and the app role is
    # not a Postgres superuser — so a query that does not set it reads ZERO ROWS and
    # every role looks unstaffed... or, as written first, every role looks staffed
    # because the tenant list came from the same empty read. That version reported
    # "every stage owner role has a holder in every tenant" against 17 real bindings
    # it could not see. The tenant list must therefore come from `organizations`,
    # which carries no RLS, and each tenant's read must set the GUC first.
    tenants = [
        r.id for r in (await session.execute(
            text("SELECT id::text AS id FROM organizations")
        )).fetchall()
    ]

    gaps: list[str] = []
    for tenant_id in tenants:
        # Transaction-local (the `true`), so it does not leak past this session.
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": tenant_id},
        )
        held = {
            r.role_name for r in (await session.execute(
                text(
                    "SELECT DISTINCT rb.role_name FROM role_bindings rb "
                    "JOIN users u ON u.id = rb.user_id "
                    "WHERE rb.role_name = ANY(CAST(:roles AS text[])) "
                    "  AND rb.status = 'active' AND u.active "
                    "  AND (rb.expires_at IS NULL OR rb.expires_at > now())"
                ),
                {"roles": list(owners)},
            )).fetchall()
        }
        gaps.extend(
            f"tenant {tenant_id}: no active user holds {role!r}, so the "
            f"{', '.join(stages)} gate(s) have no one to approve them"
            for role, stages in owners.items() if role not in held
        )
    for line in gaps:
        logger.warning("agent ownership: %s", line)
    if not gaps:
        logger.info("agent ownership: every stage owner role has a holder in every tenant")
    return gaps


async def assert_rbac_catalog(session: AsyncSession, *, autorepair: bool = False) -> None:
    """Boot guard: verify the catalogue, and refuse to start if it has drifted.

    On a genuinely EMPTY catalogue (no roles at all) this seeds instead of failing —
    that is a fresh database, not tampering, and refusing to boot would leave a new
    deployment unable to start.

    With `autorepair=True` the drift is reconciled and logged instead of raising. That
    is a deliberate opt-in: repairing by default would silently undo tampering and
    remove the alarm, which is the whole point of this check.
    """
    db_role_count = (await session.execute(text("SELECT COUNT(*) FROM roles"))).scalar() or 0
    if db_role_count == 0:
        counts = await seed_rbac_catalog(session)
        logger.info("rbac catalogue: empty database seeded (%s)", counts)
        return

    drift = await verify_rbac_catalog(session)
    if not drift:
        logger.info("rbac catalogue: verified, %d roles match the code", len(ROLE_CATALOG))
        return

    for line in drift:
        logger.error("rbac catalogue drift: %s", line)

    if autorepair:
        counts = await seed_rbac_catalog(session)
        logger.warning("rbac catalogue: %d difference(s) repaired (%s)", len(drift), counts)
        return

    raise RbacCatalogDriftError(
        f"{len(drift)} RBAC catalogue difference(s) between the database and the code. "
        "Refusing to start: a role's permissions must come from the code, never from a "
        "direct database write. See the errors logged above. Set "
        "RBAC_CATALOG_AUTOREPAIR=true to reconcile on boot instead."
    )
