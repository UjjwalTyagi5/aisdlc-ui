"""Fan one notification out to every human channel a tenant has configured.

THE GAP THIS CLOSES: SlackConnector.notify_slack has existed, been tested, and been
audited since M6 with ZERO production call sites. Gates opened, SLAs breached, and
nothing was ever delivered — notify_gate_pending wrote an audit row, and
emit_escalation_activity recorded `delegate_notified: False` as a hardcoded literal.
This module is the dispatch that was missing. Adding Microsoft Teams is what finally
made it worth building, and Slack comes along for the same ride.

DESIGN RULES, all load-bearing:

1. NEVER RAISES. Both call sites are best-effort seams whose own contract is that a
   notification failure must not break a gate or an activity. Every channel is wrapped
   independently, so a Teams outage cannot suppress Slack.
2. NO DEFAULT CHANNEL (REQ-M6-04). A tenant with no configured target gets no
   delivery — silently, and by design. The connectors still refuse a falsy channel;
   the resolvers in notification_targets decide whether to call them at all.
3. ERRORS ARE TYPE NAMES ONLY, never str(exc) — a webhook URL or bot token can appear
   in an exception message (the M1 decision).
4. Returns which channels actually delivered, so a caller can record the truth rather
   than assuming.
"""
from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)


async def notify_all(
    tenant_id: str,
    message: str,
    *,
    title: str = "",
    link_url: str = "",
) -> Dict[str, bool]:
    """Deliver `message` to every notification channel configured for this tenant.

    Args:
        tenant_id: Platform tenant whose channel configuration is used.
        message:   Plain-text body. Connectors enforce the length and secret-pattern
                   guards, so callers must not pass raw artifact content.
        title:     Optional heading (Teams renders it on the card).
        link_url:  Optional deep link back into the platform. Approvals link to the
                   existing authenticated UI rather than offering an inline button —
                   an inline callback would be a second, weaker approval path.

    Returns:
        {"slack": bool, "ms_teams": bool} — True where delivery succeeded. Channels
        that are not configured are absent from the mapping entirely.
    """
    results: Dict[str, bool] = {}
    if not tenant_id or not message:
        return results

    try:
        from shared.services.notification_targets import slack_target, teams_target
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_all: target resolver unavailable: %s", type(exc).__name__)
        return results

    # ── Slack ────────────────────────────────────────────────────────────
    try:
        target = await slack_target(tenant_id)
    except Exception:  # noqa: BLE001
        target = None
    if target:
        results["slack"] = await _deliver(
            tenant_id,
            kind="slack",
            operation="notify_slack",
            kwargs={
                "channel": target.get("channel", ""),
                "message": _with_link(message, link_url, title),
            },
        )

    # ── Microsoft Teams ──────────────────────────────────────────────────
    try:
        target = await teams_target(tenant_id)
    except Exception:  # noqa: BLE001
        target = None
    if target:
        results["ms_teams"] = await _deliver(
            tenant_id,
            kind="ms_teams",
            operation="notify_teams",
            kwargs={
                "message": message,
                "title": title,
                "link_url": link_url,
                "tenant_id": tenant_id,
                **target,
            },
        )

    return results


def _with_link(message: str, link_url: str, title: str) -> str:
    """Fold the title and deep link into a plain-text body for channels without cards."""
    parts = [f"*{title}*" if title else "", message, link_url]
    return "\n".join(p for p in parts if p)


async def _deliver(tenant_id: str, *, kind: str, operation: str, kwargs: dict) -> bool:
    """Send through one connector. Never raises; returns whether it landed."""
    try:
        from config.connector_factory import get_connector_for_session

        # `unrestricted` NAMED, because this is the fail-open door and it should read
        # as one. A tenant notification acts for no project and no stage: it is the
        # platform telling a human that a gate is waiting, not an agent reaching a
        # tool on a project's behalf. There is no stage whose grant could be checked.
        #
        # WITHOUT IT THIS WAS SILENTLY DEAD. Passing neither a project nor a stage nor
        # this flag yields ScopedConnector(raw, None), and `permits(None, "write")` is
        # False — so every write_adapter below raised ConnectorAccessDenied, which the
        # `except` swallows into `return False`. Slack and Teams delivery failed for
        # every tenant with nothing but a warning line, and "notifications are not
        # arriving" is not a symptom anyone traces back to an access level.
        connector = await get_connector_for_session(
            kind=kind, tenant_id=tenant_id, unrestricted=True,
        )
        await connector.write_adapter(operation, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        # Type name only — a webhook URL or token can appear in the message body.
        logger.warning(
            "notify_all: %s delivery failed for tenant=%r: %s",
            kind,
            tenant_id,
            type(exc).__name__,
        )
        return False
