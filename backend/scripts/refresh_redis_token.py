"""Refresh the Entra token inside REDIS_URL in backend/.env.

WHY THIS IS NEEDED. Azure Managed Redis has access keys DISABLED on the shared cluster,
so authentication is Entra-only — and with Entra, the access token IS the password. It
expires in roughly 24 hours, after which every Redis call fails with "invalid
username-password pair" and the app degrades exactly as if Redis were down: WebSocket
tickets stop minting, so agents stop opening.

`shared/redis_client.py` builds its client from REDIS_URL and does not refresh anything,
by design — the URL is the single knob. That makes this script the refresh mechanism for
local development.

    python -m scripts.refresh_redis_token

A deployed instance should NOT use this. Inside AKS the workload identity
(`backendworkloadidentity`, already on the cluster's access policy) supplies a token that
the platform can renew in-process; a file rewritten by hand is a laptop convenience, not
a deployment strategy.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from urllib.parse import quote

ENV_PATH = pathlib.Path(__file__).resolve().parents[1] / ".env"
SCOPE = "https://redis.azure.com/.default"


def _run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    if out.returncode != 0:
        raise SystemExit(f"`{' '.join(cmd[:3])}…` failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def main() -> int:
    if not ENV_PATH.exists():
        raise SystemExit(f"no .env at {ENV_PATH}")

    text = ENV_PATH.read_text(encoding="utf-8")
    match = re.search(r"^REDIS_URL=(.*)$", text, re.M)
    if not match:
        raise SystemExit("no REDIS_URL line in .env")
    current = match.group(1)

    if "redis.azure.net" not in current:
        print(f"REDIS_URL is not an Azure endpoint — nothing to refresh:\n  {current}")
        return 0

    # `az` is a shim on Windows; resolve it rather than relying on shell=True.
    az = "az.cmd" if sys.platform == "win32" else "az"
    oid = _run([az, "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"])
    token = _run(
        [az, "account", "get-access-token", "--scope", SCOPE, "--query", "accessToken", "-o", "tsv"]
    )

    # Replace only the credentials, so host, port and query flags survive untouched.
    # subn, not sub: `az` hands back a CACHED token while the current one is still
    # valid, so an unchanged URL is a perfectly normal refresh — not a parse failure.
    # Counting substitutions is what distinguishes the two; comparing strings does not.
    rebuilt, replaced = re.subn(
        r"^(rediss?://)[^@]*@",
        lambda m: f"{m.group(1)}{quote(oid)}:{quote(token)}@",
        current,
    )
    if not replaced:
        # Deliberately does NOT echo the URL. It carries a live credential, and an
        # error message — which gets pasted into chats and tickets — is the last place
        # that should appear.
        raise SystemExit(
            "could not find credentials in REDIS_URL "
            "(expected rediss://<object-id>:<token>@host)"
        )

    ENV_PATH.write_text(text.replace(match.group(0), f"REDIS_URL={rebuilt}", 1), encoding="utf-8")
    host = rebuilt.split("@", 1)[1].split("/", 1)[0]
    print(f"REDIS_URL refreshed for {oid[:8]}… against {host}")
    print("Token is valid for roughly 24 hours; re-run this when Redis starts refusing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
