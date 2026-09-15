"""Refresh the Entra token inside REDIS_URL in backend/.env.

WHY THIS IS NEEDED. Azure Managed Redis has access keys DISABLED on the shared cluster,
so authentication is Entra-only — and with Entra, the access token IS the password. It
expires in about an hour — `az` issues 60-90 minute tokens for the redis.azure.com scope,
not the ~24 hours this file used to claim — after which every Redis call fails with
"invalid username-password pair" and the app degrades exactly as if Redis were down:
WebSocket tickets stop minting, so agents stop opening. Expect to re-run this several
times a working day.

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

import base64
import datetime
import json
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


def _expiry(token: str) -> str:
    """Best-effort `exp` from the token, so the caller knows when to come back.

    Unverified, and deliberately so: this decodes a token we were just handed by `az` to
    print a time, it does not trust it for anything. Any parse problem degrades to a
    shrug rather than failing a refresh that otherwise succeeded.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        when = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc).astimezone()
        left = (when - datetime.datetime.now(when.tzinfo)).total_seconds() / 60
        return f"{when:%H:%M %Z} ({left:.0f} min)"
    except Exception:
        return "an unknown time"


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

    # Splice at the matched span rather than str.replace: replace() rewrites the FIRST
    # occurrence anywhere in the file, which is not necessarily the line the regex found.
    # A commented example or an OLD_REDIS_URL= line carrying the same value earlier in
    # the file would swallow the new token and leave the live line stale — which fails
    # indistinguishably from the expiry this script exists to prevent.
    ENV_PATH.write_text(
        f"{text[: match.start()]}REDIS_URL={rebuilt}{text[match.end() :]}", encoding="utf-8"
    )
    host = rebuilt.split("@", 1)[1].split("/", 1)[0]
    print(f"REDIS_URL refreshed for {oid[:8]}… against {host}, valid until {_expiry(token)}.")
    print("Re-run this when Redis starts refusing; the tokens last 60-90 minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
