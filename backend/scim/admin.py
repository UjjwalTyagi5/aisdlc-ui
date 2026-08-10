"""Operator CLI for seeding the per-tenant SCIM bearer credential (Wave A, D-02).

Usage (operator-only — NO HTTP surface):
  python -m agentic_app.scim.admin set-credential \\
    --tenant-id <uuid> --token <bearer-secret>

Writes the per-tenant SCIM bearer token to Key Vault via store_secret so the
FastAPI SCIM endpoint (scim/auth.py verify_scim_credential) can verify it.

KV key written: '{tenant_id}-scim-bearer-token'

Why this is operator-only bootstrap (mirrors scim/admin.py pattern from grant.py):
  The SCIM credential is a high-privilege secret that authorises an IdP (e.g. Azure AD)
  to provision/deprovision users in a tenant.  There is no safe way to let a tenant admin
  set this via HTTP without an additional RBAC gate; the operator CLI gate (--tenant-id
  required) makes the scope explicit and prevents accidental cross-tenant writes.
  A self-serve SCIM credential rotation UI is deferred to a later phase.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def _set_credential(tenant_id: str, token: str) -> None:
    """Write the SCIM bearer credential to Key Vault for the given tenant."""
    from shared.keyvault import store_secret
    ok = await store_secret("scim-bearer-token", token, tenant_id=tenant_id)
    if not ok:
        raise RuntimeError(
            f"Failed to store SCIM bearer token in Key Vault for tenant '{tenant_id}'. "
            "Check AZURE_KEY_VAULT_URL and Azure identity credentials."
        )


def _cli_main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agentic_app.scim.admin",
        description=(
            "Operator CLI: seed the per-tenant SCIM bearer credential in Key Vault.\n"
            "Writes to KV secret '{tenant_id}-scim-bearer-token'.\n"
            "NO HTTP surface — operator-only (D-02)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    set_cred = sub.add_parser(
        "set-credential",
        help="Set (or rotate) the SCIM bearer token for a tenant.",
    )
    set_cred.add_argument(
        "--tenant-id",
        required=True,
        help="Org/tenant UUID or identifier (REQUIRED — no implicit default)",
    )
    set_cred.add_argument(
        "--token",
        required=True,
        help="The bearer token the IdP will send in Authorization: Bearer {token}",
    )

    args = parser.parse_args()

    if args.command == "set-credential":
        asyncio.run(_set_credential(tenant_id=args.tenant_id, token=args.token))
        print(f"SCIM bearer credential set for tenant: {args.tenant_id!r}")


if __name__ == "__main__":
    _cli_main()
