"""Outbound email — SMTP smoke test (local-dev).

Proves the SMTP settings in backend/.env actually deliver, BEFORE trusting them with an
onboarding invite. Without this the first test of the config is a real person not getting
a link, and the failure looks like "onboarding is broken" rather than "the password is
wrong".

Run from backend/ — as a MODULE, matching the other scripts here. `python
scripts/email_smoke.py` puts scripts/ on sys.path instead of backend/ and fails on
`import config`:

    uv run python -m scripts.email_smoke you@example.com

Sends the real invite template with a dummy token, so what lands in the inbox is what a
new joiner would actually see — including how it renders and whether it goes to spam,
which is the thing worth finding out early.

Never prints SMTP_PASSWORD.
"""
import asyncio
import sys

from config.env import (
    EMAIL_FROM,
    EMAIL_FROM_NAME,
    INVITE_TOKEN_TTL_MINUTES,
    PUBLIC_APP_URL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_USE_SSL,
    SMTP_USE_TLS,
)
from shared.services import email_templates
from shared.services.email import is_configured, send_email


def _report_config() -> bool:
    print("-> Configuration")
    print(f"    SMTP_HOST       {SMTP_HOST or '(unset)'}")
    print(f"    SMTP_PORT       {SMTP_PORT}")
    print(f"    SMTP_USERNAME   {SMTP_USERNAME or '(unset)'}")
    # Length only. The value is a credential and this output gets pasted into chats.
    print(f"    SMTP_PASSWORD   {'set, ' + str(len(SMTP_PASSWORD)) + ' chars' if SMTP_PASSWORD else '(unset)'}")
    print(f"    SMTP_USE_TLS    {SMTP_USE_TLS}")
    print(f"    SMTP_USE_SSL    {SMTP_USE_SSL}")
    print(f"    EMAIL_FROM      {EMAIL_FROM or '(unset)'}")
    print(f"    EMAIL_FROM_NAME {EMAIL_FROM_NAME}")
    print(f"    PUBLIC_APP_URL  {PUBLIC_APP_URL}")
    print()

    if not is_configured():
        print("[FAIL] Not configured - the app will LOG invite emails instead of sending them.")
        print("  That is a supported mode: onboarding still works and the set-password")
        print("  link appears in the backend log. Set SMTP_HOST and EMAIL_FROM to send.")
        return False

    # A 16-character App Password is usually pasted as 4 groups of 4 with spaces. Gmail
    # tolerates them; flagging it anyway because a stray space is otherwise indis-
    # tinguishable from a wrong password in the 535 that comes back.
    if SMTP_PASSWORD and " " in SMTP_PASSWORD:
        print("  note: SMTP_PASSWORD contains spaces. Gmail App Passwords are shown as")
        print("        'abcd efgh ijkl mnop' - paste them WITHOUT the spaces.\n")
    if "gmail" in SMTP_HOST and SMTP_USERNAME and SMTP_USERNAME != EMAIL_FROM:
        print("  note: Gmail rejects a From address that is not the authenticated account")
        print("        unless it is a verified alias. SMTP_USERNAME and EMAIL_FROM differ.\n")
    return True


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: uv run python -m scripts.email_smoke <recipient@example.com>")
        raise SystemExit(2)
    recipient = sys.argv[1]

    if not _report_config():
        raise SystemExit(1)

    print(f"-> Sending the real invite template to {recipient} ...")
    subject, text_body, html_body = email_templates.invite_email(
        "smoke-test-token-not-valid", INVITE_TOKEN_TTL_MINUTES
    )
    ok = await send_email(recipient, f"[smoke test] {subject}", text_body, html_body)

    if ok:
        print("[ok] Accepted by the SMTP server.")
        print("  Check the inbox - AND the spam folder. A set-password link that lands")
        print("  in spam means a new joiner cannot get in, which is the failure mode")
        print("  worth discovering now rather than on somebody's first day.")
        print("  The link in it points at a token that was never issued, so it will")
        print("  correctly report 'this link isn't valid'. That is the test passing.")
    else:
        print("[FAIL] Not sent. The reason is in the log line above (WARNING from")
        print("  shared.services.email). Common causes:")
        print("    SMTPAuthenticationError  - wrong App Password, or 2-Step Verification")
        print("                               is off so the password is a normal one")
        print("    SMTPSenderRefused        - EMAIL_FROM is not the authenticated account")
        print("    gaierror / timeout       - SMTP_HOST or SMTP_PORT wrong, or blocked")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
