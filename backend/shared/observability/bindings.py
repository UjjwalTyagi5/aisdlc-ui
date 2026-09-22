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


def _configured_host() -> str:
    """The Langfuse instance this process talks to, normalised the way bindings compare it.

    A BINDING BELONGS TO ONE INSTANCE. Its keys exist only in the Langfuse database that
    minted them, and `client_for_binding` sends traces to the binding's OWN host. So when
    LANGFUSE_HOST moves to another instance — the remote one retired for a self-hosted one,
    or the reverse — a binding recorded against the old instance is not merely stale: used,
    it sends every trace to a server that is gone, or presents keys the new one has never
    heard of, and tracing fails open, so nothing says so. Every lookup below is therefore
    scoped to this host, and a binding for another instance is treated as no binding.
    Read at call time so a changed setting (or a test) is honoured without a re-import.
    """
    from config import env  # noqa: PLC0415

    return (env.LANGFUSE_HOST or "").strip().rstrip("/")


def _encrypt(value: str) -> str:
    from shared.services.secret_store import _fernet  # noqa: PLC0415

    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    from shared.services.secret_store import _fernet  # noqa: PLC0415

    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


async def load_binding(session, tenant_id: str, project_id: str) -> Optional[Binding]:
    """The active binding for one project ON THE CONFIGURED INSTANCE, or None. Never raises."""
    try:
        row = (
            await session.execute(
                text(
                    "select project_id, workspace_id, langfuse_org_id, langfuse_project_id, "
                    "langfuse_host, public_key_encrypted, secret_key_encrypted "
                    "from langfuse_bindings "
                    "where tenant_id = :t and project_id = :p and is_active = true "
                    "  and rtrim(langfuse_host, '/') = :h"
                ),
                {"t": str(tenant_id), "p": str(project_id), "h": _configured_host()},
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
                    # Only a binding from THIS instance is revived. One from another
                    # instance holds keys this Langfuse never minted.
                    text(
                        "update langfuse_bindings set is_active = true, updated_at = now() "
                        "where id = (select id from langfuse_bindings "
                        "            where tenant_id = cast(:t as uuid) "
                        "              and project_id = cast(:p as uuid) "
                        "              and is_active = false "
                        "              and rtrim(langfuse_host, '/') = :h "
                        "            order by updated_at desc limit 1) "
                        "returning id"
                    ),
                    {"t": str(tenant_id), "p": str(project_id), "h": _configured_host()},
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
                    "select p.display_name, p.workspace_id, w.display_name, "
                    "       w.langfuse_org_id "
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
    # The organization the unit already owns (0058), created when the unit was created.
    # Passing it is what makes this reuse that organization instead of searching by name —
    # and searching by name on a shared instance is how a unit ends up adopting the
    # sibling product's organization. None means the unit predates 0058 or its eager
    # provisioning failed; `_ensure_org` then creates one, collision-safely.
    unit_org_id = str(row[3]) if row[3] else None

    from shared.observability.org_sync import owned_org_ids  # noqa: PLC0415
    from shared.observability.provisioning import (  # noqa: PLC0415
        LangfuseProvisioner,
        LangfuseProvisioningError,
    )

    try:
        owned = await owned_org_ids(session)
        # Every Langfuse project another live binding already claims, EXCLUDING this
        # project's own. Excluding it is what keeps restore-after-archive working: that
        # path must re-adopt the Langfuse project it used to write to, or its history
        # becomes unreachable. Including everyone else's is what stops two SDLC projects
        # that happen to share a display name from sharing one Langfuse project — and
        # therefore each other's prompts and completions.
        bound = frozenset(
            str(r[0])
            for r in (
                await session.execute(
                    text(
                        "select langfuse_project_id from langfuse_bindings "
                        "where tenant_id = cast(:t as uuid) and is_active = true "
                        "  and project_id <> cast(:p as uuid) "
                        "  and rtrim(langfuse_host, '/') = :h"
                    ),
                    {"t": str(tenant_id), "p": str(project_id), "h": _configured_host()},
                )
            ).all()
        )
        result = await LangfuseProvisioner().provision(
            unit_name=unit_name,
            project_name=project_name,
            org_id=unit_org_id,
            owned_org_ids=owned,
            bound_project_ids=bound,
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

    # RECORD A REPLACED ORGANIZATION ON THE UNIT. When the unit's recorded organization
    # is gone — a Langfuse rebuilt, or LANGFUSE_HOST moved to another instance —
    # provisioning makes a replacement, and `_ensure_org` leaves recording it to the
    # caller. Nothing did. The unit went on naming the vanished id, so its NEXT project
    # hit the same "no longer exists" path, found the replacement it did not recognise as
    # its own (ownership is read from that same stale column), refused to adopt it, and
    # made "PAYMENTS (PWC)" — one Langfuse organization per project instead of per unit,
    # and org_sync's memberships landed on only one of them.
    if result.langfuse_org_id and result.langfuse_org_id != unit_org_id:
        try:
            from shared.observability.org_sync import _persist_org  # noqa: PLC0415

            await _persist_org(
                str(tenant_id), workspace_id, result.langfuse_org_id,
                result.langfuse_org_name or unit_name,
            )
            logger.info(
                "langfuse organization for unit=%s recorded as %s (was %s)",
                workspace_id, result.langfuse_org_id, unit_org_id,
            )
        except Exception:
            logger.warning(
                "langfuse organization %s could not be recorded on unit=%s — the unit's "
                "next project will provision another", result.langfuse_org_id,
                workspace_id, exc_info=True,
            )

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
            # RETIRE, THEN INSERT. A project can hold one active binding (the partial
            # unique index), so a binding still active for ANOTHER instance would make the
            # insert below a silent `on conflict do nothing` and leave this project
            # tracing nowhere. Deactivated, not deleted: pointing LANGFUSE_HOST back at
            # that instance revives it, history and keys intact.
            await write_session.execute(
                text(
                    "update langfuse_bindings set is_active = false, updated_at = now() "
                    "where tenant_id = cast(:t as uuid) and project_id = cast(:p as uuid) "
                    "  and is_active = true and rtrim(langfuse_host, '/') <> :h"
                ),
                {"t": str(tenant_id), "p": str(project_id), "h": _configured_host()},
            )
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


# WHO the run belongs to, alongside WHERE it goes. The LangChain handler receives this
# as metadata and writes it onto the trace; a direct-SDK caller has no handler, so its
# traces arrived with userId=None, tags=[] and no session -- present on the Traces page
# but unable to answer "who ran this", which is most of what the page is for.
_CURRENT_TRACE_ATTRS: contextvars.ContextVar = contextvars.ContextVar(
    "langfuse_current_trace_attrs", default=None
)


def set_current_trace_attrs(attrs: dict | None) -> None:
    """Bind this run's trace identity (user, session, tags). Called by agent_trace."""
    _CURRENT_TRACE_ATTRS.set(attrs)


def get_current_trace_attrs() -> dict | None:
    """This run's trace identity, or None when untraced."""
    try:
        return _CURRENT_TRACE_ATTRS.get()
    except Exception:
        return None
