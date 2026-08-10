"""Alembic async migration environment.

Run alembic from the agentic_app/ directory so that shared.models.orm is importable.
Offline mode is disabled — asyncpg requires an active connection.
"""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Load the backend .env so alembic sees the same DB config as the app (config.env).
from pathlib import Path as _Path
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_Path(__file__).resolve().parents[1] / ".env")

# Override the placeholder sqlalchemy.url with the superuser connection string.
# %(VAR)s in alembic.ini reads INI section values, not os.environ — set_main_option
# is the correct injection point.
# POSTGRES_MIGRATIONS_CONN_STRING is hard-required — no fallback to POSTGRES_CONN_STRING.
# Reason: POSTGRES_CONN_STRING is the app-role DSN. Once RLS is active, the app role
# cannot run schema migrations (RLS policies block the app role from reading system
# tables and DDL operations). Migrations MUST run as a superuser or BYPASSRLS role.
# Do not "fix" this error by setting POSTGRES_MIGRATIONS_CONN_STRING=POSTGRES_CONN_STRING
# — that defeats the structural isolation guarantee (D-09).
# KEY-VAULT-FIRST: the superuser migrations DSN lives in Key Vault under the secret name
# KV_SECRET_POSTGRES_MIGRATIONS_CONN (config.env; defaults to
# sdlc-{env}-postgres-migrations-conn-string, overridable via .env), not .env itself. Falls
# back to the env var only if Key Vault is unset/unreachable. Still hard-required — alembic
# cannot run without it.
from config.env import (
    AZURE_KEY_VAULT_URL as _KV_URL,
    KV_SECRET_POSTGRES_MIGRATIONS_CONN as _KV_SECRET_MIGRATIONS,
)

_migrations_url = None
if _KV_URL:
    from shared.keyvault import load_secret_sync as _load_secret_sync

    _migrations_url = _load_secret_sync(_KV_SECRET_MIGRATIONS)
if not _migrations_url:
    _migrations_url = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING")
if not _migrations_url:
    raise RuntimeError(
        "Migrations DSN unavailable — set Key Vault secret "
        "'sdlc-<env>-postgres-migrations-conn-string' (or POSTGRES_MIGRATIONS_CONN_STRING env). "
        "It must be a superuser/BYPASSRLS DSN; POSTGRES_CONN_STRING is the app-role DSN and "
        "must NOT be used for migrations."
    )
config.set_main_option("sqlalchemy.url", _migrations_url)

from shared.models.orm import Base  # noqa: E402 — must come after sys.path is established

target_metadata = Base.metadata


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    raise RuntimeError("Offline mode not supported with async engine — use online mode only")
else:
    run_migrations_online()
