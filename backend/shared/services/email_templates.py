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

ON THE HTML: see `_shell`. It is written for Outlook and Gmail rather than for a browser,
which is why it looks like markup from 2005 on purpose.
"""
from __future__ import annotations

import html

from config.env import PUBLIC_APP_URL

_BRAND = "SDLC Platform"

# Hex, not oklch. `frontend/app/globals.css` defines the palette in oklch, which no email
# client understands — these are the same tokens converted, so the mail and the app it
# links to are recognisably one product.
_ACCENT = "#C94C0F"   # --primary
_INK = "#0A0A0A"      # --foreground
_MUTED = "#737373"    # --muted-foreground
_BORDER = "#E5E5E5"   # --border
_CANVAS = "#F4F4F5"   # the page behind the card

# NO IMAGES, INCLUDING THE LOGO. Clients block remote images by default, so an <img>
# wordmark is a broken box on first open for most recipients, and there is nowhere to host
# one anyway. The mark below is a table cell with a background colour: it renders
# everywhere and cannot fail to load.
#
# Nothing in this block may be load-bearing — Gmail strips <style> in several contexts.
# Delete it entirely and the email is still a correct light-mode email; these are only the
# rules that CANNOT be expressed inline.
_STYLE = """
  :root { color-scheme: light dark; supported-color-schemes: light dark; }
  @media (prefers-color-scheme: dark) {
    .canvas { background: #0A0A0A !important; }
    .card   { background: #171717 !important; border-color: #2A2A2A !important; }
    .ink    { color: #FAFAFA !important; }
    .body   { color: #D4D4D8 !important; }
    .muted  { color: #A1A1AA !important; }
    .well   { background: #0F0F0F !important; border-color: #2A2A2A !important; }
  }
  @media only screen and (max-width: 620px) {
    .card-pad { padding: 28px 22px !important; }
    .btn { display: block !important; text-align: center !important; padding: 0 !important; }
  }
"""


def _link(token: str) -> str:
    return f"{PUBLIC_APP_URL.rstrip('/')}/reset-password?token={token}"


def _duration(minutes: int) -> str:
    """A TTL in minutes, worded for a human: "10 minutes", "1 hour", "48 hours".

    THE COPY MUST NOT HARDCODE A UNIT. Both TTLs are environment variables now
    (INVITE_TOKEN_TTL_MINUTES / RESET_TOKEN_TTL_MINUTES), so the sentence has to follow
    whatever is configured. The previous wording interpolated the number into a fixed
    "... expires in {n} hours", which after the switch to minutes would have promised a
    ten-minute link ten HOURS of life — an email confidently telling the recipient the
    wrong thing, which is worse than saying nothing.

    Falls back to "1h 30m" only for values that are neither whole hours nor under an
    hour; nobody is expected to configure those, but the sentence still has to parse.
    """
    if minutes < 60:
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" + ("" if hours == 1 else "s")
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest}m"


def _shell(
    *,
    preheader: str,
    heading: str,
    lead: str,
    cta_label: str,
    url: str,
    notice: str,
    footer: str,
) -> str:
    """One transactional email, built for mail clients rather than for browsers.

    TABLES, NOT DIVS. This was previously a single styled <div>, with a comment calling
    "table-free" a virtue. It is the opposite of one: Outlook on Windows renders mail
    through Word, which has no flex, no float and unreliable max-width, so a div-only
    layout is precisely the one that collapses there. Nested tables with explicit widths
    are ugly and they work in every client.

    EVERY VISUAL RULE IS INLINE, for the same reason — see `_STYLE` for what is not, and
    why none of it matters if a client throws it away.

    The CTA is doubled: a VML <v:roundrect> for Outlook, which ignores padding and
    border-radius on an anchor, and a normal <a> for everything else. The two are mutually
    exclusive through conditional comments, so no client shows both.

    `preheader` is the grey snippet the inbox lists beside the subject. Left unset, clients
    scrape it from the first text in the body — which here would be "Or paste this link
    into your browser" — so it is worth writing deliberately.
    """
    url_attr = html.escape(url, quote=True)
    return f"""\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="x-apple-disable-message-reformatting" />
<meta name="color-scheme" content="light dark" />
<meta name="supported-color-schemes" content="light dark" />
<title>{html.escape(heading)}</title>
<!--[if mso]>
<xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml>
<![endif]-->
<style>{_STYLE}</style>
</head>
<body class="canvas" style="margin:0;padding:0;background:{_CANVAS};">

<div style="display:none;font-size:1px;color:{_CANVAS};line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{html.escape(preheader)}&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;</div>

<table role="presentation" class="canvas" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_CANVAS};">
<tr><td align="center" style="padding:32px 12px;">

  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

    <tr><td style="padding:0 0 20px 4px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td width="30" height="30" align="center" valign="middle" style="width:30px;height:30px;background:{_ACCENT};border-radius:7px;color:#ffffff;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:14px;font-weight:700;line-height:30px;">S</td>
        <td class="ink" style="padding-left:10px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15px;font-weight:700;letter-spacing:-.01em;color:{_INK};">{_BRAND}</td>
      </tr></table>
    </td></tr>

    <tr><td class="card" style="background:#ffffff;border:1px solid {_BORDER};border-radius:12px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td class="card-pad" style="padding:40px;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

        <h1 class="ink" style="margin:0 0 12px;font-size:22px;line-height:1.3;font-weight:600;letter-spacing:-.02em;color:{_INK};">{html.escape(heading)}</h1>
        <p class="body" style="margin:0 0 28px;font-size:15px;line-height:1.6;color:#3F3F46;">{html.escape(lead)}</p>

        <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td>
          <!--[if mso]>
          <v:roundrect href="{url_attr}" style="height:46px;v-text-anchor:middle;width:240px;" arcsize="18%" stroke="f" fillcolor="{_ACCENT}">
            <w:anchorlock xmlns:w="urn:schemas-microsoft-com:office:word"/>
            <center style="color:#ffffff;font-family:'Segoe UI',Arial,sans-serif;font-size:15px;font-weight:600;">{html.escape(cta_label)}</center>
          </v:roundrect>
          <![endif]-->
          <!--[if !mso]><!-- -->
          <a class="btn" href="{url_attr}" style="display:inline-block;background:{_ACCENT};color:#ffffff;font-size:15px;font-weight:600;line-height:46px;height:46px;padding:0 30px;border-radius:8px;text-decoration:none;">{html.escape(cta_label)}</a>
          <!--<![endif]-->
        </td></tr></table>

        <p class="muted" style="margin:28px 0 8px;font-size:13px;line-height:1.5;color:{_MUTED};">Or paste this link into your browser:</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
          <td class="well" style="background:#FAFAFA;border:1px solid {_BORDER};border-radius:7px;padding:11px 13px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.5;word-break:break-all;">
            <a class="muted" href="{url_attr}" style="color:{_MUTED};text-decoration:none;">{url_attr}</a>
          </td>
        </tr></table>

        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;"><tr>
          <td style="border-left:3px solid {_ACCENT};padding:2px 0 2px 13px;">
            <p class="body" style="margin:0;font-size:13px;line-height:1.6;color:#52525B;">{html.escape(notice)}</p>
          </td>
        </tr></table>

      </td></tr></table>
    </td></tr>

    <tr><td style="padding:20px 4px 0;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
      <p class="muted" style="margin:0;font-size:12px;line-height:1.6;color:{_MUTED};">{html.escape(footer)}</p>
      <p class="muted" style="margin:10px 0 0;font-size:12px;line-height:1.6;color:{_MUTED};">{_BRAND} &middot; automated message, please do not reply</p>
    </td></tr>

  </table>

</td></tr>
</table>
</body>
</html>"""


def invite_email(token: str, ttl_minutes: int) -> tuple[str, str, str]:
    """(subject, text, html) for somebody an Org Admin has just onboarded.

    The account exists with no password at this point, so this link is the ONLY way in —
    which is why the expiry is stated plainly and the footer says who to ask when it has
    passed rather than leaving them stuck.

    At the default ten minutes that footer is the part doing the work: an invite arrives
    unannounced, so most recipients will meet an expired link rather than a live one, and
    the email has to leave them knowing what to do about it.
    """
    url = _link(token)
    ttl = _duration(ttl_minutes)
    subject = f"Set your password for the {_BRAND}"
    text = (
        f"You have been added to the {_BRAND}.\n\n"
        f"Set your password to sign in for the first time:\n{url}\n\n"
        f"This link can be used once and expires in {ttl}.\n\n"
        "If it has expired, ask your administrator to send a new invitation.\n\n"
        "If you were not expecting this email, you can ignore it — no account can be "
        "used until a password is set.\n"
    )
    html_body = _shell(
        preheader=f"Set your password to sign in. This link expires in {ttl}.",
        heading=f"You have been added to the {_BRAND}",
        lead=(
            "An account has been created for this address. Choose a password to sign in "
            "for the first time."
        ),
        cta_label="Set your password",
        url=url,
        notice=(
            f"For your security this link can be used once and expires in {ttl}. If it "
            "has expired, ask your administrator to send a new invitation."
        ),
        footer=(
            "If you were not expecting this email you can ignore it — the account cannot "
            "be used until a password is set."
        ),
    )
    return subject, text, html_body


def reset_email(token: str, ttl_minutes: int) -> tuple[str, str, str]:
    """(subject, text, html) for a requested password reset.

    Says explicitly that the current password still works. Without that line the
    recipient of an unrequested reset email cannot tell whether they have already been
    locked out, and the reasonable reaction to that ambiguity is panic.
    """
    url = _link(token)
    ttl = _duration(ttl_minutes)
    subject = f"Reset your {_BRAND} password"
    text = (
        f"Somebody asked to reset the password for this {_BRAND} account.\n\n"
        f"Choose a new password:\n{url}\n\n"
        f"This link can be used once and expires in {ttl}.\n\n"
        "If you did not request this, you can ignore this email — your current password "
        "still works and nothing has changed.\n"
    )
    html_body = _shell(
        preheader=f"Choose a new password. This link expires in {ttl}.",
        heading="Reset your password",
        lead=f"Somebody asked to reset the password for this {_BRAND} account.",
        cta_label="Choose a new password",
        url=url,
        notice=f"For your security this link can be used once and expires in {ttl}.",
        footer=(
            "If you did not request this you can ignore this email — your current "
            "password still works and nothing has changed."
        ),
    )
    return subject, text, html_body
