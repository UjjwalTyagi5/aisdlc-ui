"""Outbound transactional email — onboarding invites and password resets.

Deliberately plain SMTP over the standard library, run off the event loop with
`asyncio.to_thread`. No new dependency: `aiosmtplib` would be marginally tidier and this
sends two kinds of message on human-initiated actions, where a thread hop costs nothing
and a lockfile change costs a review.

PROVIDER-AGNOSTIC BY CONSTRUCTION. Gmail, Azure Communication Services, SES, SendGrid and
Mailgun all speak SMTP, so switching is `SMTP_HOST` and credentials — see `config/env.py`
for why Gmail is fine for development and a poor production choice for this traffic
specifically.

NOT CONFIGURED IS A SUPPORTED STATE, not an error. With no `SMTP_HOST` the message is
logged and `send_email` returns False, so local dev and the test suite run without a mail
server. Logged at WARNING because an email that silently did not arrive is exactly the
failure that goes unnoticed until somebody cannot sign in.

NEVER RAISES. A failed invite email must not roll back the onboarding that triggered it —
the account and its binding are correct and the admin can resend. The return value says
whether it went, and callers that care report it; callers that do not still complete.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from config.env import (
    EMAIL_FROM,
    EMAIL_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_USE_SSL,
    SMTP_USE_TLS,
)

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """True when a message would actually be delivered.

    Exposed so a caller can tell an admin "onboarded, but no email was sent" instead of
    implying one arrived. `EMAIL_FROM` is part of the test: most providers reject a
    message with no envelope sender, so a host without a from address is not configured,
    it is misconfigured.
    """
    return bool(SMTP_HOST and EMAIL_FROM)


def _from_header() -> str:
    return formataddr((EMAIL_FROM_NAME or None, EMAIL_FROM))


def _build(to: str, subject: str, text_body: str, html_body: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from_header()
    msg["To"] = to
    msg["Subject"] = subject
    # Plain text is the real body and HTML is the alternative, not the other way round:
    # these messages carry a link somebody must be able to reach in any client, including
    # one that blocks HTML entirely.
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def _send_blocking(msg: EmailMessage) -> None:
    """The synchronous SMTP conversation. Runs in a worker thread."""
    context = ssl.create_default_context()
    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=20) as smtp:
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.ehlo()
        if SMTP_USE_TLS:
            smtp.starttls(context=context)
            # A second EHLO after STARTTLS is required, not optional: the server's
            # advertised capabilities (including AUTH) are renegotiated on the encrypted
            # channel, and some servers refuse AUTH without it.
            smtp.ehlo()
        if SMTP_USERNAME:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(msg)


async def send_email(
    to: str, subject: str, text_body: str, html_body: str | None = None
) -> bool:
    """Deliver one message. Returns whether it was sent. Never raises.

    A False return means "not delivered" for any reason — unconfigured, unreachable,
    rejected — and the reason is in the log. Callers must not treat it as fatal: see the
    module docstring on why a failed email cannot undo the action that prompted it.
    """
    if not to:
        return False

    if not is_configured():
        # The body is logged so a developer can follow a link that was never emailed.
        # Safe because the token in it is single-use and short-lived, and because this
        # branch only runs when there is no mail server to have sent it to.
        logger.warning(
            "SMTP not configured — email NOT sent. to=%s subject=%r\n%s",
            to, subject, text_body,
        )
        return False

    msg = _build(to, subject, text_body, html_body)
    try:
        await asyncio.to_thread(_send_blocking, msg)
        logger.info("email sent to=%s subject=%r", to, subject)
        return True
    except Exception as exc:
        # type(exc).__name__ rather than str(exc): SMTP errors routinely quote the
        # credentials or the full envelope back at you, and this line goes to a log.
        logger.warning(
            "email FAILED to=%s subject=%r: %s", to, subject, type(exc).__name__
        )
        return False
