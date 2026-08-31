"""Exercise the platform's Key Vault code against a REAL Azure Key Vault.

WHAT THE UNIT TESTS CANNOT TELL YOU. tests/test_secret_bootstrap.py mocks SecretClient,
so it proves the name mapping and the hydration order and nothing about whether the
thing actually works: not that DefaultAzureCredential resolves, not that a missing
secret comes back as None rather than an exception, not that a tenant-scoped write
lands under the name a tenant-scoped read looks for. This script is the other half.

SAFE BY CONSTRUCTION, because it runs against vaults holding real credentials:

  * It NEVER prints a secret value. Only names, lengths and booleans.
  * It writes exactly one secret, named `zz-kvcheck-<uuid>`, and deletes it in a
    finally block. The `zz-` prefix sorts it to the end of any listing and the uuid
    makes a collision with a real secret impossible.
  * It never writes to, or deletes, a name it did not create.

Usage:
    AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net/ \\
      python -m scripts.kv_live_check
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VAULT = os.environ.get("AZURE_KEY_VAULT_URL", "").strip()
if not VAULT:
    raise SystemExit("Set AZURE_KEY_VAULT_URL to the vault to test against.")

# Set before importing anything that reads it at module scope.
os.environ["AZURE_KEY_VAULT_URL"] = VAULT

results: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f'  — {detail}' if detail else ''}")


def skip(label: str, why: str) -> None:
    """Record a check that could not be MEANINGFULLY run.

    Not a pass. The isolation assertions compare a read against None, and on a vault
    where every read returns None — an unreachable vault, a disabled subscription —
    they hold for a reason that has nothing to do with isolation. Reporting those as
    PASS is how a suite claims to cover the thing it just stopped covering.
    """
    results.append(("SKIP", label))
    print(f"  SKIP  {label}  — {why}")


async def main() -> int:
    import shared.keyvault as kv
    from config import secret_bootstrap as sb

    kv.AZURE_KEY_VAULT_URL = VAULT
    print(f"\nVault: {VAULT}\n")

    # ── 1. Authentication + read of a secret that EXISTS ─────────────────────
    print("1. Reading an existing secret (proves auth + network + parsing)")
    from azure.keyvault.secrets.aio import SecretClient
    from azure.identity.aio import DefaultAzureCredential

    cred = DefaultAzureCredential()
    async with SecretClient(vault_url=VAULT, credential=cred) as c:
        names = [s.name async for s in c.list_properties_of_secrets()]
    await cred.close()
    check(len(names) > 0, "vault is reachable and lists secrets", f"{len(names)} secrets")

    if names:
        probe = sorted(names)[0]
        value = await kv.load_secret(probe)
        # Length only. The value is a real credential.
        check(
            value is not None and len(value) > 0,
            f"load_secret({probe!r}) returned a value",
            f"{len(value or '')} chars",
        )

    # ── 2. A secret that does NOT exist ──────────────────────────────────────
    # The single most important behaviour: "not configured" must be None, not an
    # exception. Every connector's auth ladder depends on it — a raise here would
    # surface as a 500 instead of "not connected".
    print("\n2. Reading a secret that does not exist")
    missing = await kv.load_secret(f"zz-kvcheck-absent-{uuid.uuid4().hex[:8]}")
    check(missing is None, "a missing secret resolves to None, not an exception")

    # ── 3. Name mapping the platform actually uses ───────────────────────────
    print("\n3. Secret-name mapping (config/secret_bootstrap)")
    os.environ["ENV"] = "prod"
    mapped = sb.secret_name_for("JWT_SECRET_KEY", env="prod", prefix=None)
    check(mapped == "sdlc-prod-jwt-secret-key", "JWT_SECRET_KEY -> sdlc-prod-jwt-secret-key", mapped)
    check("_" not in mapped, "no underscores survive (Key Vault rejects them)")

    # ── 4. Write / read / delete round-trip, tenant-scoped ───────────────────
    # The tenant prefix IS the isolation between tenants. If store_secret and
    # load_secret disagree about it, one tenant writes where another reads.
    print("\n4. Tenant-scoped round-trip (the isolation contract)")
    ref = f"zz-kvcheck-{uuid.uuid4().hex[:12]}"
    tenant = "kvcheck-tenant-a"
    other = "kvcheck-tenant-b"
    marker = f"marker-{uuid.uuid4().hex}"
    wrote = False
    try:
        wrote = await kv.store_secret(ref, marker, tenant_id=tenant)
        check(wrote is True, f"store_secret({ref!r}, tenant={tenant!r})")

        if not wrote:
            # Everything below compares a read against None. With nothing written they
            # hold on a vault that cannot store anything at all, which proves nothing
            # about tenant isolation.
            why = "nothing was written, so a None result says nothing about isolation"
            skip("the same tenant reads back exactly what it wrote", why)
            skip("a DIFFERENT tenant reads None for the same ref", why)
            skip("an untenanted read does NOT see a tenant's secret", why)
        else:
            back = await kv.load_secret(ref, tenant_id=tenant)
            check(back == marker, "the same tenant reads back exactly what it wrote")

            cross = await kv.load_secret(ref, tenant_id=other)
            check(cross is None, "a DIFFERENT tenant reads None for the same ref")

            untenanted = await kv.load_secret(ref)
            check(
                untenanted is None,
                "an untenanted read does NOT see a tenant's secret",
                "this is the global-vault rung the connectors had removed",
            )
    finally:
        if wrote:
            await kv.delete_secret(ref, tenant_id=tenant)
            gone = await kv.load_secret(ref, tenant_id=tenant)
            check(gone is None, "delete_secret removed it", "no test secret left behind")

    # ── 5. Fail-closed when the vault is wrong ───────────────────────────────
    print("\n5. Fail-closed behaviour")
    saved = kv.AZURE_KEY_VAULT_URL
    try:
        kv.AZURE_KEY_VAULT_URL = ""
        check(await kv.load_secret("anything") is None, "no vault URL configured -> None, no raise")
        kv.AZURE_KEY_VAULT_URL = "https://kvcheck-does-not-exist-01234.vault.azure.net/"
        check(
            await kv.load_secret("anything") is None,
            "unreachable vault -> None, no raise",
            "load_secret is called at startup; a raise would refuse to boot",
        )
    finally:
        kv.AZURE_KEY_VAULT_URL = saved

    failed = [label for st, label in results if st == "FAIL"]
    skipped = [label for st, label in results if st == "SKIP"]
    passed = [label for st, label in results if st == "PASS"]
    print(f"\n{'=' * 62}")
    print(f"{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    for label in failed:
        print(f"  FAILED:  {label}")
    for label in skipped:
        print(f"  SKIPPED: {label}")
    if skipped:
        print(
            "\nA SKIP is not a pass. The vault could not be written to, so the "
            "isolation contract was not exercised at all."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
