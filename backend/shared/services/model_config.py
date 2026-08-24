"""Model Provider (BYOK) configuration service. Owns the model_providers /
model_offerings RLS tables + key material via secret_store. Verification probes
the provider through LiteLLM. Keys are never returned to callers or logged.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Optional

from sqlalchemy import text

from shared.db import get_db_session_for_tenant
from shared.services import secret_store
from shared.services.model_catalog import is_known_provider, is_valid_model, price_for

logger = logging.getLogger(__name__)


def _is_valid_uuid(value: str) -> bool:
    """True when `value` is a syntactically valid UUID — guards every place a caller-
    supplied id is bound against a UUID-typed column, so a malformed id (e.g. a
    not-yet-migrated fixture identity) fails predictably in Python instead of as an
    unhandled asyncpg.DataError partway through a query."""
    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class InvalidModelError(Exception):
    """A requested model is not valid for the given provider (catalog check)."""


class ProviderNotFoundError(Exception):
    """No provider with that id for this tenant."""


class OfferingNotFoundError(Exception):
    """No offering with that id for this tenant."""


class DuplicateProviderNameError(Exception):
    """Another provider connection in this tenant already uses that display name.
    Names must be unique per tenant so a connection (= one API key) is identifiable
    everywhere a model is chosen."""


async def _name_exists(s, tenant_id: str, display_name: str, exclude_id: str | None = None) -> bool:
    q = ("SELECT 1 FROM model_providers "
         "WHERE tenant_id = :t AND lower(display_name) = lower(:n)")
    params: dict = {"t": tenant_id, "n": display_name}
    if exclude_id:
        q += " AND id <> :ex"
        params["ex"] = exclude_id
    return (await s.execute(text(q), params)).first() is not None


def _secret_ref(provider_id: str) -> str:
    return f"model-{provider_id}"


async def _provider_row(s, provider_id: str):
    return (await s.execute(
        text("SELECT id, provider, display_name, secret_ref, status, last_verified_at, created_at, "
             "workspace_id, project_id, api_base, is_custom, max_cost_per_call_usd, "
             "approval_status, approval_decided_by, approval_decided_at, approval_reason "
             "FROM model_providers WHERE id = :id"),
        {"id": provider_id},
    )).first()


def _price(v) -> float | None:
    return float(v) if v is not None else None


async def _offerings_for(s, provider_id: str) -> list[dict]:
    rows = (await s.execute(
        text("SELECT id, provider_id, model_id, enabled, is_default, "
             "input_price_per_million, output_price_per_million, "
             "rpm_limit, tpm_limit, cost_limit_usd FROM model_offerings "
             "WHERE provider_id = :pid ORDER BY model_id"),
        {"pid": provider_id},
    )).fetchall()
    return [
        {"id": str(r.id), "provider_id": str(r.provider_id), "model_id": r.model_id,
         "enabled": bool(r.enabled), "is_default": bool(r.is_default),
         "input_price_per_million": _price(r.input_price_per_million),
         "output_price_per_million": _price(r.output_price_per_million),
         "rpm_limit": r.rpm_limit, "tpm_limit": r.tpm_limit,
         "cost_limit_usd": _price(r.cost_limit_usd)}
        for r in rows
    ]


def _provider_dict(row, offerings: list[dict]) -> dict:
    approval_decided_at = getattr(row, "approval_decided_at", None)
    return {
        "id": str(row.id), "provider": row.provider, "display_name": row.display_name,
        "secret_ref": row.secret_ref, "status": row.status,
        "api_base": getattr(row, "api_base", None),
        "is_custom": bool(getattr(row, "is_custom", False)),
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "offerings": offerings,
        "workspace_id": str(row.workspace_id) if getattr(row, "workspace_id", None) else None,
        "project_id": str(row.project_id) if getattr(row, "project_id", None) else None,
        "max_cost_per_call_usd": _price(getattr(row, "max_cost_per_call_usd", None)),
        # Synthetic — there is no has_key column; a connection has a usable key iff
        # secret_ref is non-null (it's registered with no key, or the key was removed).
        "has_key": row.secret_ref is not None,
        "approval_status": getattr(row, "approval_status", None) or "active",
        "approval_decided_by": getattr(row, "approval_decided_by", None),
        "approval_decided_at": approval_decided_at.isoformat() if approval_decided_at else None,
        "approval_reason": getattr(row, "approval_reason", None),
    }


async def create_provider(
    tenant_id: str, *, provider: str, display_name: str, api_key: str | None,
    created_by: str, models: list[dict] | None = None,
    enabled_models: list[str] | None = None, api_base: str | None = None,
    workspace_id: str | None = None, max_cost_per_call_usd: float | None = None,
    project_id: str | None = None,
) -> dict:
    """Create a provider connection + its enabled model offerings.

    `api_key` may be None/empty — the connection is registered with no secret (hasKey via
    a null secret_ref) so its models can be granted centrally while a Business Unit or
    project supplies its own key later (spec §2.3). `workspace_id` scopes the connection to
    that BU (NULL = org-wide). `project_id` scopes it one level deeper still — a single
    project's own key (PRD §371/§1640: only reachable via the /model/project-providers
    router, which validates the project's BU actually allows it before calling this).
    When `project_id` is set the caller (the router) MUST also pass the project's own
    `workspace_id` — this function does not look it up, so a project-scoped connection is
    never silently misfiled under the wrong BU.

    Pass either `models` (rich specs: {model_id, input_price_per_million?,
    output_price_per_million?}) or `enabled_models` (bare ids, back-compat).
    Providers outside the curated catalog are treated as CUSTOM: catalog
    validation is skipped (any LiteLLM provider/model is allowed), but pricing on
    every model is MANDATORY so Cost/Langfuse can attribute spend. Onboarding is
    gated only by model:manage RBAC at the router.
    """
    display_name = (display_name or "").strip()
    provider = (provider or "").strip()
    api_base = (api_base or "").strip() or None
    api_key = (api_key or "").strip() or None
    if workspace_id and not _is_valid_uuid(workspace_id):
        # Silently dropping to None here would widen a BU-scoped onboarding to org-wide
        # (every business unit could suddenly see it) — the opposite of fail-safe. Reject
        # instead, unlike the read path in list_providers where the same malformed id can
        # safely fall back to "show nothing BU-specific".
        raise ValueError(f"workspace_id is not a valid identifier: {workspace_id!r}")
    if project_id and not _is_valid_uuid(project_id):
        raise ValueError(f"project_id is not a valid identifier: {project_id!r}")
    if project_id and not workspace_id:
        raise ValueError("project_id requires workspace_id (the project's own BU)")
    if models is None:
        models = [{"model_id": m} for m in (enabled_models or [])]
    if not provider or not display_name:
        raise ValueError("provider and display_name are required")
    if not models:
        raise ValueError("at least one model is required")

    is_custom = not is_known_provider(provider)
    for m in models:
        model_id = (m.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("each model needs a model_id")
        known = is_valid_model(provider, model_id)
        in_p = m.get("input_price_per_million")
        out_p = m.get("output_price_per_million")
        if in_p is None or out_p is None:
            c_in, c_out = price_for(provider, model_id)
            in_p = in_p if in_p is not None else c_in
            out_p = out_p if out_p is not None else c_out
        if not known and (in_p is None or out_p is None):
            raise InvalidModelError(
                f"model {model_id!r} is not in the catalog for provider {provider!r} — "
                f"provide input and output pricing (USD per 1M tokens) to onboard it")
        m["input_price_per_million"] = in_p
        m["output_price_per_million"] = out_p

    async with get_db_session_for_tenant(tenant_id) as s:
        if await _name_exists(s, tenant_id, display_name):
            raise DuplicateProviderNameError(
                f"A provider connection named {display_name!r} already exists")

    provider_id = str(_uuid.uuid4())
    secret_ref = _secret_ref(provider_id) if api_key else None
    if api_key:
        await secret_store.put_secret(tenant_id, secret_ref, api_key)
    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            await s.execute(
                text("INSERT INTO model_providers "
                     "(id, tenant_id, workspace_id, project_id, provider, display_name, secret_ref, api_base, is_custom, "
                     "status, created_by, max_cost_per_call_usd) "
                     "VALUES (:id, :t, :w, :proj, :p, :n, :ref, :ab, :cust, 'unverified', :by, :mcpc)"),
                {"id": provider_id, "t": tenant_id, "w": workspace_id, "proj": project_id,
                 "p": provider, "n": display_name,
                 "ref": secret_ref, "ab": api_base, "cust": is_custom, "by": created_by,
                 "mcpc": max_cost_per_call_usd},
            )
            for m in models:
                await s.execute(
                    text("INSERT INTO model_offerings "
                         "(id, tenant_id, provider_id, model_id, enabled, is_default, "
                         "input_price_per_million, output_price_per_million, "
                         "rpm_limit, tpm_limit, cost_limit_usd) "
                         "VALUES (:id, :t, :pid, :m, true, false, :ip, :op, :rpm, :tpm, :cost)"),
                    {"id": str(_uuid.uuid4()), "t": tenant_id, "pid": provider_id,
                     "m": (m.get("model_id") or "").strip(),
                     "ip": m.get("input_price_per_million"),
                     "op": m.get("output_price_per_million"),
                     "rpm": m.get("rpm_limit"), "tpm": m.get("tpm_limit"),
                     "cost": m.get("cost_limit_usd")},
                )
            row = await _provider_row(s, provider_id)
            offerings = await _offerings_for(s, provider_id)
    except Exception:
        if secret_ref:
            await secret_store.delete_secret(tenant_id, secret_ref)
        raise
    logger.info("model provider created tenant=%s provider=%s id=%s custom=%s workspace=%s",
                tenant_id, provider, provider_id, is_custom, workspace_id)
    return _provider_dict(row, offerings)


async def list_providers(
    tenant_id: str, workspace_id: str | None = None, scope: str | None = None,
    project_id: str | None = None,
) -> list[dict]:
    """scope="all" -> every connection (org-wide + every BU's) — the Org Admin's view.
    A bare workspace_id -> org-wide connections + that one BU's own — a BU/Project Admin's
    view. Neither -> org-wide only (legacy default, unchanged for existing callers).
    `project_id` -> ONLY that project's own connections (a Project Admin's "your own
    keys" list) — takes precedence over workspace_id/scope when given, since it's a
    strictly narrower, self-contained view.

    workspace_id is compared against a UUID column: a caller passing a malformed value
    (e.g. a BU identity that hasn't been migrated to a real backend id yet) must not crash
    the request. Falling back to the org-wide-only view is safe here — it never surfaces a
    BU-specific connection, only withholds one — unlike defaulting the WRITE path in
    create_provider, where the same fallback would incorrectly widen a scoped onboarding to
    org-wide. Validated in Python (not via a failed-then-retried query) so a malformed id
    never leaves the session's transaction in an aborted state."""
    if project_id and not _is_valid_uuid(project_id):
        project_id = None
    if workspace_id and scope != "all" and not _is_valid_uuid(workspace_id):
        workspace_id = None

    async with get_db_session_for_tenant(tenant_id) as s:
        if project_id:
            where = "tenant_id = :t AND project_id = :proj"
            params: dict = {"t": tenant_id, "proj": project_id}
        elif scope == "all":
            where = "tenant_id = :t"
            params = {"t": tenant_id}
        elif workspace_id:
            where = "tenant_id = :t AND (workspace_id IS NULL OR workspace_id = :w)"
            params = {"t": tenant_id, "w": workspace_id}
        else:
            where = "tenant_id = :t AND workspace_id IS NULL"
            params = {"t": tenant_id}
        prows = (await s.execute(
            text(f"SELECT id, provider, display_name, secret_ref, status, last_verified_at, created_at, "
                 f"api_base, is_custom, workspace_id, project_id, max_cost_per_call_usd, "
                 f"approval_status, approval_decided_by, approval_decided_at, approval_reason "
                 f"FROM model_providers WHERE {where} ORDER BY created_at"),
            params,
        )).fetchall()
        out = []
        for r in prows:
            out.append(_provider_dict(r, await _offerings_for(s, str(r.id))))
    return out


async def _probe_model(provider: str, model: str, api_key: str, api_base: str | None = None) -> bool:
    """1-token live completion to validate the key. Returns True on success.
    Wrapped so tests can monkeypatch it without network. Never logs the key.
    `api_base` targets a custom/self-hosted/OpenAI-compatible endpoint."""
    import litellm
    try:
        kwargs: dict = dict(
            model=model,
            custom_llm_provider=provider,
            api_key=api_key,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        if api_base:
            kwargs["api_base"] = api_base
        await litellm.acompletion(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 — any provider/auth error => invalid
        # Log the provider's actual message (not just the class) so verify failures are
        # diagnosable. LiteLLM/Anthropic error text carries the reason (bad model / key /
        # param) and never echoes the api_key.
        logger.warning(
            "model verify failed provider=%s model=%s err=%s: %s",
            provider, model, type(exc).__name__, str(exc)[:600],
        )
        return False


async def probe_provider(
    provider: str, api_key: str, api_base: str | None = None, model: str | None = None,
) -> dict:
    """Stateless pre-save credential check — the BU Admin's "Test" button (spec §5,
    Task 10), fired before `create_provider` has ever been called. No `model_providers`
    row exists yet to update, unlike `verify_provider`: this touches no database at all,
    it only exercises the same `_probe_model` live-completion call directly.

    `model` should be one of the models the caller is about to onboard (the dialog
    passes whichever one was picked first); when omitted, falls back to the catalog's
    own first model for `provider`, mirroring `verify_provider`'s own fallback so a
    caller mid-onboarding with no model chosen yet still gets a real answer rather than
    a 422.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return {"status": "invalid"}
    probe_model = (model or "").strip()
    if not probe_model:
        from shared.services.model_catalog import models_for_provider
        models = models_for_provider(provider)
        probe_model = models[0]["model_id"] if models else ""
    if not probe_model:
        raise InvalidModelError(f"no model to probe for provider {provider!r}")
    ok = await _probe_model(provider, probe_model, api_key, (api_base or "").strip() or None)
    return {"status": "valid" if ok else "invalid"}


async def verify_provider(tenant_id: str, provider_id: str) -> dict:
    async with get_db_session_for_tenant(tenant_id) as s:
        row = await _provider_row(s, provider_id)
        if row is None:
            raise ProviderNotFoundError(provider_id)
        # pick an ENABLED offering's model to probe; fall back to first catalog model.
        # Must filter enabled=true — disabled offerings (e.g. a retired model the user
        # swapped away from) are kept as rows for audit/default history, and probing one
        # would 404 and wrongly mark the whole provider invalid.
        model = (await s.execute(
            text("SELECT model_id FROM model_offerings WHERE provider_id = :pid "
                 "AND enabled = true ORDER BY model_id LIMIT 1"),
            {"pid": provider_id},
        )).scalar()
    if model is None:
        from shared.services.model_catalog import models_for_provider
        models = models_for_provider(row.provider)
        model = models[0]["model_id"] if models else None
    if model is None:
        raise InvalidModelError(f"no model to probe for provider {row.provider}")

    api_key = await secret_store.get_secret(tenant_id, row.secret_ref)
    api_base = getattr(row, "api_base", None)
    if api_base:
        ok = bool(api_key) and await _probe_model(row.provider, model, api_key, api_base)
    else:
        ok = bool(api_key) and await _probe_model(row.provider, model, api_key)
    status = "valid" if ok else "invalid"
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text("UPDATE model_providers SET status = :st, last_verified_at = now() WHERE id = :id"),
            {"st": status, "id": provider_id},
        )
    logger.info("model provider verify tenant=%s id=%s status=%s", tenant_id, provider_id, status)
    return {"id": provider_id, "status": status}


async def update_provider(
    tenant_id: str, provider_id: str, *,
    display_name: str | None = None, enabled_models: list[str] | None = None,
    max_cost_per_call_usd: float | None = "__unset__",  # type: ignore[assignment]
) -> dict:
    """max_cost_per_call_usd defaults to a sentinel (not None) so "not provided" and
    "explicitly clearing the cap" are distinguishable — None is a valid, meaningful
    value (no per-call cap) that a caller must be able to set."""
    async with get_db_session_for_tenant(tenant_id) as s:
        row = await _provider_row(s, provider_id)
        if row is None:
            raise ProviderNotFoundError(provider_id)
        if max_cost_per_call_usd != "__unset__":
            await s.execute(
                text("UPDATE model_providers SET max_cost_per_call_usd = :v, updated_at = now() WHERE id = :id"),
                {"v": max_cost_per_call_usd, "id": provider_id},
            )
        if display_name is not None:
            new_name = display_name.strip()
            if new_name and await _name_exists(s, tenant_id, new_name, exclude_id=provider_id):
                raise DuplicateProviderNameError(
                    f"A provider connection named {new_name!r} already exists")
            await s.execute(
                text("UPDATE model_providers SET display_name = :n, updated_at = now() WHERE id = :id"),
                {"n": new_name, "id": provider_id},
            )
        if enabled_models is not None:
            # Custom providers accept any model id (no curated catalog to check against).
            if is_known_provider(row.provider):
                for m in enabled_models:
                    if not is_valid_model(row.provider, m):
                        raise InvalidModelError(f"{m!r} invalid for provider {row.provider!r}")
            existing = {
                r.model_id: r.id for r in (await s.execute(
                    text("SELECT id, model_id FROM model_offerings WHERE provider_id = :pid"),
                    {"pid": provider_id},
                )).fetchall()
            }
            want = set(enabled_models)
            # add new, enable wanted, disable the rest (keep rows for audit/default history)
            for m in want - set(existing):
                await s.execute(
                    text("INSERT INTO model_offerings (id, tenant_id, provider_id, model_id, enabled, is_default) "
                         "VALUES (:id, :t, :pid, :m, true, false)"),
                    {"id": str(_uuid.uuid4()), "t": tenant_id, "pid": provider_id, "m": m},
                )
            for m, oid in existing.items():
                await s.execute(
                    text("UPDATE model_offerings SET enabled = :en WHERE id = :id"),
                    {"en": m in want, "id": oid},
                )
        new_row = await _provider_row(s, provider_id)
        offerings = await _offerings_for(s, provider_id)
    return _provider_dict(new_row, offerings)


async def set_default(tenant_id: str, offering_id: str) -> None:
    async with get_db_session_for_tenant(tenant_id) as s:
        target = (await s.execute(
            text("SELECT id, enabled FROM model_offerings WHERE id = :id"), {"id": offering_id},
        )).first()
        if target is None:
            raise OfferingNotFoundError(offering_id)
        if not target.enabled:
            raise ValueError("cannot default a disabled model")
        # clear the current default first (partial-unique index allows only one);
        # explicit tenant_id guard is defence-in-depth under superuser dev connections.
        await s.execute(
            text("UPDATE model_offerings SET is_default = false WHERE is_default = true AND tenant_id = :t"),
            {"t": tenant_id},
        )
        await s.execute(
            text("UPDATE model_offerings SET is_default = true WHERE id = :id"), {"id": offering_id},
        )
    logger.info("model default set tenant=%s offering=%s", tenant_id, offering_id)


async def delete_provider(tenant_id: str, provider_id: str, workspace_id: str | None = None) -> None:
    # Providers are TENANT-WIDE: any connection the tenant owns can be deleted. RLS + the
    # tenant session guarantee only this tenant's rows are reachable, so no workspace guard
    # is needed (the old guard made NULL-workspace / cross-workspace connections impossible
    # to remove from the UI while the resolver kept using them). Deleting removes the row
    # (offerings cascade) AND the stored key, so a removed key can never be resolved again.
    async with get_db_session_for_tenant(tenant_id) as s:
        row = await _provider_row(s, provider_id)
        if row is None:
            raise ProviderNotFoundError(provider_id)
        secret_ref = row.secret_ref
        # offerings cascade via FK ON DELETE CASCADE
        await s.execute(text("DELETE FROM model_providers WHERE id = :id"), {"id": provider_id})
    if secret_ref:
        await secret_store.delete_secret(tenant_id, secret_ref)
    logger.info("model provider deleted tenant=%s id=%s", tenant_id, provider_id)


async def get_options(tenant_id: str, project_id: str | None = None) -> dict:
    """Selectable offerings whose provider is verified `valid` — for the model
    picker. When `project_id` is given and the tenant has grants configured, narrows to
    that project's effective offering set (spec §5) — otherwise (no grants yet, or no
    project context) stays tenant-wide, unchanged from before this feature.

    TAKES NO workspace_id. It used to accept one and never read it: the scope that
    decides this list is the PROJECT's, and `effective_project_offerings` walks from the
    project to its own selection or its unit's inherited set. The caller was resolving
    an active-workspace selector to pass in — a DB round trip whose result was discarded,
    and which named whichever unit the ambient X-Workspace-Id cookie pointed at rather
    than the one the project actually belongs to."""
    from shared.services.model_grants import effective_project_offerings  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(text(
            "SELECT o.id AS offering_id, o.model_id, o.is_default, "
            "o.input_price_per_million, o.output_price_per_million, "
            "p.id AS provider_id, p.provider, p.display_name "
            "FROM model_offerings o JOIN model_providers p ON p.id = o.provider_id "
            "WHERE o.enabled = true AND p.status = 'valid' AND p.tenant_id = :t AND o.tenant_id = :t"
            " ORDER BY p.display_name, p.provider, o.model_id"
        ), {"t": tenant_id})).fetchall()

    if project_id:
        effective_ids = await effective_project_offerings(tenant_id, project_id)
        if effective_ids is not None:
            rows = [r for r in rows if str(r.offering_id) in effective_ids]

    options = [{
        "offering_id": str(r.offering_id),
        "provider_id": str(r.provider_id),
        "display_name": r.display_name,
        "provider": r.provider,
        "model_id": r.model_id,
        "is_default": bool(r.is_default),
        "input_price_per_million": _price(r.input_price_per_million),
        "output_price_per_million": _price(r.output_price_per_million),
    } for r in rows]
    default = next((o for o in options if o["is_default"]), None)
    return {
        "options": options,
        "default_offering_id": default["offering_id"] if default else None,
        "default_model_id": default["model_id"] if default else None,
    }
