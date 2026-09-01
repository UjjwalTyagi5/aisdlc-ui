"""The in-code gate on Consequential agent actions (§1.5).

THE TIER. `help/multi-track-agent-access-design.md` §1.5 splits what an agent does into
three: a **Safe** action it just does, a **Consequential** one that needs the owning
role's approval before it happens, and a **Sign-off** on the stage's output. Reading a
board is Safe. Creating, editing or deleting work items on a live board is
Consequential — `delete_board_item` calls itself IRREVERSIBLE in its own docstring.

WHY THIS MODULE EXISTS. Until now the only thing between the model and a real board was
a sentence in the tool docstring ("Always confirm with the user before calling this
tool"). The status doc records the same failure for Documentation's `open_docs_pr` and
`publish_to_sharepoint` — *"enforced by prompt text only"*, the tool node executes
whatever the model emits. A prompt is a request, not a control: it is not enforced, not
audited, and one jailbreak or one confused turn from being ignored.

WHAT COUNTS AS THE OWNER'S APPROVAL. `AGENT_OWNER_ROLE` names the role that owns each
agent (requirements → `ba`, design → `architect`), and `_PHASE_PERMISSION` names the
permission that role holds for the stage (`artifact:approve_requirements`,
`artifact:approve_design`). So "an owner approved this action" is, concretely, "the
person driving this turn holds the stage's approval permission". `can_user_approve` is
reused rather than re-derived, so there is one definition of the owning role and the
`admin:*` wildcard behaves the same here as on the gate.

WHAT THIS IS NOT. It is not the connector access level. `permits(level, "write")` asks
whether this PROJECT's stage may write to its board at all; this asks whether this
PERSON may authorise it. Both must hold and neither implies the other — a project with a
`both` grant still should not let a passing developer delete an epic.

It is also not the Sign-off gate. §20.3: an action that *puts work in front of people*
happens first and is signed off after; one that *ships outward* is signed off first. The
board write is the former, so this authorises the write, and `runs.gate_pending` handles
the separate acceptance of the stage's output afterwards. Two gates, not one.
"""
from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

#: Returned when nobody is signed in. Phrased for the model to relay verbatim.
_NO_ACTOR = (
    "This change needs a signed-in approver and this run has none — it is running in "
    "the background. Ask {owner} to make the change from the project chat, where the "
    "approval can be attributed to a person."
)

_UNRESOLVABLE = (
    "Your permissions could not be checked just now, so nothing was changed. "
    "Try again in a moment."
)

_NOT_OWNER = (
    "This is a consequential change that {owner} has to approve, and you don't hold "
    "that approval permission for the {stage} stage. The work can still be drafted "
    "here — ask {owner} to review it and apply it."
)


def _owner_label(stage: str) -> str:
    """The owning role, spelled the way a person would say it."""
    from shared.governance.routing import agent_owner_role  # noqa: PLC0415 — import cycle

    return {
        "ba": "a Business Analyst",
        "architect": "an Architect",
        "qa": "a QA engineer",
        "security_engineer": "a Security engineer",
        "devops_engineer": "a DevOps engineer",
        "data_engineer": "a Data engineer",
        "project_admin": "a Project Admin",
    }.get(agent_owner_role(stage), "this agent's owner")


async def owner_approved(stage: str) -> Tuple[bool, str]:
    """`(True, "")` when an owner is present and accountable for a Consequential action.

    FAILS CLOSED on every uncertainty. "We could not tell who is asking" and "we could
    not resolve what they may do" are both refusals — a Consequential action with no
    identified human is precisely the case the tier exists to prevent, so treating the
    unknown as permission would defeat the whole check.

    KNOWN CONSEQUENCE, stated here rather than discovered in production: a queued worker
    run sets no user (`set_user_id` is called on the copilot WebSocket path only), so
    Consequential actions from a background run refuse. Under §1.5 that is the correct
    answer — nobody approved that action — but it is a real behaviour change from
    "writes always went through". The right long-term fix is for an autonomous run to
    raise a gate and wait for a human, not to hand background runs a blanket exemption.
    Tracked in `help/requirements-design-e2e-plan.md` §5.
    """
    from config.ws_helper import get_tenant_id, get_user_id  # noqa: PLC0415

    owner = _owner_label(stage)
    user_id = get_user_id() or ""
    tenant_id = get_tenant_id() or ""
    if not user_id or not tenant_id:
        logger.info("consequential action refused: no actor in context (stage=%s)", stage)
        return False, _NO_ACTOR.format(owner=owner)

    try:
        from shared.authz.resolver import resolve_permissions_for_user  # noqa: PLC0415

        perms = await resolve_permissions_for_user(user_id, tenant_id)
    except Exception as exc:  # noqa: BLE001 — an unresolvable permission set is not an empty one
        logger.warning(
            "consequential action refused: permissions unresolvable (stage=%s user=%s): %s",
            stage, user_id, type(exc).__name__,
        )
        return False, _UNRESOLVABLE

    from shared.services.orchestrator.gate_routing import can_user_approve  # noqa: PLC0415

    if not can_user_approve(perms, stage):
        logger.info(
            "consequential action refused: %s is not an owner of %s", user_id, stage
        )
        return False, _NOT_OWNER.format(owner=owner, stage=stage.replace("_", " ").title())
    return True, ""
