"""Copy the platform secrets out of backend/.env and into Azure Key Vault.

RUN THIS YOURSELF, and do not pipe its output anywhere you would not paste a password.
It reads live credentials. It never prints a secret VALUE — only names, lengths and
whether each one changed — but the .env it reads is the real thing.

    cd C:\\pwc_work\\frontend\\backend
    .\\.venv\\Scripts\\python.exe -m scripts.seed_key_vault --env prod --dry-run
    .\\.venv\\Scripts\\python.exe -m scripts.seed_key_vault --env prod

--dry-run first, always. It shows exactly which secret names would be written without
touching the vault, which is the cheap way to catch a wrong --env before it scatters
production credentials under `sdlc-dev-*`.

WHICH SECRETS: exactly the list in config/secret_bootstrap.PLATFORM_SECRETS, so what is
seeded and what is read back can never drift apart. A setting absent or empty in .env is
SKIPPED rather than written blank — Key Vault rejects empty values, and a secret that
exists holding nothing is worse than one that does not exist, because the loader would
treat it as configured.

THE DATABASE DSNs ARE OPT-IN, VIA --with-database. They are absent from
PLATFORM_SECRETS on purpose — see config/secret_bootstrap.py for why two mechanisms
writing the same value is a bug rather than a convenience — but "not in
PLATFORM_SECRETS" had become "written by nothing at all", which is a different and
worse problem.

Nothing in the codebase called store_secret for a Postgres DSN. shared/db.py,
config/checkpoint.py and migrations/env.py all READ one from Key Vault under
KV_SECRET_POSTGRES_*, every read missed, and every boot fell back to
POSTGRES_CONN_STRING with a log line saying so. The net effect on a deployment that
followed the runbook: every platform secret in the vault EXCEPT the database
password, which stayed in .env — the one place the vault exists to get it out of.

So this seeds them under exactly the names those three modules read, from
config.env.KV_SECRET_POSTGRES_*. One writer, one naming convention, and the
convention is the reader's own rather than a second copy of it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import dotenv_values  # noqa: E402

from config.secret_bootstrap import PLATFORM_SECRETS, secret_name_for  # noqa: E402
from config.env import (  # noqa: E402
    KV_SECRET_POSTGRES_CONN,
    KV_SECRET_POSTGRES_MIGRATIONS_CONN,
    KV_SECRET_POSTGRES_SYNC_CONN,
)

# env var -> the Key Vault name its READER already looks under. Deliberately not
# secret_name_for(): these three predate that convention and are configurable, so
# rebuilding the name here would seed a name nothing reads the moment one is
# overridden. Same bug process_api's startup check had.
DATABASE_DSNS: tuple[tuple[str, str], ...] = (
    ("POSTGRES_CONN_STRING", KV_SECRET_POSTGRES_CONN),
    ("POSTGRES_SYNC_CONN_STRING", KV_SECRET_POSTGRES_SYNC_CONN),
    ("POSTGRES_MIGRATIONS_CONN_STRING", KV_SECRET_POSTGRES_MIGRATIONS_CONN),
)


def _mask(value: str) -> str:
    """What a secret is allowed to look like in this script's output.

    Length alone, plus the first two characters for the one job that actually needs
    them: telling apart two keys that were pasted into the wrong variables. Two
    characters of a 40-character token is not a meaningful disclosure; the whole token
    on a terminal that scrolls into a screenshot is.
    """
    n = len(value)
    if n <= 6:
        return f"<{n} chars>"
    return f"{value[:2]}… <{n} chars>"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--env",
        required=True,
        help="Deployment environment these secrets are for (prod, staging, uat …). "
             "Decides the secret-name prefix, so it must match the ENV the deployed "
             "process will run with.",
    )
    ap.add_argument(
        "--vault-url",
        default=os.environ.get("AZURE_KEY_VAULT_URL", ""),
        help="https://<vault>.vault.azure.net. Defaults to AZURE_KEY_VAULT_URL.",
    )
    ap.add_argument(
        "--env-file",
        default=str(BACKEND_ROOT / ".env"),
        help="Where to read the values from. Defaults to backend/.env.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written and exit without contacting the vault.",
    )
    ap.add_argument(
        "--with-database",
        action="store_true",
        help=(
            "Also seed the three Postgres DSNs under the KV_SECRET_POSTGRES_* names "
            "shared/db.py, config/checkpoint.py and migrations/env.py read. Without "
            "this the vault holds every platform secret EXCEPT the database password, "
            "and every boot falls back to POSTGRES_CONN_STRING from the environment."
        ),
    )
    args = ap.parse_args()

    if args.env.strip().lower() == "dev":
        print(
            "Refusing: --env dev is the environment that reads from .env and never "
            "contacts Key Vault, so seeding it would create secrets nothing ever reads.\n"
            "Pass the environment you are deploying (e.g. --env prod)."
        )
        return 2

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"No such file: {env_path}")
        return 2

    values = dotenv_values(env_path)
    present: list[tuple[str, str, str]] = []   # (env_var, kv_name, value)
    skipped: list[str] = []
    for var in PLATFORM_SECRETS:
        raw = (values.get(var) or "").strip()
        if not raw:
            skipped.append(var)
            continue
        present.append((var, secret_name_for(var, env=args.env.strip().lower(), prefix=None), raw))

    if args.with_database:
        for var, kv_name in DATABASE_DSNS:
            raw = (values.get(var) or "").strip()
            if not raw:
                skipped.append(var)
                continue
            present.append((var, kv_name, raw))

    print(f"\n  source     : {env_path}")
    print(f"  target env : {args.env}")
    print(f"  vault      : {args.vault_url or '(none given)'}")
    print(f"  to write   : {len(present)}     not set in .env: {len(skipped)}\n")

    for var, kv_name, value in present:
        print(f"    {var:28s} -> {kv_name:48s} {_mask(value)}")
    if skipped:
        print(f"\n  skipped (empty or absent in .env):\n    {', '.join(skipped)}")

    if args.dry_run:
        print("\n  --dry-run: nothing was written.")
        return 0

    if not args.vault_url:
        print("\n  No --vault-url and no AZURE_KEY_VAULT_URL. Nothing written.")
        return 2

    if not present:
        print("\n  Nothing to write.")
        return 0

    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=args.vault_url, credential=credential)

    written = unchanged = 0
    failures: list[str] = []
    try:
        for var, kv_name, value in present:
            try:
                # Skip a write when the value already matches. Every set_secret creates a
                # new VERSION even when nothing changed, and a vault whose history is
                # mostly identical versions is one where the audit trail no longer
                # answers "when did this credential actually change".
                try:
                    if client.get_secret(kv_name).value == value:
                        unchanged += 1
                        print(f"    = {kv_name} (unchanged)")
                        continue
                except Exception:
                    pass  # not present yet, or unreadable — fall through to the write
                client.set_secret(kv_name, value)
                written += 1
                print(f"    + {kv_name}")
            except Exception as exc:
                failures.append(f"{kv_name}: {type(exc).__name__}: {exc}")
                print(f"    ! {kv_name} FAILED ({type(exc).__name__})")
    finally:
        try:
            client.close()
        except Exception:
            pass

    print(f"\n  written: {written}   unchanged: {unchanged}   failed: {len(failures)}")
    if failures:
        print("\n  failures:")
        for f in failures:
            print(f"    {f}")
        print(
            "\n  'Forbidden' here almost always means the role assignment has not "
            "propagated yet, or you hold 'Key Vault Secrets User' (read) rather than "
            "'Key Vault Secrets Officer' (write). Wait a minute, then re-run."
        )
        return 1

    print(
        f"\n  Done. Set these on the deployed host and it will read from the vault:\n"
        f"    ENV={args.env}\n"
        f"    AZURE_KEY_VAULT_URL={args.vault_url}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
