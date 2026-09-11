"""Give every SDLC project its own Langfuse project, so logs cannot mix.

THE PROBLEM THIS SOLVES. One Langfuse project with `tenant:` / `workspace:` / `project:`
tags puts every business unit's prompts and outputs in one bucket and asks the read path
to be careful. PRD §17 calls organisation-wide trace leakage a release blocker and §45
names "project-level trace isolation" as an R1 gate — and a tag is a promise the
application makes, not a boundary the store enforces. One wrong filter and a project
admin is reading another unit's requirements verbatim.

THE MAPPING. Langfuse has two levels; this platform has three, so one tier has to give:

    SDLC organization (tenant)  ->  a tag  (PRD §3: single-tenant by default)
    SDLC business unit          ->  Langfuse ORGANIZATION
    SDLC project                ->  Langfuse PROJECT, with its own API key pair
    member                      ->  Langfuse userId within that project

The tenant tier is the right one to drop: this is a single-tenant-default product, while
§45 is explicit about projects. Isolation between units and between projects is now
structural — a key pair only reaches its own project, so a wrong filter yields nothing
rather than someone else's data.

WHY IT WRITES TO THE DATABASE. Creating organizations, projects and API keys through the
Langfuse API requires an Enterprise licence; this deployment is OSS, so that route does
not exist. Writing the rows directly is what the sibling platform on this same instance
does, and it is the only unlicensed path. The cost is real and worth stating: this
couples us to Langfuse's schema, so a Langfuse upgrade that renames a column breaks
provisioning. Hence `_assert_schema` below — it fails loudly at the start of a
provisioning run rather than writing half a project.

THE SALT MUST MATCH THE INSTANCE. Langfuse stores `sha256(secret_key + sha256_hex(salt))`
as `fast_hashed_secret_key` and authenticates against it (see its
`packages/shared/src/server/auth/apiKeys.ts`). A wrong LANGFUSE_SALT produces keys that
look right and fail every request with a 401.

IDEMPOTENT. Every step looks before it writes, so re-running against an existing unit or
project returns what is already there instead of a duplicate.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from config.env import (
    DEFAULT_ORG_NAME,
    LANGFUSE_BOOTSTRAP_USER_EMAIL,
    LANGFUSE_DB_URL,
    LANGFUSE_HOST,
    LANGFUSE_RETENTION_DAYS,
    LANGFUSE_SALT,
)

logger = logging.getLogger(__name__)

# Only these tables are ever written. Not a security boundary — the DSN already grants
# everything — but it keeps a typo or a future edit from reaching Langfuse's trace data.
_WRITABLE_TABLES = frozenset(
    {
        "organizations",
        "organization_memberships",
        "membership_invitations",
        "projects",
        "project_memberships",
        "api_keys",
    }
)

# Langfuse's organization-role enum. OWNER and ADMIN are the only two this module grants:
# the platform's organization admins own every unit's organization, and the unit's own
# bu_admin administers theirs. MEMBER/VIEWER/NONE exist in Langfuse and are unused here.
ORG_ROLE_OWNER = "OWNER"
ORG_ROLE_ADMIN = "ADMIN"

# Strongest-wins ordering, used when two rules name the same address. Observed in
# testing: the bootstrap address is an OWNER, and appointing that same person admin of one
# business unit DEMOTED them to ADMIN of it — the safety floor that keeps a provisioned
# organization openable was being removed by an ordinary appointment.
_ROLE_RANK = {"NONE": 0, "VIEWER": 1, "MEMBER": 2, ORG_ROLE_ADMIN: 3, ORG_ROLE_OWNER: 4}


def strongest_role(*roles: str) -> str:
    """The most privileged of the given Langfuse roles."""
    return max((r for r in roles if r), key=lambda r: _ROLE_RANK.get(r, 0))

# Columns this module depends on. Checked before the first write so a Langfuse upgrade
# that renames one fails with a readable message instead of a half-created project.
_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "organizations": {"id", "name"},
    "projects": {"id", "name", "org_id", "deleted_at"},
    "organization_memberships": {"id", "org_id", "user_id", "role"},
    # How a person who has never signed in to Langfuse is granted access. Langfuse
    # creates the membership from this row when they first authenticate, which is the
    # only supported way to grant somebody who has no `users` row yet.
    "membership_invitations": {"id", "email", "org_id", "org_role"},
    "api_keys": {
        "id",
        "public_key",
        "hashed_secret_key",
        "fast_hashed_secret_key",
        "display_secret_key",
        "project_id",
    },
}


class LangfuseProvisioningError(RuntimeError):
    """Provisioning could not complete. The caller decides whether that is fatal."""


@dataclass(frozen=True)
class ProvisionedProject:
    """What a caller needs to send traces to one project, and to record the binding."""

    langfuse_org_id: str
    langfuse_project_id: str
    langfuse_project_name: str
    public_key: str
    secret_key: str
    host: str
    created_org: bool
    created_project: bool


@dataclass(frozen=True)
class ProvisionedOrg:
    """A business unit's Langfuse organization, and what access was applied to it."""

    langfuse_org_id: str
    langfuse_org_name: str
    host: str
    created: bool
    # email -> what happened ("granted" / "updated" / "invited" / "unchanged" / ...).
    # `invited` is the interesting one: that person has no Langfuse account yet, so the
    # grant is pending their first sign-in and is not access they hold right now.
    access: dict[str, str]


def _affected(command_tag) -> int:
    """Row count out of an asyncpg command tag ("DELETE 3" -> 3)."""
    try:
        return int(str(command_tag).rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        return 0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mask(secret_key: str) -> str:
    """Langfuse's display form: enough to recognise a key, not enough to use it."""
    return f"{secret_key[:6]}...{secret_key[-4:]}" if len(secret_key) > 12 else "..."


def generate_key_pair() -> tuple[str, str]:
    """A Langfuse-shaped key pair. Lengths match what Langfuse itself issues."""
    return f"pk-lf-{secrets.token_hex(16)}", f"sk-lf-{secrets.token_hex(24)}"


def hash_secret_key(secret_key: str, salt: str) -> tuple[str, str]:
    """(bcrypt hash, fast sha256 hash) exactly as Langfuse computes them.

    The fast hash is what authenticates a request on the hot path; the bcrypt hash is
    the slow verification. Both are stored, so both must be right.
    """
    try:
        import bcrypt  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - declared dependency
        raise LangfuseProvisioningError(
            "bcrypt is required to hash Langfuse API keys — pip install bcrypt"
        ) from exc

    hashed = bcrypt.hashpw(secret_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    # Langfuse: createShaHash(privateKey, salt) = SHA256(privateKey + SHA256_hex(salt))
    salt_hash_hex = hashlib.sha256(salt.encode("utf-8")).hexdigest()
    fast = hashlib.sha256((secret_key + salt_hash_hex).encode("utf-8")).hexdigest()
    return hashed, fast


class LangfuseProvisioner:
    """Creates Langfuse organizations, projects and keys by writing to its database."""

    def __init__(
        self,
        db_url: str | None = None,
        salt: str | None = None,
        host: str | None = None,
        bootstrap_email: str | None = None,
    ) -> None:
        self.db_url = (db_url if db_url is not None else LANGFUSE_DB_URL).strip()
        self.salt = salt if salt is not None else LANGFUSE_SALT
        self.host = (host if host is not None else LANGFUSE_HOST).strip()
        self.bootstrap_email = (
            bootstrap_email if bootstrap_email is not None else LANGFUSE_BOOTSTRAP_USER_EMAIL
        ).strip()

    # ── connection ────────────────────────────────────────────────────────────

    def _require_config(self) -> None:
        if not self.db_url:
            raise LangfuseProvisioningError(
                "LANGFUSE_DB_URL is not set — per-project provisioning needs direct "
                "database access because the Langfuse management API is Enterprise-only."
            )
        if not self.salt:
            raise LangfuseProvisioningError(
                "LANGFUSE_SALT is not set. Keys minted without the instance's own salt "
                "authenticate against nothing and fail every request with 401."
            )

    async def _connect(self):
        import asyncpg  # noqa: PLC0415

        # asyncpg does not accept the +driver suffixes SQLAlchemy URLs may carry.
        dsn = self.db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgresql+psycopg://", "postgresql://"
        )
        try:
            return await asyncpg.connect(dsn, ssl="require", timeout=30)
        except Exception as exc:
            raise LangfuseProvisioningError(
                f"cannot reach the Langfuse database: {type(exc).__name__}. "
                f"On Azure this is usually a firewall rule missing for this host."
            ) from exc

    async def _assert_schema(self, conn) -> None:
        """Fail before writing anything if Langfuse's schema is not what we expect."""
        rows = await conn.fetch(
            "select table_name, column_name from information_schema.columns "
            "where table_schema='public' and table_name = any($1::text[])",
            list(_REQUIRED_COLUMNS),
        )
        present: dict[str, set[str]] = {}
        for r in rows:
            present.setdefault(r["table_name"], set()).add(r["column_name"])

        problems: list[str] = []
        for table, needed in _REQUIRED_COLUMNS.items():
            if table not in present:
                problems.append(f"table '{table}' is missing")
                continue
            missing = needed - present[table]
            if missing:
                problems.append(f"{table} is missing {sorted(missing)}")
        if problems:
            raise LangfuseProvisioningError(
                "the Langfuse schema is not what this provisioner expects "
                f"({'; '.join(problems)}). A Langfuse upgrade probably moved something; "
                "provisioning writes rows directly and cannot guess the new shape."
            )

    # ── steps, each idempotent ────────────────────────────────────────────────

    async def _available_org_name(
        self, conn, name: str, owned_org_ids: frozenset[str]
    ) -> tuple[str, Optional[str]]:
        """Resolve `name` to a name we may safely use, plus the org already holding it.

        WHY THIS IS NOT JUST A LOOKUP. Langfuse does not make organization names unique,
        and this instance is shared: it already holds organizations called `Payments` and
        `Lending` that belong to the sibling product. Returning the existing row for a
        matching name — which is what this used to do — means a business unit called
        `Payments` adopts theirs, and from then on this platform writes its prompts and
        completions into an organization their users can open. That is the exact
        cross-unit mixing the per-unit organization exists to prevent, and it fails
        silently: provisioning succeeds, traces arrive, nothing looks wrong.

        So an existing organization is reused ONLY if we created it — that is,
        `owned_org_ids` (the ids recorded on this platform's workspaces) contains it.
        Otherwise the name is disambiguated and the caller gets a fresh organization.
        Isolation wins over an exact name match; the unit's real name is in this product's
        own UI either way.
        """
        candidate = name
        attempt = 1
        while True:
            existing = await conn.fetchval(
                "select id from organizations where name=$1", candidate
            )
            if existing is None:
                return candidate, None
            if str(existing) in owned_org_ids:
                return candidate, str(existing)
            attempt += 1
            suffix = DEFAULT_ORG_NAME if attempt == 2 else f"{DEFAULT_ORG_NAME} {attempt - 1}"
            candidate = f"{name} ({suffix})"
            logger.warning(
                "Langfuse already has an organization named %r that this platform did not "
                "create — not adopting it, using %r instead so traces cannot mix",
                name, candidate,
            )

    async def _ensure_org(
        self,
        conn,
        name: str,
        *,
        org_id: Optional[str] = None,
        owned_org_ids: frozenset[str] = frozenset(),
    ) -> tuple[str, str, bool]:
        """(org_id, org_name, created). Addresses a known organization by id, never name."""
        if org_id:
            row = await conn.fetchrow(
                "select id, name from organizations where id=$1", org_id
            )
            if row is not None:
                return str(row["id"]), str(row["name"]), False
            # The recorded organization is gone — a Langfuse rebuild, or somebody deleting
            # it in the UI. Fall through and make a new one rather than failing: the unit
            # needs somewhere to send traces more than it needs the old id. The caller
            # records the new id, and the old traces stay unreachable, which is the honest
            # outcome of having deleted the organization holding them.
            logger.warning(
                "Langfuse organization %s is recorded against a business unit but no "
                "longer exists — provisioning a replacement", org_id,
            )

        resolved, existing = await self._available_org_name(conn, name, owned_org_ids)
        if existing is not None:
            return existing, resolved, False
        new_id = str(uuid.uuid4())
        now = _now()
        await conn.execute(
            "insert into organizations (id,name,created_at,updated_at) values ($1,$2,$3,$4)",
            new_id, resolved, now, now,
        )
        return new_id, resolved, True

    async def _ensure_project(self, conn, org_id: str, name: str) -> tuple[str, bool]:
        existing = await conn.fetchval(
            "select id from projects where org_id=$1 and name=$2", org_id, name
        )
        if existing:
            return str(existing), False
        project_id = str(uuid.uuid4())
        now = _now()
        retention = None
        if LANGFUSE_RETENTION_DAYS.strip():
            try:
                retention = int(LANGFUSE_RETENTION_DAYS)
            except ValueError:
                logger.warning(
                    "LANGFUSE_RETENTION_DAYS=%r is not a number — leaving the instance "
                    "default", LANGFUSE_RETENTION_DAYS,
                )
        await conn.execute(
            "insert into projects (id,name,org_id,created_at,updated_at,retention_days) "
            "values ($1,$2,$3,$4,$5,$6)",
            project_id, name, org_id, now, now, retention,
        )
        return project_id, True

    def _bootstrap_emails(self) -> list[str]:
        """Addresses always made OWNER of any organization this module provisions.

        MORE THAN ONE, DELIBERATELY. Langfuse lists only the organizations you belong to,
        so provisioning under a single service account produces projects that collect
        traces nobody can open — indistinguishable, from the UI, from provisioning having
        failed. Every address that should be able to look belongs here.

        STILL NEEDED NOW THAT REAL PEOPLE ARE GRANTED. The platform's own organization
        admins are made OWNER as well (see `sync_org_access`), derived from role bindings
        — but on a fresh deployment none of them may have a Langfuse identity yet, and an
        organization whose every grant is a pending invitation is one nobody can open. This
        list is the floor that keeps it reachable.

        No database lookup: unlike the old version this returns ADDRESSES, because
        `_grant_org_role` no longer requires the person to already exist.
        """
        return [e.strip() for e in (self.bootstrap_email or "").split(",") if e.strip()]

    async def _grant_org_role(
        self, conn, org_id: str, email: str, role: str, invited_by: Optional[str] = None
    ) -> str:
        """Give `email` `role` on `org_id`, whether or not they have a Langfuse account.

        THE TWO PATHS, AND WHY BOTH ARE NEEDED. `organization_memberships.user_id`
        references a `users` row, and Langfuse only creates one when that person first
        signs in. A business unit admin appointed five minutes ago has never signed in, so
        the membership cannot be written — which is why granting used to be limited to a
        fixed list of bootstrap addresses that happened to already exist.

        `membership_invitations` is Langfuse's own answer: a pending grant keyed by email,
        which Langfuse converts into a membership when that address authenticates. So:

            user row exists -> organization_memberships
            it does not     -> membership_invitations, consumed at first sign-in

        WHY NOT JUST CREATE THE USER ROW. We have write access and could. But this
        instance authenticates through `azure-ad`, and a `users` row with no matching
        `Account` row is what makes NextAuth raise `OAuthAccountNotLinked` — we would be
        locking the person out of Langfuse in the act of granting them access. The
        invitation path is supported and has no such failure mode.

        Idempotent: re-granting the same role is a no-op, a different role is an update.
        """
        email = (email or "").strip()
        if not email:
            return "skipped"
        now = _now()

        user_id = await conn.fetchval(
            "select id from users where lower(email) = lower($1)", email
        )
        if user_id:
            # They have an account now, so any invitation is stale. Left in place it
            # would be a second, independent grant that Langfuse could apply later —
            # including after a revoke.
            await conn.execute(
                "delete from membership_invitations "
                "where org_id=$1 and lower(email)=lower($2)",
                org_id, email,
            )
            current = await conn.fetchrow(
                "select id, role from organization_memberships where org_id=$1 and user_id=$2",
                org_id, str(user_id),
            )
            if current is None:
                await conn.execute(
                    "insert into organization_memberships "
                    '(id,org_id,user_id,role,created_at,updated_at) '
                    'values ($1,$2,$3,$4::text::"Role",$5,$6)',
                    str(uuid.uuid4()), org_id, str(user_id), role, now, now,
                )
                return "granted"
            if str(current["role"]) != role:
                await conn.execute(
                    'update organization_memberships set role=$1::text::"Role", '
                    "updated_at=$2 where id=$3",
                    role, now, str(current["id"]),
                )
                return "updated"
            return "unchanged"

        existing = await conn.fetchrow(
            "select id, org_role from membership_invitations "
            "where org_id=$1 and lower(email)=lower($2)",
            org_id, email,
        )
        if existing is None:
            await conn.execute(
                "insert into membership_invitations "
                "(id,email,org_id,org_role,invited_by_user_id,created_at,updated_at) "
                'values ($1,$2,$3,$4::text::"Role",$5,$6,$7)',
                str(uuid.uuid4()), email.lower(), org_id, role, invited_by, now, now,
            )
            return "invited"
        if str(existing["org_role"]) != role:
            await conn.execute(
                'update membership_invitations set org_role=$1::text::"Role", '
                "updated_at=$2 where id=$3",
                role, now, str(existing["id"]),
            )
            return "invite-updated"
        return "unchanged"

    async def _revoke_org_role(self, conn, org_id: str, email: str) -> str:
        """Remove `email`'s access to `org_id` — membership AND any pending invitation.

        BOTH, ALWAYS. Deleting only the membership leaves an invitation that Langfuse
        will happily convert into a fresh membership the next time that address signs in.
        Somebody removed as a unit admin would silently regain access by logging in.
        """
        email = (email or "").strip()
        if not email:
            return "skipped"
        removed: list[str] = []

        user_id = await conn.fetchval(
            "select id from users where lower(email) = lower($1)", email
        )
        if user_id:
            result = await conn.execute(
                "delete from organization_memberships where org_id=$1 and user_id=$2",
                org_id, str(user_id),
            )
            if not result.endswith(" 0"):
                removed.append("membership")
        result = await conn.execute(
            "delete from membership_invitations where org_id=$1 and lower(email)=lower($2)",
            org_id, email,
        )
        if not result.endswith(" 0"):
            removed.append("invitation")
        return "+".join(removed) if removed else "nothing-to-revoke"

    async def _ensure_memberships(self, conn, org_id: str, email: str) -> str:
        """Make a bootstrap address an OWNER. Best-effort — a key pair matters more.

        Traces do not depend on membership; visibility does. A failure here must not cost
        us a working key pair, so it is swallowed rather than raised.
        """
        try:
            return await self._grant_org_role(conn, org_id, email, ORG_ROLE_OWNER)
        except Exception as exc:
            logger.warning(
                "Langfuse membership not created for org=%s (%s) — traces will still "
                "arrive, but the organization may not be visible in the Langfuse UI",
                org_id, type(exc).__name__,
            )
            return "failed"

    async def _insert_api_key(self, conn, project_id: str, note: str) -> tuple[str, str]:
        public_key, secret_key = generate_key_pair()
        hashed, fast = hash_secret_key(secret_key, self.salt)
        await conn.execute(
            "insert into api_keys (id,public_key,hashed_secret_key,fast_hashed_secret_key,"
            "display_secret_key,project_id,scope,note,created_at) "
            "values ($1,$2,$3,$4,$5,$6,'PROJECT',$7,$8)",
            str(uuid.uuid4()), public_key, hashed, fast, _mask(secret_key),
            project_id, note, _now(),
        )
        return public_key, secret_key

    # ── the one public entry point ────────────────────────────────────────────

    async def provision(
        self,
        *,
        unit_name: str,
        project_name: str,
        org_id: Optional[str] = None,
        owned_org_ids: frozenset[str] = frozenset(),
    ) -> ProvisionedProject:
        """Ensure a Langfuse org for the unit and a project inside it, and mint a key.

        `unit_name` is the business unit — it becomes the Langfuse organization.
        `project_name` is the SDLC project — it becomes the Langfuse project.

        `org_id` is the organization already recorded against that business unit
        (`workspaces.langfuse_org_id`). Pass it whenever it is known: it is what makes
        this reuse the organization created when the unit was created, instead of
        searching by name. `owned_org_ids` is every such id across the platform, used to
        tell our organizations apart from the sibling product's — see `_available_org_name`.

        Runs in one transaction: a failure part-way leaves no organization without its
        project, and no project without its key.
        """
        self._require_config()
        conn = await self._connect()
        try:
            await self._assert_schema(conn)
            async with conn.transaction():
                org_id, _org_name, created_org = await self._ensure_org(
                    conn, unit_name, org_id=org_id, owned_org_ids=owned_org_ids
                )
                project_id, created_project = await self._ensure_project(
                    conn, org_id, project_name
                )
                for email in self._bootstrap_emails():
                    await self._ensure_memberships(conn, org_id, email)
                public_key, secret_key = await self._insert_api_key(
                    conn, project_id, note=f"provisioned-for-{project_name}"
                )
            logger.info(
                "Langfuse provisioned: org=%s(%s) project=%s(%s)",
                unit_name, "new" if created_org else "existing",
                project_name, "new" if created_project else "existing",
            )
            return ProvisionedProject(
                langfuse_org_id=org_id,
                langfuse_project_id=project_id,
                langfuse_project_name=project_name,
                public_key=public_key,
                secret_key=secret_key,
                host=self.host,
                created_org=created_org,
                created_project=created_project,
            )
        finally:
            await conn.close()


    # -- the business-unit lifecycle -------------------------------------------
    #
    # These exist because a business unit's Langfuse organization used to appear only when
    # somebody ran an agent inside one of its projects -- creating the unit did nothing, and
    # the only people ever granted access were a fixed list of addresses in an env var.
    # Each one is called from a fail-soft hook in the BU routes, so none of them may raise:
    # Langfuse being unreachable must never stop somebody creating a business unit.
    # `scripts/sync_langfuse_orgs.py` converges whatever a failure left behind.

    async def provision_org(
        self,
        *,
        unit_name: str,
        org_id: Optional[str] = None,
        owned_org_ids: frozenset = frozenset(),
        access: Optional[dict] = None,
        invited_by_email: Optional[str] = None,
    ) -> ProvisionedOrg:
        """Ensure the Langfuse organization for a business unit, and apply access to it.

        Organization-only, unlike `provision()`, which always wants a project too. A unit
        is created before any of its projects exist, and its organization should appear in
        Langfuse at that moment rather than whenever somebody first runs an agent.

        `access` maps email -> Langfuse role. Bootstrap addresses are always added as
        OWNER on top, so an organization is never provisioned with nobody able to open it.
        """
        self._require_config()
        conn = await self._connect()
        try:
            await self._assert_schema(conn)
            invited_by = (
                await conn.fetchval(
                    "select id from users where lower(email) = lower($1)", invited_by_email
                )
                if invited_by_email
                else None
            )
            applied: dict = {}
            async with conn.transaction():
                resolved_id, resolved_name, created = await self._ensure_org(
                    conn, unit_name, org_id=org_id, owned_org_ids=owned_org_ids
                )
                wanted = {e.strip().lower(): ORG_ROLE_OWNER for e in self._bootstrap_emails()}
                # STRONGEST WINS, not last-write. A bootstrap address that is also this
                # unit's admin must stay OWNER: letting the ADMIN grant overwrite it would
                # quietly remove the floor that guarantees somebody can open the
                # organization at all.
                for email, role in (access or {}).items():
                    if not email or not email.strip():
                        continue
                    key = email.strip().lower()
                    wanted[key] = strongest_role(wanted.get(key, ""), role)
                for email, role in wanted.items():
                    applied[email] = await self._grant_org_role(
                        conn, resolved_id, email, role,
                        invited_by=str(invited_by) if invited_by else None,
                    )
            logger.info(
                "Langfuse organization for unit %r: %s (%s), access=%s",
                unit_name, resolved_id, "created" if created else "existing", applied,
            )
            return ProvisionedOrg(
                langfuse_org_id=resolved_id,
                langfuse_org_name=resolved_name,
                host=self.host,
                created=created,
                access=applied,
            )
        finally:
            await conn.close()

    async def sync_org_access(
        self,
        *,
        org_id: str,
        grants: Optional[dict] = None,
        revoke: Optional[list] = None,
        invited_by_email: Optional[str] = None,
    ) -> dict:
        """Apply grants and revocations to an existing organization. Never raises.

        Revocations are applied BEFORE grants, so moving a role between two people in one
        call cannot leave the incoming grant undone by the outgoing revoke when both name
        the same address.
        """
        self._require_config()
        try:
            conn = await self._connect()
        except LangfuseProvisioningError as exc:
            logger.warning("langfuse access sync skipped for org=%s (%s)", org_id, exc)
            return {}
        applied: dict = {}
        try:
            invited_by = (
                await conn.fetchval(
                    "select id from users where lower(email) = lower($1)", invited_by_email
                )
                if invited_by_email
                else None
            )
            async with conn.transaction():
                for email in revoke or []:
                    applied[email.strip().lower()] = await self._revoke_org_role(
                        conn, org_id, email
                    )
                for email, role in (grants or {}).items():
                    applied[email.strip().lower()] = await self._grant_org_role(
                        conn, org_id, email, role,
                        invited_by=str(invited_by) if invited_by else None,
                    )
        except Exception:
            logger.warning("langfuse access sync failed for org=%s", org_id, exc_info=True)
        finally:
            await conn.close()
        return applied

    async def rename_org(
        self, *, org_id: str, new_name: str, owned_org_ids: frozenset = frozenset()
    ) -> Optional[str]:
        """Rename a business unit's organization. Returns the name actually used.

        May differ from `new_name`: renaming INTO a name the sibling product already uses
        would leave two organizations indistinguishable in the Langfuse UI, so the same
        disambiguation as creation applies.
        """
        self._require_config()
        try:
            conn = await self._connect()
        except LangfuseProvisioningError as exc:
            logger.warning("langfuse org rename skipped (%s)", exc)
            return None
        try:
            # Our own row holds the old name; exclude it so a rename is never treated as a
            # collision with itself.
            resolved, existing = await self._available_org_name(
                conn, new_name, owned_org_ids | {org_id}
            )
            if existing is not None and existing != org_id:
                logger.warning(
                    "not renaming Langfuse org %s to %r -- another organization of ours "
                    "already has that name", org_id, new_name,
                )
                return None
            await conn.execute(
                "update organizations set name=$1, updated_at=$2 where id=$3",
                resolved, _now(), org_id,
            )
            return resolved
        except Exception:
            logger.warning("langfuse org rename failed for org=%s", org_id, exc_info=True)
            return None
        finally:
            await conn.close()

    async def teardown_org(self, *, org_id: str) -> dict:
        """Make an archived unit's organization inert: no access, no ingestion, hidden.

        WHY NOT `DELETE FROM organizations`. Postgres would cascade cleanly -- projects,
        memberships, invitations and api_keys all have ON DELETE CASCADE -- and it would
        still be the wrong operation, because TRACES DO NOT LIVE IN POSTGRES. They are in
        ClickHouse, and Langfuse removes them through an async cleanup job that its own
        delete endpoint schedules. Dropping the Postgres rows behind its back leaves trace
        data that is neither reachable nor deleted: worse than either outcome, and
        indefensible if somebody later has to demonstrate that a deletion happened.

        What this does instead reaches the same end state by Langfuse's own mechanisms:

          1. soft-delete every project (`deleted_at`) -- they vanish from the Langfuse UI
          2. delete their API keys -- ingestion stops immediately
          3. delete every membership and pending invitation -- nobody retains access, and
             no invitation can quietly restore it at next sign-in

        It is also REVERSIBLE, which matters: archiving a business unit is reversible in
        this product, so a hard delete would make a routine undoable action permanently
        destructive. `restore_org` brings it back -- minus the API keys, which are gone for
        good and get re-minted by the normal provisioning path.
        """
        self._require_config()
        try:
            conn = await self._connect()
        except LangfuseProvisioningError as exc:
            logger.warning("langfuse org teardown skipped for org=%s (%s)", org_id, exc)
            return {}
        counts = {"projects": 0, "api_keys": 0, "memberships": 0, "invitations": 0}
        try:
            now = _now()
            async with conn.transaction():
                project_ids = [
                    str(r["id"])
                    for r in await conn.fetch(
                        "select id from projects where org_id=$1 and deleted_at is null",
                        org_id,
                    )
                ]
                if project_ids:
                    await conn.execute(
                        "update projects set deleted_at=$1, updated_at=$1 "
                        "where id = any($2::text[])",
                        now, project_ids,
                    )
                    counts["projects"] = len(project_ids)
                    counts["api_keys"] = _affected(
                        await conn.execute(
                            "delete from api_keys where project_id = any($1::text[])",
                            project_ids,
                        )
                    )
                counts["memberships"] = _affected(
                    await conn.execute(
                        "delete from organization_memberships where org_id=$1", org_id
                    )
                )
                counts["invitations"] = _affected(
                    await conn.execute(
                        "delete from membership_invitations where org_id=$1", org_id
                    )
                )
            logger.info("Langfuse org %s torn down: %s", org_id, counts)
        except Exception:
            logger.warning("langfuse org teardown failed for org=%s", org_id, exc_info=True)
        finally:
            await conn.close()
        return counts

    async def restore_org(self, *, org_id: str) -> int:
        """Undo `teardown_org`'s soft-delete. Keys are NOT restored -- they were deleted.

        The caller re-mints them through the normal binding path, which is why
        `deactivate_binding` is the right partner to `teardown_org`: a deactivated binding
        makes the next traced run provision a fresh key pair instead of reusing a deleted one.
        """
        self._require_config()
        try:
            conn = await self._connect()
        except LangfuseProvisioningError as exc:
            logger.warning("langfuse org restore skipped for org=%s (%s)", org_id, exc)
            return 0
        try:
            restored = _affected(
                await conn.execute(
                    "update projects set deleted_at=null, updated_at=$1 "
                    "where org_id=$2 and deleted_at is not null",
                    _now(), org_id,
                )
            )
            logger.info("Langfuse org %s restored: %d project(s)", org_id, restored)
            return restored
        except Exception:
            logger.warning("langfuse org restore failed for org=%s", org_id, exc_info=True)
            return 0
        finally:
            await conn.close()

    async def org_access(self, *, org_id: str) -> dict:
        """Who can reach this organization today. For the reconciler and for tests.

        Returns {"members": {email: role}, "invitations": {email: role}}. The split
        matters: an invitation is a grant that has NOT taken effect -- that person cannot
        open Langfuse until they sign in, and if they cannot authenticate against the
        instance's Entra tenant at all, they never will. Reporting them as members would
        hide exactly that failure.
        """
        self._require_config()
        try:
            conn = await self._connect()
        except LangfuseProvisioningError as exc:
            logger.warning("langfuse org access read skipped for org=%s (%s)", org_id, exc)
            return {"members": {}, "invitations": {}}
        try:
            members = {
                str(r["email"]).lower(): str(r["role"])
                for r in await conn.fetch(
                    "select u.email, m.role from organization_memberships m "
                    "join users u on u.id = m.user_id where m.org_id=$1",
                    org_id,
                )
                if r["email"]
            }
            invitations = {
                str(r["email"]).lower(): str(r["org_role"])
                for r in await conn.fetch(
                    "select email, org_role from membership_invitations where org_id=$1",
                    org_id,
                )
            }
            return {"members": members, "invitations": invitations}
        except Exception:
            logger.warning("langfuse org access read failed for org=%s", org_id, exc_info=True)
            return {"members": {}, "invitations": {}}
        finally:
            await conn.close()

    async def rename_project(self, langfuse_project_id: str, new_name: str) -> bool:
        """Rename the Langfuse project behind a binding. Best-effort; never raises.

        Cosmetic but not pointless: the Langfuse UI lists projects by name, so a project
        renamed here and not there leaves whoever opens Langfuse looking for a name that
        no longer exists in this platform.
        """
        self._require_config()
        try:
            conn = await self._connect()
        except LangfuseProvisioningError as exc:
            logger.warning("langfuse rename skipped (%s)", exc)
            return False
        try:
            await conn.execute(
                "update projects set name=$1, updated_at=$2 where id=$3",
                new_name, _now(), langfuse_project_id,
            )
            return True
        except Exception:
            logger.warning(
                "langfuse rename failed for project=%s", langfuse_project_id, exc_info=True
            )
            return False
        finally:
            await conn.close()


async def verify_key_pair(public_key: str, secret_key: str, host: str) -> bool:
    """Prove a minted pair actually authenticates before anything relies on it.

    Provisioning writes hashes into a database; only a real request proves the salt was
    right. Without this a wrong LANGFUSE_SALT is discovered later, as traces silently
    failing to arrive.
    """
    import base64  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    try:
        # Generous: this runs once per project, and a cold instance answered in 20s+
        # during development. A timeout here would report a perfectly good key pair as
        # broken, which is a worse failure than waiting.
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{host.rstrip('/')}/api/public/traces",
                params={"limit": 1},
                headers={"Authorization": f"Basic {token}"},
            )
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Langfuse key verification failed: %s", type(exc).__name__)
        return False
