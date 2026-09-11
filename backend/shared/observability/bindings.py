"""Resolve which Langfuse project an SDLC project writes to, and hold its credentials.

This is the layer between `langfuse_bindings` rows and everything that needs a Langfuse
client. It answers two questions:

    write path — "which client does THIS run's project write to?"
    read  path — "which clients cover the projects this CALLER may read?"

Both are the same lookup from opposite ends, which is the point: a caller can only ever
receive credentials for projects `visible_project_ids` already allowed, so the isolation
that used to depend on injecting the right tag now depends on which key pair is even
reachable. A missing filter returns nothing rather than another unit's prompts.

PROVISIONING IS LAZY, NOT EAGER. A binding is created the first time a project needs one
rather than when the project is created, for three reasons: projects already exist that
have no binding, project creation should not fail because Langfuse is unreachable, and a
project that never runs an agent never needs a Langfuse project. The cost is a slower
first traced turn for a project.

FAIL-OPEN, LIKE EVERY OTHER OBSERVABILITY PATH HERE. If Langfuse is down or unreachable
the caller gets None and tracing no-ops. Losing a trace is not a reason to fail an agent
run — the same posture `get_langfuse_client` has always taken.

KEYS ARE DECRYPTED ONLY IN MEMORY, and the client cache is bounded so a long-lived
process holding many projects does not grow without limit.
"""
from __future__ import annotations

import contextvars
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Clients are cheap to hold and expensive to build (each opens its own HTTP pool and
# OTEL exporter), but a process serving many projects should not accumulate them
# forever. Oldest-out once full.
_CLIENT_CACHE_MAX = 64
_client_cache: "OrderedDict[str, Any]" = OrderedDict()


@dataclass(frozen=True)
class Binding:
    """One SDLC project's Langfuse coordinates, with the keys already decrypted."""

    project_id: str
    workspace_id: str
    langfuse_org_id: str
    langfuse_project_id: str
    langfuse_host: str
    public_key: str
    secret_key: str


def _encrypt(value: str) -> str:
    from shared.services.secret_store import _fernet  # noqa: PLC0415

    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    from shared.services.secret_store import _fernet  # noqa: PLC0415

    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


async def load_binding(session, tenant_id: str, project_id: str) -> Optional[Binding]:
    """The active binding for one project, or None. Never raises."""
    try:
        row = (
            await session.execute(
                text(
                    "select project_id, workspace_id, langfuse_org_id, langfuse_project_id, "
                    "langfuse_host, public_key_encrypted, secret_key_encrypted "
                    "from langfuse_bindings "
                    "where tenant_id = :t and project_id = :p and is_active = true"
                ),
                {"t": str(tenant_id), "p": str(project_id)},
            )
        ).first()
    except Exception:
        logger.warning("langfuse binding lookup failed", exc_info=True)
        return None
    if row is None:
        return None
    try:
        return Binding(
            project_id=str(row[0]),
            workspace_id=str(row[1]),
            langfuse_org_id=row[2],
            langfuse_project_id=row[3],
            langfuse_host=row[4],
            public_key=_decrypt(row[5]),
            secret_key=_decrypt(row[6]),
        )
    except Exception:
        # A binding whose keys will not decrypt is worse than no binding: it would send
        # traces nowhere while looking configured. SECRET_STORE_KEY has probably changed.
        logger.warning(
            "langfuse binding for project=%s could not be decrypted — treating as "
            "unbound; SECRET_STORE_KEY may have rotated",
            project_id,
        )
        return None


async def load_bindings(session, tenant_id: str, project_ids: list[str]) -> list[Binding]:
    """Active bindings for several projects. The read path's entry point."""
    out: list[Binding] = []
    for pid in project_ids:
        b = await load_binding(session, tenant_id, pid)
        if b is not None:
            out.append(b)
    return out


async def ensure_binding(
    session, *, tenant_id: str, project_id: str
) -> Optional[Binding]:
    """Return this project's binding, provisioning a Langfuse project if it has none.

    Idempotent and safe to call on every traced turn: the common path is a single
    indexed SELECT. Returns None — rather than raising — when Langfuse is unreachable or
    provisioning is not configured, so an agent run is never lost to an observability
    problem.
    """
    existing = await load_binding(session, tenant_id, project_id)
    if existing is not None:
        return existing

    # REACTIVATE BEFORE PROVISIONING. A project that was bound and then archived still
    # has its row, just inactive, and its traces are still in that Langfuse project.
    # Provisioning a fresh one would strand the history and leave two projects for one.
    #
    # This must not be a name lookup. Provisioning matches an existing Langfuse project
    # by name, so a project renamed while archived would not be found and a DUPLICATE
    # would be created — observed exactly that before this existed. The binding row is
    # the durable identity; the name is not.
    try:
        from shared.db import get_db_session_for_tenant  # noqa: PLC0415

        async with get_db_session_for_tenant(str(tenant_id)) as revive:
            revived = (
                await revive.execute(
                    text(
                        "update langfuse_bindings set is_active = true, updated_at = now() "
                        "where id = (select id from langfuse_bindings "
                        "            where tenant_id = cast(:t as uuid) "
                        "              and project_id = cast(:p as uuid) "
                        "              and is_active = false "
                        "            order by updated_at desc limit 1) "
                        "returning id"
                    ),
                    {"t": str(tenant_id), "p": str(project_id)},
                )
            ).first()
        if revived is not None:
            logger.info("langfuse binding reactivated for project=%s", project_id)
            return await load_binding(session, tenant_id, project_id)
    except Exception:
        logger.warning(
            "langfuse binding reactivation failed for project=%s", project_id, exc_info=True
        )

    # Names come from OUR records, so the Langfuse organization and project are
    # recognisable to a human opening the UI rather than a pair of uuids.
    try:
        row = (
            await session.execute(
                text(
                    "select p.display_name, p.workspace_id, w.display_name "
                    "from projects p join workspaces w on w.id = p.workspace_id "
                    "where p.id = :p and p.tenant_id = :t"
                ),
                {"p": str(project_id), "t": str(tenant_id)},
            )
        ).first()
    except Exception:
        logger.warning("langfuse binding: project lookup failed", exc_info=True)
        return None
    if row is None:
        logger.warning(
            "langfuse binding: project=%s not found in tenant=%s", project_id, tenant_id
        )
        return None

    project_name, workspace_id, unit_name = str(row[0]), str(row[1]), str(row[2])

    from shared.observability.provisioning import (  # noqa: PLC0415
        LangfuseProvisioner,
        LangfuseProvisioningError,
    )

    try:
        result = await LangfuseProvisioner().provision(
            unit_name=unit_name, project_name=project_name
        )
    except LangfuseProvisioningError as exc:
        logger.warning(
            "langfuse provisioning unavailable for project=%s: %s", project_id, exc
        )
        return None
    except Exception:
        logger.warning(
            "langfuse provisioning failed for project=%s", project_id, exc_info=True
        )
        return None

    # A SEPARATE SESSION, DELIBERATELY. Two reasons, and the second is a real bug this
    # had: committing here would commit whatever else the caller has in flight — this is
    # called mid-request from an agent turn — and `get_db_session_for_tenant` sets
    # `app.current_tenant_id` with SET LOCAL, which is transaction-scoped. Committing on
    # the caller's session therefore DROPS the GUC, and every later RLS query in that
    # request quietly returns zero rows instead of failing. Owning the transaction keeps
    # the binding durable without touching the caller's.
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    try:
        async with get_db_session_for_tenant(str(tenant_id)) as write_session:
            await write_session.execute(
                text(
                    "insert into langfuse_bindings "
                "(id, tenant_id, workspace_id, project_id, langfuse_org_id, "
                " langfuse_project_id, langfuse_project_name, langfuse_host, "
                    " public_key_encrypted, secret_key_encrypted) "
                    "values (gen_random_uuid(), :t, :w, :p, :org, :lp, :lpn, :host, :pk, :sk) "
                    # Two concurrent first-turns on one project both provision; the
                    # partial unique index rejects the loser, which re-reads the winner.
                    "on conflict do nothing"
                ),
                {
                    "t": str(tenant_id),
                    "w": workspace_id,
                    "p": str(project_id),
                    "org": result.langfuse_org_id,
                    "lp": result.langfuse_project_id,
                    "lpn": result.langfuse_project_name,
                    "host": result.host,
                    "pk": _encrypt(result.public_key),
                    "sk": _encrypt(result.secret_key),
                },
            )
    except Exception:
        logger.warning(
            "langfuse binding for project=%s could not be persisted", project_id, exc_info=True
        )
        # The Langfuse project exists either way; re-reading covers the race above.
        return await load_binding(session, tenant_id, project_id)

    logger.info(
        "langfuse binding created: project=%s -> %s/%s",
        project_id, unit_name, result.langfuse_project_name,
    )
    return await load_binding(session, tenant_id, project_id)


def client_for_binding(binding: Binding):
    """A Langfuse SDK client for one binding, memoised. None if the SDK is unavailable.

    Keyed on the Langfuse project id, so two SDLC projects can never share a client and
    therefore can never write into each other's project.
    """
    cached = _client_cache.get(binding.langfuse_project_id)
    if cached is not None:
        _client_cache.move_to_end(binding.langfuse_project_id)
        return cached
    try:
        from langfuse import Langfuse  # noqa: PLC0415

        from shared.observability.redaction import mask_sensitive  # noqa: PLC0415

        client = Langfuse(
            public_key=binding.public_key,
            secret_key=binding.secret_key,
            host=binding.langfuse_host,
            # Same write-time redaction the single-project client has (PRD §34.8).
            # Per-project clients must not quietly lose it.
            mask=mask_sensitive,
        )
    except Exception:
        logger.warning(
            "langfuse client construction failed for project=%s",
            binding.langfuse_project_id, exc_info=True,
        )
        return None

    _client_cache[binding.langfuse_project_id] = client
    while len(_client_cache) > _CLIENT_CACHE_MAX:
        _client_cache.popitem(last=False)
    return client


def clear_client_cache() -> None:
    """Drop memoised clients. For tests, and after a credential rotation."""
    _client_cache.clear()


async def sync_project_rename(session, *, tenant_id: str, project_id: str, new_name: str) -> None:
    """Carry an SDLC project rename through to its Langfuse project.

    Best-effort and deliberately silent on failure: a rename must not fail because an
    observability system is unreachable. The consequence of skipping it is cosmetic —
    Langfuse keeps the old name — and the next successful rename corrects it.
    """
    binding = await load_binding(session, tenant_id, project_id)
    if binding is None:
        return
    try:
        from shared.observability.provisioning import LangfuseProvisioner  # noqa: PLC0415

        ok = await LangfuseProvisioner().rename_project(
            binding.langfuse_project_id, new_name
        )
    except Exception:
        logger.warning("langfuse rename failed for project=%s", project_id, exc_info=True)
        return
    if not ok:
        return
    try:
        from shared.db import get_db_session_for_tenant  # noqa: PLC0415

        async with get_db_session_for_tenant(str(tenant_id)) as s:
            await s.execute(
                text(
                    "update langfuse_bindings set langfuse_project_name = :n, "
                    "updated_at = now() where tenant_id = cast(:t as uuid) "
                    "and project_id = cast(:p as uuid) and is_active = true"
                ),
                {"n": new_name, "t": str(tenant_id), "p": str(project_id)},
            )
    except Exception:
        logger.warning(
            "langfuse binding name not updated for project=%s", project_id, exc_info=True
        )


async def deactivate_binding(session, *, tenant_id: str, project_id: str) -> None:
    """Mark a project's binding inactive when the project is archived.

    THE LANGFUSE PROJECT IS NOT DELETED, and that is the point. Archiving here is a soft
    delete with a `/restore` counterpart, so destroying the traces would be a harder
    action than the one the user took — and it would break PRD §17's auditability and
    §34.10's "retention expiry is the only way an audit record ever leaves". Only the
    retention policy set at provisioning time ever removes a trace.

    Deactivating stops new traces (agent_trace finds no binding, so the project is not
    traced) while leaving the history readable if the project is restored: `restore`
    calls ensure_binding, which finds the existing Langfuse project by name and adopts
    it rather than creating a second one.
    """
    try:
        from shared.db import get_db_session_for_tenant  # noqa: PLC0415

        async with get_db_session_for_tenant(str(tenant_id)) as s:
            await s.execute(
                text(
                    "update langfuse_bindings set is_active = false, updated_at = now() "
                    "where tenant_id = cast(:t as uuid) and project_id = cast(:p as uuid) "
                    "and is_active = true"
                ),
                {"t": str(tenant_id), "p": str(project_id)},
            )
    except Exception:
        logger.warning(
            "langfuse binding not deactivated for project=%s", project_id, exc_info=True
        )


# The Langfuse client for the run currently executing, so code that is NOT LangChain can
# still trace to the right project. The LangChain handler carries this implicitly; agents
# calling a provider SDK directly (the monitoring agent uses litellm) have nothing to
# carry it, and a module-level client would send every project's traces to whichever one
# happened to be configured first.
_CURRENT_CLIENT: contextvars.ContextVar = contextvars.ContextVar(
    "langfuse_current_client", default=None
)


def set_current_client(client: Any | None) -> None:
    """Bind a Langfuse client to this run's context. Called by agent_trace."""
    _CURRENT_CLIENT.set(client)


def get_current_client():
    """The Langfuse client for this run, or None when untraced."""
    try:
        return _CURRENT_CLIENT.get()
    except Exception:
        return None
