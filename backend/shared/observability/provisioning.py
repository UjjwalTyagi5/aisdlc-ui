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
    {"organizations", "organization_memberships", "projects", "project_memberships", "api_keys"}
)

# Columns this module depends on. Checked before the first write so a Langfuse upgrade
# that renames one fails with a readable message instead of a half-created project.
_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "organizations": {"id", "name"},
    "projects": {"id", "name", "org_id"},
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

    async def _ensure_org(self, conn, name: str) -> tuple[str, bool]:
        existing = await conn.fetchval("select id from organizations where name=$1", name)
        if existing:
            return str(existing), False
        org_id = str(uuid.uuid4())
        now = _now()
        await conn.execute(
            "insert into organizations (id,name,created_at,updated_at) values ($1,$2,$3,$4)",
            org_id, name, now, now,
        )
        return org_id, True

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

    async def _bootstrap_user_ids(self, conn) -> list[str]:
        """Langfuse user ids to make owners. Comma-separated in config; may be empty.

        MORE THAN ONE, DELIBERATELY. Langfuse lists only the organizations a user
        belongs to, so provisioning under a single service account produces projects
        that collect traces nobody can open — indistinguishable, from the UI, from
        provisioning having failed. Every address that should be able to look belongs
        here.
        """
        emails = [e.strip() for e in (self.bootstrap_email or "").split(",") if e.strip()]
        if not emails:
            return []
        rows = await conn.fetch("select id, email from users where email = any($1::text[])", emails)
        found = {r["email"]: str(r["id"]) for r in rows}
        for missing in [e for e in emails if e not in found]:
            logger.warning(
                "LANGFUSE_BOOTSTRAP_USER_EMAIL lists %s, which is not a Langfuse user — "
                "they must sign in once before they can be granted access",
                missing,
            )
        return list(found.values())

    async def _ensure_memberships(self, conn, org_id: str, project_id: str, user_id: str) -> None:
        """Make the bootstrap user an owner, so the project is visible in the UI.

        Best-effort: traces do not depend on it, and the membership tables carry enum
        columns whose values differ across Langfuse versions. A failure here must not
        cost us a working key pair.
        """
        try:
            om_id = await conn.fetchval(
                "select id from organization_memberships where org_id=$1 and user_id=$2",
                org_id, user_id,
            )
            now = _now()
            if not om_id:
                om_id = str(uuid.uuid4())
                await conn.execute(
                    "insert into organization_memberships (id,org_id,user_id,role,created_at,updated_at) "
                    "values ($1,$2,$3,'OWNER',$4,$5)",
                    om_id, org_id, user_id, now, now,
                )
            exists = await conn.fetchval(
                "select 1 from project_memberships where project_id=$1 and user_id=$2",
                project_id, user_id,
            )
            if not exists:
                await conn.execute(
                    "insert into project_memberships "
                    "(project_id,user_id,role,created_at,updated_at,org_membership_id) "
                    "values ($1,$2,'OWNER',$3,$4,$5)",
                    project_id, user_id, now, now, str(om_id),
                )
        except Exception as exc:
            logger.warning(
                "Langfuse membership not created for project=%s (%s) — traces will still "
                "arrive, but the project will not be visible in the Langfuse UI",
                project_id, type(exc).__name__,
            )

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

    async def provision(self, *, unit_name: str, project_name: str) -> ProvisionedProject:
        """Ensure a Langfuse org for the unit and a project inside it, and mint a key.

        `unit_name` is the business unit — it becomes the Langfuse organization.
        `project_name` is the SDLC project — it becomes the Langfuse project.

        Runs in one transaction: a failure part-way leaves no organization without its
        project, and no project without its key.
        """
        self._require_config()
        conn = await self._connect()
        try:
            await self._assert_schema(conn)
            async with conn.transaction():
                org_id, created_org = await self._ensure_org(conn, unit_name)
                project_id, created_project = await self._ensure_project(
                    conn, org_id, project_name
                )
                for user_id in await self._bootstrap_user_ids(conn):
                    await self._ensure_memberships(conn, org_id, project_id, user_id)
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
