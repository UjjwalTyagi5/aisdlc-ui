"""Backfill and re-sync the model prices each project's Langfuse uses to cost traces.

WHY THIS EXISTS. `shared/observability/model_prices.py` seeds a price the first time a
project resolves a model, which covers everything from now on and nothing from before.
Traces already recorded keep whatever cost Langfuse computed when it ingested them —
for a model it could not price, that is $0, permanently. This is the backfill, and the
reconciler for the two ways the lazy path can drift:

    a model was resolved while Langfuse was unreachable (the sync is fail-open)
    LiteLLM repriced a model in a dependency bump

It prices every model the tenant has ENABLED, not merely the ones already seen, so a
project's first turn on a newly enabled model is costed correctly rather than being the
run that teaches Langfuse about it.

WHAT IT CANNOT DO. Langfuse computes cost at INGESTION. Re-pricing a model does not
recost the traces already stored — those stay at $0 and no write here changes that. The
figures start being right from the next trace onward.

`--force` EXISTS FOR THE CACHE. Langfuse caches model lookups per project, misses
included, and invalidates only on its own writes. A project that traced a model before
it was priced therefore keeps costing it at $0 even once the price is correct, until
that entry expires by itself. Re-registering is what clears it, which is why "the price
is already right" is not a reason to skip the write.

USAGE
    python -m scripts.sync_langfuse_model_prices --dry-run    # report, change nothing
    python -m scripts.sync_langfuse_model_prices              # converge
    python -m scripts.sync_langfuse_model_prices --force      # + bust cached misses
    python -m scripts.sync_langfuse_model_prices --project <uuid>
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

from shared.db import get_db_session_for_tenant  # noqa: E402
from shared.observability.model_prices import (  # noqa: E402
    _match_pattern,
    _per_token_prices,
    sync_model_price,
)

logger = logging.getLogger("sync_langfuse_model_prices")


async def _bindings(project_filter: str | None) -> list[dict]:
    """Every active binding, with the tenant it belongs to.

    Organizations are read outside a tenant session; the bindings themselves are read
    inside one, because `langfuse_bindings` is RLS-scoped and returns zero rows — not
    an error — without `app.current_tenant_id` set.
    """
    from shared.db import get_db_session_superuser  # noqa: PLC0415

    from shared.observability.bindings import _decrypt  # noqa: PLC0415

    async with get_db_session_superuser() as s:
        orgs = (await s.execute(text("select id, slug from organizations"))).fetchall()

    out: list[dict] = []
    for org_id, slug in orgs:
        async with get_db_session_for_tenant(str(org_id)) as s:
            sql = (
                "select b.project_id, b.langfuse_project_id, b.langfuse_host, "
                "b.public_key_encrypted, b.secret_key_encrypted, p.display_name "
                "from langfuse_bindings b join projects p on p.id = b.project_id "
                "where b.is_active = true"
            )
            params: dict = {}
            if project_filter:
                sql += " and b.project_id = :p"
                params["p"] = project_filter
            rows = (await s.execute(text(sql), params)).mappings().fetchall()
        for r in rows:
            out.append({
                "tenant_id": str(org_id), "tenant_slug": slug,
                "project_id": str(r["project_id"]),
                "project_name": r["display_name"],
                "langfuse_project_id": r["langfuse_project_id"],
                "host": r["langfuse_host"],
                "public_key": _decrypt(r["public_key_encrypted"]),
                "secret_key": _decrypt(r["secret_key_encrypted"]),
            })
    return out


async def _enabled_models(tenant_id: str) -> list[str]:
    """Model ids from every enabled provider offering for one tenant."""
    from shared.services.model_resolver import _load_enabled  # noqa: PLC0415

    offerings = await _load_enabled(tenant_id)
    return sorted({o["model_id"] for o in offerings if o.get("model_id")})


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report drift, change nothing")
    ap.add_argument("--project", help="one SDLC project id only")
    ap.add_argument(
        "--force", action="store_true",
        help="re-register even when the price is already correct — Langfuse caches "
             "model lookups per project and only invalidates on its own writes, so "
             "this is what clears a miss cached before the model was priced",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s"
    )

    bindings = await _bindings(args.project)
    if not bindings:
        logger.info("no active Langfuse bindings — nothing to price.")
        return 0

    written = unpriced = 0
    for b in bindings:
        models = await _enabled_models(b["tenant_id"])
        logger.info(
            "\n%s / %s  ->  langfuse project %s  (%d enabled model(s))",
            b["tenant_slug"], b["project_name"], b["langfuse_project_id"], len(models),
        )
        for model in models:
            prices = _per_token_prices(model)
            if prices is None:
                logger.info("   %-40s UNPRICED — traces will show $0", model)
                unpriced += 1
                continue
            if args.dry_run:
                logger.info(
                    "   %-40s would price in=%s out=%s per token, matching %s",
                    model, *(f"{p:.10f}" for p in prices), _match_pattern(model),
                )
                continue
            try:
                changed = await sync_model_price(
                    host=b["host"], public_key=b["public_key"],
                    secret_key=b["secret_key"], model=model, force=args.force,
                )
            except Exception as exc:
                logger.warning("   %-40s FAILED: %s: %s", model, type(exc).__name__, exc)
                continue
            logger.info("   %-40s %s", model, "written" if changed else "already correct")
            written += int(changed)

    logger.info(
        "\n%s. %d price row(s) written, %d model(s) nothing could price.",
        "Dry run" if args.dry_run else "Done", written, unpriced,
    )
    if unpriced:
        logger.info(
            "An unpriced model is in neither the curated table nor LiteLLM's — check the "
            "model id, or add it to shared/cost/pricing._MODEL_PRICES."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
