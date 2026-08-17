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
