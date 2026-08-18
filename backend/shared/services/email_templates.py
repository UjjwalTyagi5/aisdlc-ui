"""The two emails this platform sends, and their wording.

Kept apart from `email.py` (how a message is delivered) and from the routes (when one is
sent) because the copy is the part most likely to be revised, and by somebody who should
not have to read SMTP code to do it.

BOTH EMAILS AVOID SAYING ANYTHING A STRANGER SHOULD NOT READ. Neither names the
organization, the inviting admin, or the business unit. A set-password link may sit in a
forwarded thread or an unlocked phone, and the address it was sent to is not proof of who
is reading it. "You have been added to the SDLC Platform" is enough for the recipient to
act on and tells an interceptor nothing about the customer.

NO PASSWORD IS EVER IN AN EMAIL. Both flows send a single-use link and let the person
choose their own, so nothing here needs to be deleted from a mailbox afterwards, and the
Sent folder of whatever account sends these holds no credentials.
"""
from __future__ import annotations

from config.env import PUBLIC_APP_URL

_BRAND = "SDLC Platform"


def _link(token: str) -> str:
    return f"{PUBLIC_APP_URL.rstrip('/')}/reset-password?token={token}"


def _shell(heading: str, lead: str, cta_label: str, url: str, footer: str) -> str:
    """Inline-styled HTML. Table-free, single column, and every rule inline.

    Email clients strip <style> blocks and ignore most modern CSS, so this is written for
    the lowest common denominator rather than to match the app.
    """
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
            max-width:520px;margin:0 auto;padding:24px;color:#1a1a1a;line-height:1.5">
  <p style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;
            color:#6b7280;margin:0 0 24px">{_BRAND}</p>
  <h1 style="font-size:20px;font-weight:600;margin:0 0 12px">{heading}</h1>
  <p style="margin:0 0 24px">{lead}</p>
  <p style="margin:0 0 24px">
    <a href="{url}"
       style="display:inline-block;background:#1a1a1a;color:#ffffff;text-decoration:none;
              padding:11px 20px;border-radius:6px;font-weight:500">{cta_label}</a>
  </p>
  <p style="margin:0 0 8px;font-size:13px;color:#6b7280">
    If the button does not work, copy this link into your browser:
  </p>
  <p style="margin:0 0 24px;font-size:13px;word-break:break-all">
    <a href="{url}" style="color:#1a1a1a">{url}</a>
  </p>
  <p style="margin:0;font-size:13px;color:#6b7280;border-top:1px solid #e5e7eb;
            padding-top:16px">{footer}</p>
</div>"""


def invite_email(token: str, ttl_hours: int) -> tuple[str, str, str]:
    """(subject, text, html) for somebody an Org Admin has just onboarded.

    The account exists with no password at this point, so this link is the ONLY way in —
    which is why the expiry is stated plainly and the footer says who to ask when it has
    passed rather than leaving them stuck.
    """
    url = _link(token)
    subject = f"Set your password for the {_BRAND}"
    text = (
        f"You have been added to the {_BRAND}.\n\n"
        f"Set your password to sign in for the first time:\n{url}\n\n"
        f"This link can be used once and expires in {ttl_hours} hours.\n\n"
        "If it has expired, ask your administrator to send a new invitation.\n\n"
        "If you were not expecting this email, you can ignore it — no account can be "
        "used until a password is set.\n"
    )
    html = _shell(
        heading=f"You have been added to the {_BRAND}",
        lead="Set a password to sign in for the first time.",
        cta_label="Set your password",
        url=url,
        footer=(
            f"This link can be used once and expires in {ttl_hours} hours. If it has "
            "expired, ask your administrator to send a new invitation. If you were not "
            "expecting this email you can ignore it."
        ),
    )
    return subject, text, html


def reset_email(token: str, ttl_hours: int) -> tuple[str, str, str]:
    """(subject, text, html) for a requested password reset.

    Says explicitly that the current password still works. Without that line the
    recipient of an unrequested reset email cannot tell whether they have already been
    locked out, and the reasonable reaction to that ambiguity is panic.
    """
    url = _link(token)
    subject = f"Reset your {_BRAND} password"
    text = (
        f"Somebody asked to reset the password for this {_BRAND} account.\n\n"
        f"Choose a new password:\n{url}\n\n"
        f"This link can be used once and expires in {ttl_hours} hours.\n\n"
        "If you did not request this, you can ignore this email — your current password "
        "still works and nothing has changed.\n"
    )
    html = _shell(
        heading="Reset your password",
        lead=f"Somebody asked to reset the password for this {_BRAND} account.",
        cta_label="Choose a new password",
        url=url,
        footer=(
            f"This link can be used once and expires in {ttl_hours} hours. If you did "
            "not request this you can ignore this email — your current password still "
            "works and nothing has changed."
        ),
    )
    return subject, text, html
