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
             "workspace_id, api_base, is_custom "
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
    return {
        "id": str(row.id), "provider": row.provider, "display_name": row.display_name,
        "secret_ref": row.secret_ref, "status": row.status,
        "api_base": getattr(row, "api_base", None),
        "is_custom": bool(getattr(row, "is_custom", False)),
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "offerings": offerings,
    }


async def create_provider(
    tenant_id: str, *, provider: str, display_name: str, api_key: str,
    created_by: str, models: list[dict] | None = None,
    enabled_models: list[str] | None = None, api_base: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    """Create a provider connection + its enabled model offerings.

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
    if models is None:
        models = [{"model_id": m} for m in (enabled_models or [])]
    if not provider or not display_name or not api_key:
        raise ValueError("provider, display_name, and api_key are required")
    if not models:
        raise ValueError("at least one model is required")

    is_custom = not is_known_provider(provider)
    # Resolve pricing per model: caller-supplied wins, else the LiteLLM catalog.
    # Governance: a model NOT in the catalog (custom / unlisted / self-hosted)
    # MUST carry pricing so Cost/Langfuse can attribute spend. Catalog models may
    # leave price NULL (resolved from LiteLLM's cost map at read time).
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

    # Connection names must be unique per tenant (identity for model selection).
    async with get_db_session_for_tenant(tenant_id) as s:
        if await _name_exists(s, tenant_id, display_name):
            raise DuplicateProviderNameError(
                f"A provider connection named {display_name!r} already exists")

    provider_id = str(_uuid.uuid4())
    secret_ref = _secret_ref(provider_id)
    # Store the key FIRST so a row never references a missing secret.
    await secret_store.put_secret(tenant_id, secret_ref, api_key)
    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            await s.execute(
                text("INSERT INTO model_providers "
                     "(id, tenant_id, workspace_id, provider, display_name, secret_ref, api_base, is_custom, status, created_by) "
                     "VALUES (:id, :t, :w, :p, :n, :ref, :ab, :cust, 'unverified', :by)"),
                {"id": provider_id, "t": tenant_id, "w": workspace_id, "p": provider, "n": display_name,
                 "ref": secret_ref, "ab": api_base, "cust": is_custom, "by": created_by},
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
        # roll back the orphaned secret on row-insert failure
        await secret_store.delete_secret(tenant_id, secret_ref)
        raise
    logger.info("model provider created tenant=%s provider=%s id=%s custom=%s",
                tenant_id, provider, provider_id, is_custom)
    return _provider_dict(row, offerings)


async def list_providers(tenant_id: str, workspace_id: str | None = None) -> list[dict]:
    # Providers are TENANT-WIDE: every connection the org adds is visible and usable
    # org-wide, exactly matching how the model resolver selects them (resolver filters
    # by tenant only). This keeps one source of truth — what the UI shows is precisely
    # what agents can run on. `workspace_id` is accepted for call-site compatibility but
    # no longer narrows results (an earlier workspace filter let "removed" connections
    # survive unseen because the resolver ignored workspace).
    async with get_db_session_for_tenant(tenant_id) as s:
        prows = (await s.execute(
            text("SELECT id, provider, display_name, secret_ref, status, last_verified_at, created_at, "
                 "api_base, is_custom "
                 "FROM model_providers WHERE tenant_id = :t ORDER BY created_at"),
            {"t": tenant_id},
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
) -> dict:
    async with get_db_session_for_tenant(tenant_id) as s:
        row = await _provider_row(s, provider_id)
        if row is None:
            raise ProviderNotFoundError(provider_id)
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
    await secret_store.delete_secret(tenant_id, secret_ref)
    logger.info("model provider deleted tenant=%s id=%s", tenant_id, provider_id)


async def get_options(tenant_id: str, workspace_id: str | None = None) -> dict:
    """Selectable offerings whose provider is verified `valid` — for the model
    picker. Each option carries full identity (offering_id + provider connection +
    model) so the UI can disambiguate two keys that expose the same model, and so
    runs can be dispatched against an exact offering rather than a bare model_id.

    Returns {options:[{offering_id, provider_id, display_name, provider, model_id,
    is_default}], default_offering_id, default_model_id}. Explicit p.tenant_id
    filter is defence-in-depth under superuser dev connections (RLS enforces
    isolation in production)."""
    # TENANT-WIDE: the picker offers every valid+enabled offering the org owns — the same
    # set the resolver can run on. `workspace_id` is accepted for call-site compatibility
    # but no longer narrows the options (one source of truth across UI + resolver).
    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(text(
            "SELECT o.id AS offering_id, o.model_id, o.is_default, "
            "o.input_price_per_million, o.output_price_per_million, "
            "p.id AS provider_id, p.provider, p.display_name "
            "FROM model_offerings o JOIN model_providers p ON p.id = o.provider_id "
            "WHERE o.enabled = true AND p.status = 'valid' AND p.tenant_id = :t AND o.tenant_id = :t"
            " ORDER BY p.display_name, p.provider, o.model_id"
        ), {"t": tenant_id})).fetchall()
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
