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

TWO CHECKS, NOT ONE — `owner_approved` is only half. It asks whether this PERSON may
authorise the action; `authorize_consequential` also asks whether they actually DID, on
this turn. Both are needed and neither implies the other: a `ba` holds
`artifact:approve_requirements` by definition, so the role check alone lets the model
write to a live board — or call `delete_board_item`, which its own docstring calls
IRREVERSIBLE — without the human ever being asked. The Development agent reached the
same conclusion from the other side: its `push_gate_enabled`/`push_approved` pair asks
the human per turn but never checks that the human holds the owning role, so any project
member driving that chat can approve a push. Requirements and Design run both checks.

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


# ── The second half: did the owner actually say yes, on this turn? ────────────
#
# Deliberately NOT the Development agent's phrase list. Its `_is_push_approval` includes
# "push", "create pr" and "open the pr" — words that are approvals *there* because the
# action is always a push. On a board they are not: "push the release notes to the epic"
# describes work, it does not consent to it. Keeping a separate list is the point; a
# shared one would have to be the union, and the union is wrong for both.
_APPROVE_EXACT = frozenset({
    "yes", "y", "ok", "okay", "confirm", "confirmed", "proceed", "yep", "yeah", "sure",
    "do it", "go ahead", "approved", "approve",
})

#: Matched as a PREFIX, never as a substring. "i approve" appears inside "what happens
#: when I approve it", which is a question about the process and not consent to
#: anything — an early version of this used `p in t` and read exactly that as a yes.
_APPROVE_PHRASES = (
    "yes please", "go ahead", "please proceed", "i approve", "i confirm",
    "that's approved", "thats approved", "you have my approval",
)


def is_approval_message(*texts: object) -> bool:
    """True when the user's message for THIS turn is an explicit approval.

    THREE FORMS, ALL ANCHORED, in narrowing order of certainty: the whole message is an
    approval word; the clause before the first comma is ("yes, go ahead"); or the
    message opens with an approval phrase ("go ahead and create them").

    NEVER A SUBSTRING TEST. That is what makes this kind of check dangerous: an
    unanchored "approve" reads "do not approve that" and "who needs to approve this?" as
    consent, and the failure is silent and in the permissive direction. Anchoring costs
    a few phrasings that will simply be asked again; the alternative costs a live board.

    A question is never consent, whatever it contains.
    """
    t = " ".join(x for x in texts if isinstance(x, str)).strip().lower()
    if not t:
        return False
    t = t.rstrip(".!").strip()
    if t.endswith("?"):
        return False
    if t in _APPROVE_EXACT:
        return True
    if t.split(",", 1)[0].strip() in _APPROVE_EXACT:
        return True
    return any(t.startswith(p) for p in _APPROVE_PHRASES)


#: Shaped for the MODEL, not for the user, and it says so in the first four words. A
#: refusal that reads like a failure gets retried; the Development agent's gate messages
#: open with "NOT CREATED — this is NOT an error" for exactly that reason, and these
#: match. The instruction to STOP is the load-bearing part.
_NEEDS_CONSENT = (
    "⛔ NOT DONE — this is NOT an error, and nothing was changed. {action} is a "
    "Consequential action under §1.5 and needs the user's explicit approval first.\n"
    "Do this now, then STOP: {ask} Quote the exact change back to them so they are "
    "approving something specific. Do NOT retry the tool until the user replies with an "
    "approval such as \"yes\" or \"go ahead\"."
)


async def authorize_consequential(
    stage: str, *, action: str, ask: str = "ask the user to confirm the change.",
) -> Tuple[bool, str]:
    """The full Consequential check: may this person authorise it, AND did they.

    ORDER MATTERS. The role check runs first, so somebody without the approval
    permission hears that they lack the authority — telling them instead to "ask the
    user for approval" would be nonsense, since they are the user and their yes would
    not count.
    """
    ok, why = await owner_approved(stage)
    if not ok:
        return False, why

    from config.ws_helper import get_consequential_approved  # noqa: PLC0415

    if not get_consequential_approved():
        logger.info("consequential action needs explicit approval (stage=%s)", stage)
        return False, _NEEDS_CONSENT.format(action=action, ask=ask)
    return True, ""
