"""What approving a request actually DOES.

A decision that changes only the request's own status is a note, not a governance
action. When someone approves "raise Payments' cap to $16,000", the cap has to move —
otherwise the queue records agreement and the thing agreed to never happens, which is
the failure mode most likely to go unnoticed: everyone believes it was handled.

APPLIED IN THE SAME TRANSACTION as the status change, deliberately. Two statements that
can half-succeed give you a request marked approved over a budget that never moved, and
no way to tell from either row which of the two is wrong. `decide()` also runs its
status UPDATE (with its own AlreadyClosed optimistic-concurrency guard) BEFORE calling
into this module, so a request a concurrent decision already closed never reaches an
effect at all — closing the most likely way to hit the half-succeed above.

TWO EFFECTS ARE A DOCUMENTED EXCEPTION to "same transaction": `_apply_model_credential`
and `_apply_user_onboarding` both call into pre-existing service functions
(`model_grants.py`'s `set_project_selection`/`get_project_selection`;
`onboarding.py`'s `_onboard_person`) that open and commit their own independent
sessions rather than using the `db` passed into the effect. This was a real Critical
finding for the first of the two (`_apply_model_credential` was new code introduced
here); for the second, `_onboard_person`'s multi-session shape is pre-existing,
already-shipped behavior on the direct `POST /onboarding` route, not something this
effect introduces. The `decide()` reorder above still closes the concurrent-decision
race for both. The residual, accepted risk it does NOT close: an exception raised
AFTER one of these two effects' independent commit but before the outer transaction's
own commit (e.g. a failure in the notification/audit code that runs after
`apply_on_approve` returns) can leave the effect's write durably applied while the
request's own status change rolls back. Any NEW effect added to this file should write
only through the passed-in `db` session — do not add a third exception without a
reason as carefully argued as these two.

NOT EVERY TYPE HAS AN EFFECT, and that is not an omission. For `access_request` and
`other`, the DECISION IS THE OUTCOME — the approver then does the thing by hand on
the page that owns it, and the request is the record that they were asked and said
yes. Wiring a side effect onto those would mean this module performing grants it
has no business performing.

FOUR MORE TYPES USED TO LIVE IN `_DECISION_IS_THE_OUTCOME` and now have real effects
below — each approving-recorded-agreement-and-changing-nothing, until now:
  connector_access    granted no access; `_apply_connector_access` writes the real
                      `integration_grants`/`project_connector_access` row.
  model_credential    selected no model; `_apply_model_credential` adds it to the
                      project's `set_project_selection`.
  mcp_server          granted no server; `_apply_mcp_server` writes `integration_grants`.
  user_onboarding     onboarded nobody, so an Organization Admin who approved a
                      request still had to go and onboard the person by hand from
                      Users. `_apply_user_onboarding` reuses `_onboard_person`
                      (shared/routers/onboarding.py), the exact three-act body
                      `POST /onboarding` already performs, rather than a second copy.

`agent_access` used to be a fifth: approving recorded agreement and granted nothing,
so the requester still had to be given the extra agent by hand. See
`_apply_agent_access` below — it is the same `role_bindings.extra_agents` write the
manual "grant extra agent access" admin action already performs (PRD §43.2 step 3).
Only reachable for a phase whose owner holds `governance:decide` — see the migration
`0037_agent_owner_decide` and its docstring for the reachability fix this needed.

One type has an effect the backend cannot perform yet; it raises
`EffectNotAvailable` rather than silently approving into a void:
  role_assignment       closed by ASSIGNING a role, not by approving; the write lives in
                        `PATCH /workspaces/{id}/members/{userId}`.

`cross_bu_assignment` used to be a second: this comment once read "there is no
cross-BU grant table, so a loan cannot be recorded" — stale even when the request
lane shipped, since `cross_bu_grants` has existed since migration 0016. See
`_apply_cross_bu_assignment` below.

`project_creation` used to be a third: `POST /projects` created the project directly,
so approval had nothing to flip. Migration 0028 gave it a pending state
(`Project.approval_status`), so approving now activates it — see
`_apply_project_creation` below. It is also, so far, the only type whose REJECTION
needs a real effect rather than a bare status flip: every other type's rejection
leaves nothing behind to undo, but a rejected project_creation still has to flip its
project row out of `pending_approval` or the project is stuck looking live-pending
forever. See `apply_on_reject`.
"""
from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class EffectNotAvailable(Exception):
    """Approving would have no way to take effect, so the approval is refused.

    Refusing is the conservative answer. Recording an approval whose consequence
    cannot be applied leaves the request looking settled while nothing changed,
    and the person who approved it has no reason to check.
    """

    def __init__(self, request_type: str, detail: str):
        super().__init__(detail)
        self.request_type = request_type
        self.detail = detail


_NOT_APPLICABLE = {
    "role_assignment": (
        "A role-assignment request closes when the role is actually assigned, from "
        "Users or from this queue — not by approving it."
    ),
}

# The decision is the whole outcome. Approving records that the approver agreed;
# they then act on the page that owns the thing.
_DECISION_IS_THE_OUTCOME = frozenset(
    {
        "access_request",
        # `model_credential`, `mcp_server`, `user_onboarding` and `agent_access` all
        # used to live here too — see the module docstring above for what each one's
        # real effect now does and why. `connector_access` left this set before this
        # plan started (its own effect predates the work here).
        "other",
    }
)


async def apply_on_approve(db: AsyncSession, request: dict[str, Any]) -> Optional[str]:
    """Perform the consequence of approving `request`. Returns a short audit note.

    Raises EffectNotAvailable when the type's consequence has no implementation,
    so the caller can refuse the decision rather than record a hollow one.
    """
    rtype = request["type"]

    if rtype in _NOT_APPLICABLE:
        raise EffectNotAvailable(rtype, _NOT_APPLICABLE[rtype])

    if rtype in _DECISION_IS_THE_OUTCOME:
        return None

    if rtype == "budget_increase":
        return await _apply_budget_increase(db, request)
    if rtype == "project_archive":
        return await _apply_project_archive(db, request)
    if rtype == "project_creation":
        return await _apply_project_creation(db, request)
    if rtype == "project_settings_change":
        return await _apply_project_settings_change(db, request)
    if rtype == "model_provider_access":
        return await _apply_model_provider_access(db, request)
    if rtype == "model_credential":
        return await _apply_model_credential(db, request)
    if rtype == "connector_access":
        return await _apply_connector_access(db, request)
    if rtype == "mcp_server":
        return await _apply_mcp_server(db, request)
    if rtype == "agent_access":
        return await _apply_agent_access(db, request)
    if rtype == "cross_bu_assignment":
        return await _apply_cross_bu_assignment(db, request)
    if rtype == "user_onboarding":
        return await _apply_user_onboarding(db, request)
    if rtype.startswith("agent_default_"):
        return await _apply_agent_default(db, request)

    # Unreachable while REQUEST_TYPES and the branches above agree. Refusing beats
    # silently approving a type nobody wired up.
    raise EffectNotAvailable(rtype, f"No approval effect is defined for '{rtype}'.")


async def apply_on_reject(db: AsyncSession, request: dict[str, Any]) -> Optional[str]:
    """The consequence of REJECTING `request`, if any. Returns a short audit note.

    Every other type's rejection is a bare status flip with nothing to undo —
    nothing happened while the request was open, so there is nothing to unwind. Not
    so for `project_creation`: the project row already exists in `pending_approval`
    (created synchronously by POST /projects), so a rejection has to flip it to
    `rejected` explicitly or it is stuck reading as live-pending forever. Unlike
    `apply_on_approve`, never raises — a request can always be refused, even if the
    project it named is somehow already gone (see `_apply_project_creation_reject`).
    """
    if request["type"] == "project_creation":
        return await _apply_project_creation_reject(db, request)
    return None


async def _apply_budget_increase(db: AsyncSession, request: dict[str, Any]) -> str:
    """Move the cap to the amount that was asked for.

    THE TARGET IS WHICHEVER SCOPE THE REQUEST NAMES. A Project Admin whose project
    has exhausted its total budget raises this against their PROJECT; a Business
    Unit Admin raises it against their unit. Applying it to `workspaceId`
    unconditionally moved the unit's cap for a request that was never about the
    unit — raising it for every project at once, and leaving the one that actually
    ran out still blocked.

    The amount comes from `payload.requestedAmountUsd` — the figure recorded when
    the request was raised, NOT one supplied at decision time. The approver agreed
    to a number they could read; letting the decision carry its own would mean
    approving one figure and applying another.
    """
    payload = request.get("payload") or {}
    amount = payload.get("requestedAmountUsd")
    if amount is None:
        raise EffectNotAvailable(
            "budget_increase", "This request records no requested amount to apply."
        )
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise EffectNotAvailable("budget_increase", "The requested amount is not a number.")
    if amount < 0:
        raise EffectNotAvailable("budget_increase", "The requested amount is negative.")

    project_id = request.get("projectId")
    if project_id:
        result = await db.execute(
            text(
                "UPDATE projects SET monthly_budget_usd = :amt, updated_at = now() "
                "WHERE id = CAST(:p AS uuid)"
            ),
            {"amt": amount, "p": project_id},
        )
        if not result.rowcount:
            raise EffectNotAvailable("budget_increase", "That project no longer exists.")
        target, target_id, label = "project", project_id, "Project"
    else:
        result = await db.execute(
            text(
                "UPDATE workspaces SET monthly_budget_usd = :amt, updated_at = now() "
                "WHERE id = CAST(:w AS uuid)"
            ),
            {"amt": amount, "w": request["workspaceId"]},
        )
        if not result.rowcount:
            raise EffectNotAvailable("budget_increase", "That business unit no longer exists.")
        target, target_id, label = "workspace", request["workspaceId"], "Business unit"

    # The cap is enforced against LIFETIME spend (shared/services/budget_store.py),
    # so raising it is what unblocks a scope that has already spent its total —
    # nothing resets on its own at the end of the month.
    from shared.services.budget_guard import clear_budget_cache  # noqa: PLC0415
    clear_budget_cache()

    logger.info(
        "governance: budget_increase applied request=%s %s=%s amount=%s",
        request["id"], target, target_id, amount,
    )
    return f"{label} total cap set to {amount:.2f} USD."


# The project columns a settings request may write, mapped to the payload key the
# request carries. NOT derived from the PATCH body at apply time: an approver agreed
# to the fields shown on the request, and a mapping computed later could apply
# something they never saw. Anything absent from here is not applicable through this
# route no matter what the payload contains.
#
# `description` is DELIBERATELY ABSENT: `projects` has no such column (confirmed via
# the live baseline audit, 2026-08-29 — approving a queued description edit crashed
# with a raw 500 `UndefinedColumnError` on `UPDATE projects SET description = ...`,
# and the request was left permanently stuck open since the crash rolled back the
# status flip too). Leaving the key out is exactly the escape hatch this function's
# own docstring already documents ("a request can outlive a schema"; see `applied`
# below) — it makes a description edit a silent no-op on approval rather than a
# crash, matching `patch_project`'s direct-write branch (shared/routers/projects.py),
# where the same dead column already makes a direct edit a no-op instead of an error.
_SETTINGS_FIELDS: dict[str, str] = {
    "name": "display_name",
    "monthlyBudgetUsd": "monthly_budget_usd",
    "connectors": "connectors",
    "mcpServers": "mcp_servers",
    "toolAccessModes": "tool_access_modes",
}

# The JSONB ones, which have to be bound as JSON text rather than a dict.
_SETTINGS_JSON_FIELDS = frozenset({"connectors", "mcp_servers", "tool_access_modes"})


async def _apply_project_settings_change(db: AsyncSession, request: dict[str, Any]) -> str:
    """Write the settings edit its Project Admin proposed.

    The values come from `payload.changes` — recorded when the edit was submitted,
    never supplied at decision time. Same rule as budget_increase: the approver
    agreed to the values they could read, and letting the decision carry its own
    would mean approving one thing and applying another.

    A field the project no longer has, or one absent from _SETTINGS_FIELDS, is
    ignored rather than fatal: a request can outlive a schema, and refusing the
    whole edit because one key went stale would strand the rest of it.
    """
    payload = request.get("payload") or {}
    changes = payload.get("changes") or {}
    if not isinstance(changes, dict) or not changes:
        raise EffectNotAvailable(
            "project_settings_change", "This request records no settings to apply."
        )
    project_id = request.get("projectId") or request.get("targetRef")
    if not project_id:
        raise EffectNotAvailable(
            "project_settings_change", "This request names no project to apply to."
        )

    sets, params = [], {"p": str(project_id)}
    applied: list[str] = []
    for key, value in changes.items():
        column = _SETTINGS_FIELDS.get(key)
        if column is None:
            continue
        bind = f"v_{column}"
        if column in _SETTINGS_JSON_FIELDS:
            sets.append(f"{column} = CAST(:{bind} AS jsonb)")
            params[bind] = json.dumps(value) if value else None
        else:
            sets.append(f"{column} = :{bind}")
            params[bind] = value
        applied.append(key)

    if not sets:
        raise EffectNotAvailable(
            "project_settings_change", "None of the requested settings can be applied."
        )

    result = await db.execute(
        text(
            f"UPDATE projects SET {', '.join(sets)}, updated_at = now() "
            "WHERE id = CAST(:p AS uuid)"
        ),
        params,
    )
    if not result.rowcount:
        raise EffectNotAvailable("project_settings_change", "That project no longer exists.")

    # A budget among the changes moves an enforced cap, and the guard caches them.
    from shared.services.budget_guard import clear_budget_cache  # noqa: PLC0415
    clear_budget_cache()

    logger.info(
        "governance: project_settings_change applied request=%s project=%s fields=%s",
        request["id"], project_id, ",".join(applied),
    )
    return f"Applied {len(applied)} setting{'' if len(applied) == 1 else 's'}: {', '.join(applied)}."


async def _apply_project_archive(db: AsyncSession, request: dict[str, Any]) -> str:
    """Archive the project the request names.

    `target_ref` rather than `project_id`: the two are the same for this type, and
    reading the one the decision was recorded against keeps a request that was
    edited from archiving something else.
    """
    target = request.get("targetRef") or request.get("projectId")
    if not target:
        raise EffectNotAvailable("project_archive", "This request names no project.")
    try:
        _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        raise EffectNotAvailable("project_archive", "This request's project id is malformed.")

    result = await db.execute(
        text(
            "UPDATE projects SET archived = true, updated_at = now() "
            "WHERE id = CAST(:p AS uuid) AND archived = false"
        ),
        {"p": str(target)},
    )
    if not result.rowcount:
        # Already archived is success, not failure — the requester's intent holds
        # either way. Only a missing project is a real problem.
        exists = (
            await db.execute(
                text("SELECT 1 FROM projects WHERE id = CAST(:p AS uuid)"), {"p": str(target)}
            )
        ).first()
        if exists is None:
            raise EffectNotAvailable("project_archive", "That project no longer exists.")
        return "Project was already archived."
    logger.info("governance: project archived request=%s project=%s", request["id"], target)
    return "Project archived."


async def _apply_project_creation(db: AsyncSession, request: dict[str, Any]) -> str:
    """Activate the project the request named, then seat the contributors it deferred.

    Contributors were deliberately NOT granted at creation time — see
    shared/routers/projects.py's create_project — because seating them on a project
    that might still be rejected would hand out working access to something that
    doesn't officially exist yet. `payload.contributors` carries the (user_id, role,
    extra_agents) rows already validated and resolved at creation time — re-resolving
    emails or re-checking grant permission here would be redundant and would let a
    change to someone's email between creation and approval silently swap who gets
    seated; the approver agreed to the people named at raise time, same discipline as
    `_apply_budget_increase` reading its amount from `payload`, never the decision.
    """
    target = request.get("targetRef") or request.get("projectId")
    if not target:
        raise EffectNotAvailable("project_creation", "This request names no project.")
    try:
        _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        raise EffectNotAvailable("project_creation", "This request's project id is malformed.")

    row = (
        await db.execute(
            text(
                "UPDATE projects SET approval_status = 'active', approval_decided_by = :by, "
                "  approval_decided_at = now(), approval_reason = :reason, updated_at = now() "
                "WHERE id = CAST(:p AS uuid) AND approval_status = 'pending_approval' "
                "RETURNING display_name"
            ),
            {
                "p": str(target),
                "by": request.get("decidedBy"),
                "reason": request.get("reason"),
            },
        )
    ).first()
    if row is None:
        # Already active is success, not failure — same reasoning as
        # _apply_project_archive's "already archived". Only a missing project is a
        # real problem.
        existing = (
            await db.execute(
                text("SELECT display_name FROM projects WHERE id = CAST(:p AS uuid)"),
                {"p": str(target)},
            )
        ).first()
        if existing is None:
            raise EffectNotAvailable("project_creation", "That project no longer exists.")
        return "Project was already active."
    display_name = row[0]

    payload = request.get("payload") or {}
    contributors = payload.get("contributors") or []
    seated = 0
    if contributors:
        from shared.authz.grant import grant_role  # noqa: PLC0415 - import cycle
        from shared.services import notifications  # noqa: PLC0415 - import cycle

        for c in contributors:
            user_id = c.get("userId")
            role_name = c.get("roleName")
            if not user_id or not role_name:
                continue
            exists = (
                await db.execute(
                    text("SELECT 1 FROM users WHERE id = :u"), {"u": user_id}
                )
            ).first()
            if exists is None:
                # A since-deleted account must not block the rest of the project
                # from going live — skip and note it in the audit log, not the row.
                logger.warning(
                    "governance: project_creation contributor %s no longer exists, skipped",
                    user_id,
                )
                continue
            await grant_role(
                user_id, str(target), role_name,
                tenant_id=request["tenantId"], scope_kind="project",
                granted_by=request.get("decidedBy"),
            )
            extra_agents = c.get("extraAgents")
            if extra_agents:
                await db.execute(
                    text(
                        "UPDATE role_bindings SET extra_agents = CAST(:a AS jsonb) "
                        "WHERE user_id = :u AND scope_kind = 'project' AND scope_id = :p "
                        "  AND role_name = :r"
                    ),
                    {"a": json.dumps(extra_agents), "u": user_id, "p": str(target), "r": role_name},
                )
            await notifications.emit(
                db,
                tenant_id=request["tenantId"],
                kind="project_activated",
                title=f'"{display_name}" is now live',
                body=f"You were added as {role_name.replace('_', ' ')}.",
                href=f"/projects/{target}",
                recipient_user_id=user_id,
                project_id=str(target),
            )
            seated += 1

    logger.info(
        "governance: project activated request=%s project=%s contributors=%d",
        request["id"], target, seated,
    )
    return f"Project activated.{f' {seated} contributor(s) added.' if seated else ''}"


async def _apply_project_creation_reject(db: AsyncSession, request: dict[str, Any]) -> Optional[str]:
    """Flip the project out of pending on rejection. No contributor loop —
    they were never seated (see `_apply_project_creation`), so there is nothing to
    undo."""
    target = request.get("targetRef") or request.get("projectId")
    if not target:
        return None
    try:
        _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        return None

    result = await db.execute(
        text(
            "UPDATE projects SET approval_status = 'rejected', approval_decided_by = :by, "
            "  approval_decided_at = now(), approval_reason = :reason, updated_at = now() "
            "WHERE id = CAST(:p AS uuid) AND approval_status = 'pending_approval'"
        ),
        {
            "p": str(target),
            "by": request.get("decidedBy"),
            "reason": request.get("reason"),
        },
    )
    if not result.rowcount:
        return None
    logger.info("governance: project creation rejected request=%s project=%s", request["id"], target)
    return "Project marked rejected."


async def _apply_model_provider_access(db: AsyncSession, request: dict[str, Any]) -> str:
    """Grant the requesting Business Unit reach to the provider named — the same
    write `PUT /model/providers/grants` performs by hand (shared/routers/model.py's
    `set_model_provider_grants_route`, Org-Admin-only, delete-then-insert into
    `integration_grants` for the target unit) and the same `integration_grants`
    table `_apply_mcp_server` above writes for `kind='mcp'`. NOT a `model_providers`
    row: migration `0028_model_provider_grant_kind`'s own docstring is explicit that
    "a model_provider grant means 'this Business Unit may use provider X' — no
    model, no key, exactly like a connector grant" — `model_providers.status` is an
    unrelated enum (`unverified | valid | invalid`, frontend `ModelProviderStatus`)
    written only by a real API-key verify probe and read only by model-dispatch
    code that gates usability on `status = 'valid'`. An earlier version of this
    function wrote `status = 'active'` there directly — not a legal value —
    corrupting a live connection row into a shape the frontend's own schema
    rejects outright. This function must never touch `model_providers` at all.

    Validated against `catalog_providers()`, the same check the manual route
    performs, so a stale or hand-crafted provider slug refuses cleanly rather
    than granting a name the catalog does not recognize. `ON CONFLICT ... DO
    UPDATE` mirrors `_apply_mcp_server`'s idempotency: `integration_grants`'s
    primary key IS `(tenant_id, kind, target_ref, workspace_id)`
    (migration `0015_integration_grants`), so granting the same provider to the
    same unit twice — a re-approval, two separate requests — is a no-op-shaped
    upsert, never a duplicate-row error.

    NO org_admin re-check here, unlike `_apply_mcp_server`'s. That one is real
    defense-in-depth because `mcp_server` is tier-routed (absent from
    `routing.TYPE_ROUTED`) and can in principle reach a lower tier before
    escalating. `model_provider_access` is TYPE_ROUTED with a FIXED approver —
    `routing.GOVERNANCE_APPROVER_ROLE["model_provider_access"] = "org_admin"` —
    so `initial_approver_role` sets `current_approver_role = "org_admin"` at
    creation and it never changes (escalation is refused: `can_escalate` sees
    the request already at its ceiling). `decide()`'s own "is it yours to
    answer?" gate (`decider_role != request["currentApproverRole"]`) therefore
    already guarantees org_admin by the time this effect runs; re-checking here
    would be a redundant copy of a guarantee the routing layer, not this
    function, is responsible for keeping true.
    """
    from shared.services.model_catalog import list_providers as catalog_providers  # noqa: PLC0415

    payload = request.get("payload") or {}
    provider = (payload.get("providerModel") or {}).get("provider")
    if not provider:
        raise EffectNotAvailable("model_provider_access", "This request names no provider.")

    catalog = {p["provider"] for p in catalog_providers()}
    if provider not in catalog:
        raise EffectNotAvailable(
            "model_provider_access", f"{provider!r} is not a recognized model provider."
        )

    workspace_id = request.get("workspaceId")
    if not workspace_id:
        raise EffectNotAvailable(
            "model_provider_access", "This request names no business unit."
        )

    await db.execute(
        text(
            "INSERT INTO integration_grants "
            "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
            "VALUES (CAST(:t AS uuid), 'model_provider', :r, CAST(:w AS uuid), :by) "
            "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO UPDATE "
            "  SET granted_by = EXCLUDED.granted_by"
        ),
        {
            "t": request["tenantId"], "r": provider, "w": str(workspace_id),
            "by": request.get("decidedBy"),
        },
    )
    logger.info(
        "model_provider_access approved: unit %s -> provider %s", workspace_id, provider,
    )

    # REGISTER THE PROVIDER SO IT HAS A CARD TO BE GRANTED ON.
    #
    # The grant above and the Org Admin's Models grid answer to two different tables:
    # the grant is a row in `integration_grants`, the grid renders `model_providers`
    # connections. Approving therefore wrote a grant against a provider that had no
    # connection to hang it on — the approval "succeeded", the request closed, and the
    # Models page looked exactly as it had before, with no OpenAI anywhere and nothing
    # saying why. This is the same keyless registration the "Add provider" button
    # performs (AddProviderDialog -> create_provider with no api_key), which is the
    # step a human would otherwise have to know to go and repeat by hand.
    #
    # Keyless on purpose: a connection needs a credential before it can run, and the
    # approver does not necessarily hold one. Registering it keyless is what makes the
    # card exist, with the requesting unit already granted on it, so curating models
    # and adding a key are the only steps left.
    existing_connection = (
        await db.execute(
            text(
                "SELECT 1 FROM model_providers "
                " WHERE tenant_id = CAST(:t AS uuid) AND provider = :p LIMIT 1"
            ),
            {"t": request["tenantId"], "p": provider},
        )
    ).scalar()
    if existing_connection is None:
        label = next(
            (p["label"] for p in catalog_providers() if p["provider"] == provider), provider
        )
        try:
            from shared.services.model_config import (  # noqa: PLC0415
                DuplicateProviderNameError, create_provider,
            )

            await create_provider(
                request["tenantId"], provider=provider, display_name=label,
                api_key=None, created_by=request.get("decidedBy") or "system",
                workspace_id=None,
            )
            logger.info("model_provider_access approved: registered %s connection", provider)
        except DuplicateProviderNameError:
            # A connection under that display name already exists — nothing to add.
            logger.info("model_provider_access: %s already registered by name", provider)
        except Exception:  # noqa: BLE001 — the GRANT is the approval; registration is convenience
            logger.exception(
                "model_provider_access: could not register a %s connection", provider
            )

    # THE REQUESTED MODEL, NOT JUST ITS PROVIDER. These requests name one model
    # ("bedrock/ap-southeast-3/deepseek.v3.2 access"), and that name is what the
    # requester reads on the approval. Granting only the provider left the Models page
    # — which lists `org_model_grants`, a different table — exactly as it was: the BU
    # Admin was told their model was approved and then could not find it anywhere.
    # `integration_grants` above is the provider gate; both layers must say yes
    # (`get_bu_allowed` intersects them), so the provider grant alone reaches nothing.
    #
    # Additive on purpose. `set_bu_grants` would have been the obvious helper and is
    # the wrong shape here: it REPLACES the unit's set, so approving one model would
    # have silently revoked every other model the unit already had.
    model_id = (payload.get("providerModel") or {}).get("modelId")
    if model_id:
        row = (
            await db.execute(
                text(
                    "SELECT id, visibility, business_unit_ids FROM org_model_grants "
                    " WHERE tenant_id = CAST(:t AS uuid) AND provider = :p AND model_id = :m"
                ),
                {"t": request["tenantId"], "p": provider, "m": model_id},
            )
        ).first()
        if row is None:
            await db.execute(
                text(
                    "INSERT INTO org_model_grants "
                    "  (id, tenant_id, provider, model_id, visibility, business_unit_ids, created_by) "
                    "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :p, :m, 'specific', :bus, :by)"
                ),
                {
                    "id": str(_uuid.uuid4()), "t": request["tenantId"], "p": provider,
                    "m": model_id, "bus": json.dumps([str(workspace_id)]),
                    "by": request.get("decidedBy") or "system",
                },
            )
        elif row.visibility == "specific":
            # A `global` row already reaches every unit — adding to it would be a no-op.
            current = row.business_unit_ids or []
            if isinstance(current, str):
                current = json.loads(current)
            units = {str(u) for u in current}
            if str(workspace_id) not in units:
                units.add(str(workspace_id))
                await db.execute(
                    text("UPDATE org_model_grants SET business_unit_ids = :bus WHERE id = :id"),
                    {"bus": json.dumps(sorted(units)), "id": row.id},
                )
        logger.info(
            "model_provider_access approved: unit %s -> model %s", workspace_id, model_id,
        )
        return f"{model_id} granted to this business unit."

    return f"{provider} granted to this business unit."


async def _apply_model_credential(db: AsyncSession, request: dict[str, Any]) -> str:
    """Add the requested (provider, model_id) to the project's selection —
    the exact write set_project_selection already performs when a Project
    Admin does this by hand (Model Management). Reuses that function rather
    than reimplementing its reachability checks (NotAllowedForUnitError etc.):
    a request approved for a model the project's BU never made reachable
    should fail the same way the manual path does, not silently succeed
    through a second, looser route.
    """
    from shared.services.model_grants import (
        NotAllowedForUnitError,
        get_project_selection,
        set_project_selection,
    )

    payload = request.get("payload") or {}
    pm = payload.get("providerModel") or {}
    provider, model_id = pm.get("provider"), pm.get("modelId")
    project_id = request.get("projectId")

    if not project_id:
        raise EffectNotAvailable("model_credential", "This request names no project.")
    if not provider or not model_id:
        raise EffectNotAvailable(
            "model_credential", "This request names no provider or model to select."
        )

    current = await get_project_selection(request["tenantId"], project_id)
    already = any(
        e["provider"] == provider and e["model_id"] == model_id for e in current["selected"]
    )
    if already:
        return f"{provider}/{model_id} was already selected for this project."

    next_selection = [*current["selected"], {"provider": provider, "model_id": model_id}]
    try:
        await set_project_selection(
            request["tenantId"], project_id, next_selection, current.get("defaultKey")
        )
    except NotAllowedForUnitError as exc:
        raise EffectNotAvailable("model_credential", str(exc))

    logger.info(
        "governance: model_credential applied request=%s project=%s model=%s/%s",
        request["id"], project_id, provider, model_id,
    )
    return f"{provider}/{model_id} selected for this project."


async def _apply_agent_default(db: AsyncSession, request: dict[str, Any]) -> str:
    """Publish the proposed agent-profile (Behavior) OR agent-skill (Skills) version.

    `target_ref` is the DRAFT version's id, saved before the proposal was raised.
    Approving publishes exactly that version — which is why the proposal carries an
    id rather than the prompt/skill text: the approver agreed to a specific draft,
    and re-reading the text at decision time would publish whatever it had become.

    `target_ref` may name either an `AgentProfile` row or an `AgentSkill` row — every
    `agent_default_*` request (org/workspace/project) is routed here regardless of
    which resource kind raised it, via the SAME `agent_default_org`/`_workspace`/
    `_project` request types `AgentProfile.propose()` always used. Dispatch is a
    plain "try one, then the other" fallback on `target_ref`, not a payload
    discriminator: this function first looks up `target_ref` as an `AgentProfile`
    id; a miss falls through to `_apply_agent_default_skill`, which looks it up as
    an `AgentSkill` id. No `skill_default_*` type family was introduced for Skills
    proposals (considered and rejected — see the sub-project 3 design doc,
    "Considered and rejected" section): the approver-routing, self-approval rule,
    and audit/system-raised handling in `routing.py` are IDENTICAL for both resource
    kinds at every tier, so a type-level split would only add parallel entries to
    5+ shared registries (backend `routing.py`, frontend `governance.ts`/`routing.ts`,
    the Zod `GovernanceApprovalType` enum) for zero behavioral difference — only the
    row being flipped differs, and `target_ref`'s id already tells you which one
    that is.

    Reuses `apply_publish_flip` so this and `POST /agent-profiles/{id}/publish` (or
    `POST /agent-skills/{skill_key}/activate/{version}`) cannot disagree about what
    "published"/"active" means (exactly one active version per agent+scope, or per
    agent+scope+skill_key for Skills).
    """
    from shared.models.orm import AgentProfile  # noqa: PLC0415 - avoids a cycle at import
    from shared.routers.agent_profiles import apply_publish_flip  # noqa: PLC0415

    target = request.get("targetRef")
    if not target:
        raise EffectNotAvailable(request["type"], "This proposal names no draft version.")
    try:
        target_uuid = _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        raise EffectNotAvailable(request["type"], "This proposal's version id is malformed.")

    row = (
        await db.execute(select(AgentProfile).where(AgentProfile.id == target_uuid))
    ).scalar_one_or_none()
    if row is None:
        return await _apply_agent_default_skill(db, request, target_uuid)

    siblings = list(
        (
            await db.execute(
                select(AgentProfile).where(
                    AgentProfile.agent_id == row.agent_id,
                    AgentProfile.scope == row.scope,
                    AgentProfile.scope_id == row.scope_id,
                )
            )
        )
        .scalars()
        .all()
    )
    apply_publish_flip(siblings, row.id)
    await db.flush()

    # Published prompts are cached per agent; a stale cache means the approval
    # takes effect on the next process restart rather than the next run.
    try:
        from shared.services.prompt_runtime import invalidate_profile_cache  # noqa: PLC0415

        invalidate_profile_cache(str(request["tenantId"]), row.agent_id)
    except Exception:  # pragma: no cover - cache is best-effort, the write is not
        logger.warning("governance: profile cache invalidation failed for %s", row.agent_id)

    logger.info(
        "governance: agent default published request=%s profile=%s agent=%s v%s",
        request["id"], row.id, row.agent_id, row.version,
    )
    return f"Published {row.agent_id} v{row.version} at {row.scope} scope."


async def _apply_agent_default_skill(db: AsyncSession, request: dict[str, Any], target_uuid) -> str:
    """AgentSkill counterpart to the AgentProfile path above — same target_ref
    convention, same apply_publish_flip reuse, different ORM model. A proposal's
    target_ref may name either kind of row; this is the fallback once the
    AgentProfile lookup comes up empty."""
    from shared.models.orm import AgentSkill  # noqa: PLC0415
    from shared.routers.agent_profiles import apply_publish_flip  # noqa: PLC0415

    # deleted_at IS NULL on both queries below: without it, approving a proposal
    # against a skill that was soft-deleted after the proposal was filed silently
    # resurrects it (deleted rows aren't purged, only flagged) — the target lookup
    # would find the deleted draft, and the sibling flip would reactivate it right
    # alongside its still-deleted siblings (final whole-branch review, sub-project
    # 3, Important #4).
    row = (
        await db.execute(
            select(AgentSkill).where(
                AgentSkill.id == target_uuid, AgentSkill.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise EffectNotAvailable(request["type"], "That draft version no longer exists.")

    siblings = list(
        (
            await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == row.agent_id,
                    AgentSkill.scope == row.scope,
                    AgentSkill.scope_id == row.scope_id,
                    AgentSkill.skill_key == row.skill_key,
                    AgentSkill.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    apply_publish_flip(siblings, row.id)
    await db.flush()

    try:
        from shared.services.skill_runtime import invalidate_skills_cache  # noqa: PLC0415

        invalidate_skills_cache(str(request["tenantId"]), row.agent_id)
    except Exception:  # pragma: no cover - cache is best-effort, the write is not
        logger.warning("governance: skill cache invalidation failed for %s", row.agent_id)

    logger.info(
        "governance: skill published request=%s skill=%s agent=%s key=%s v%s",
        request["id"], row.id, row.agent_id, row.skill_key, row.version,
    )
    return f"Published skill '{row.skill_key}' v{row.version} at {row.scope} scope."


async def _apply_connector_access(db: AsyncSession, request: dict[str, Any]) -> str:
    """Grant the connector access that was asked for, at the tier that approved it.

    THE APPROVAL IS THE GATE, so it has to be the thing that acts. While this type
    sat in `_DECISION_IS_THE_OUTCOME` an approved request granted nothing — somebody
    still had to go and set it by hand, and until they did, an agent hit a denial
    holding an approved request that said otherwise.

    WHICH TIER MAY APPLY WHAT is the same rule the API enforces, restated here
    because an approval is a second door into the same write:

      org_admin   may grant a BUSINESS UNIT (integration_grants) — the only tier
                  that can, since a unit granting itself has no grant. Reach only:
                  the grant carries no level (migration 0024)
      any tier    may set a PROJECT's default (project_connector_access), bounded
                  by nothing above it, exactly as the direct endpoint now is

    Everything comes from the payload recorded when the request was RAISED, never
    from the decision: the approver agreed to a level they could read, and letting
    the decision carry its own would mean approving one thing and applying another.
    """
    from shared.authz.connector_access import is_access_level, label

    payload = request.get("payload") or {}
    target_ref = (payload.get("targetId") or "").strip()
    kind = (payload.get("kind") or "connector").strip()
    access = (payload.get("access") or "").strip()
    scope = (payload.get("scope") or "").strip() or (
        "project" if request.get("projectId") else "unit"
    )

    if not target_ref:
        raise EffectNotAvailable(
            "connector_access", "This request names no connector to grant."
        )
    if not is_access_level(access):
        raise EffectNotAvailable(
            "connector_access",
            "This request records no valid access level (read, write or read_write).",
        )
    if kind not in ("connector", "mcp"):
        raise EffectNotAvailable("connector_access", f"Unknown integration kind {kind!r}.")

    # THE THIRD DOOR carries the manifest check too. A request raised before a
    # connector's capabilities were known — or approved after they changed — would
    # otherwise write a level the connector cannot exercise, which is the hollow
    # grant this validation exists to prevent, arriving by the one route that skips
    # the endpoint doing the checking.
    if kind == "connector":
        from shared.authz.connector_capabilities import unsupported_reason

        reason = unsupported_reason(target_ref, access)
        if reason:
            raise EffectNotAvailable("connector_access", reason)

    decided_by_tier = request.get("currentApproverRole") or ""
    tenant_id = request["tenantId"]

    # ── a grant to the business unit ─────────────────────────────────────────
    if scope == "unit":
        if decided_by_tier != "org_admin":
            raise EffectNotAvailable(
                "connector_access",
                "Only an Organization Admin can give a business unit an integration. "
                "Escalate this request rather than approving it here.",
            )
        workspace_id = request.get("workspaceId")
        if not workspace_id:
            raise EffectNotAvailable(
                "connector_access", "This request names no business unit."
            )
        # No level on the row since migration 0024 — a grant is reach only, and the
        # read/write choice belongs to the project's stages. The `access` the request
        # asked for is deliberately NOT applied here: honouring it would put a level
        # back on the grant by the approval door after the direct endpoint stopped
        # accepting one.
        await db.execute(
            text(
                "INSERT INTO integration_grants "
                "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
                "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid), :by) "
                "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO UPDATE "
                "  SET granted_by = EXCLUDED.granted_by"
            ),
            {
                "t": tenant_id, "k": kind, "r": target_ref, "w": str(workspace_id),
                "by": request.get("decidedBy"),
            },
        )
        logger.info(
            "connector_access approved: unit %s -> %s %s", workspace_id, kind, target_ref
        )
        return (
            f"{target_ref} granted to the business unit. Each project chooses read "
            "or write per stage."
        )

    # ── a narrowing for one project ──────────────────────────────────────────
    project_id = request.get("projectId")
    if not project_id:
        raise EffectNotAvailable(
            "connector_access", "This request names no project to grant access on."
        )

    workspace_id = (
        await db.execute(
            text("SELECT workspace_id FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": str(project_id)},
        )
    ).scalar()
    if workspace_id is None:
        raise EffectNotAvailable("connector_access", "That project no longer exists.")

    granted = (
        await db.execute(
            text(
                "SELECT 1 FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = :w "
                "  AND kind = :k AND target_ref = :r"
            ),
            {"t": tenant_id, "w": workspace_id, "k": kind, "r": target_ref},
        )
    ).scalar()
    if granted is None:
        # AN ORG ADMIN APPROVING *IS* THE UNIT GRANT. Refusing here told the one person
        # with the authority to widen the unit's reach that somebody with that authority
        # had to act first — the Org Admin was reading their own permission back as a
        # blocker, on a request routed to them precisely because they hold it. A BU Admin
        # asking for Slack on their project could never be approved by anyone: the org
        # admin's only route was to leave the request, grant the unit by hand elsewhere,
        # and come back. Approving now does both steps, in the order they were always
        # meant to happen. Same INSERT and same reach-only semantics as the unit branch
        # above; the project narrowing below still applies the requested level.
        if decided_by_tier != "org_admin":
            raise EffectNotAvailable(
                "connector_access",
                f"This project's business unit has not been given {target_ref}. "
                "An Organization Admin has to grant it to the unit first.",
            )
        await db.execute(
            text(
                "INSERT INTO integration_grants "
                "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
                "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid), :by) "
                "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO UPDATE "
                "  SET granted_by = EXCLUDED.granted_by"
            ),
            {
                "t": tenant_id, "k": kind, "r": target_ref, "w": str(workspace_id),
                "by": request.get("decidedBy"),
            },
        )
        logger.info(
            "connector_access approved: unit %s -> %s %s (implied by a project request)",
            workspace_id, kind, target_ref,
        )
    # THE CEILING CHECK THAT STOOD HERE IS GONE with migration 0024. It refused an
    # approval that handed a project more than the unit's grant allowed; the grant
    # carries no level to exceed now. Reach is still checked above — approving access
    # to an integration the unit was never given remains refused.

    await db.execute(
        text(
            "INSERT INTO project_connector_access "
            "  (tenant_id, project_id, kind, target_ref, access, granted_by) "
            "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), :k, :r, :a, :by) "
            "ON CONFLICT (tenant_id, project_id, kind, target_ref) DO UPDATE "
            "  SET access = EXCLUDED.access, granted_by = EXCLUDED.granted_by"
        ),
        {
            "t": tenant_id, "p": str(project_id), "k": kind, "r": target_ref,
            "a": access, "by": request.get("decidedBy"),
        },
    )
    logger.info(
        "connector_access approved: project %s -> %s %s (%s)",
        project_id, kind, target_ref, access,
    )
    return f"{target_ref} set to {label(access)} for this project."


async def _apply_mcp_server(db: AsyncSession, request: dict[str, Any]) -> str:
    """Grant the MCP server that was asked for to the business unit named on the
    request — the same write `POST /integrations/access` performs for kind='mcp'
    (shared/routers/integration_access.py's `grant_integration_access`), found by
    reading that manual path first (557a86db's own commit message describes MCP
    servers as "governed identically — granted to units, consumed by projects" to
    connectors, and confirms it never merged the `mcp_server` request TYPE into
    `connector_access`; the two stay genuinely distinct end to end — separate
    `TYPE_ROUTED`/raisable-list entries in routing.py, untouched by that commit here
    and in governance_requests.py). NOT a thin wrapper around `_apply_connector_access`
    — the two request types are kept apart at every other layer, and a wrapper here
    would be the one place they secretly weren't.

    UNIT-LEVEL ONLY, unlike connector_access's two shapes. mcp_server's payload never
    carries an access level — `governance_requests.create_request` merges `access`
    into the payload for `connector_access` alone (see its own comment: "connector_
    access alone also carries an access level"), because there is no per-stage read/
    write question for an MCP server the way there is for a connector; a project
    wiring an MCP server to a stage is a `project_settings_change` (the direct
    `mcpServers` picker on Settings), not this request type. So there is no
    project_connector_access-shaped second branch to mirror here — a reach grant to
    the unit is the whole effect.

    ORG-ADMIN GATE, restated here for the same reason `_apply_connector_access`
    restates it: an approval is a second door into the same write, and
    `grant_integration_access`'s `_require_org_admin` has no kind-specific carve-out
    — 'mcp' is checked exactly like 'connector'. Skipping this check would let a
    request tier-routed to a lower approver (mcp_server is absent from TYPE_ROUTED,
    same as connector_access) grant a unit something only an Organization Admin may.
    """
    payload = request.get("payload") or {}
    target_ref = (payload.get("targetId") or "").strip()
    if not target_ref:
        raise EffectNotAvailable(
            "mcp_server",
            "This request doesn't yet name which server — ask the requester to specify "
            "one, or register it directly.",
        )

    workspace_id = request.get("workspaceId")
    if not workspace_id:
        raise EffectNotAvailable("mcp_server", "This request names no business unit.")

    decided_by_tier = request.get("currentApproverRole") or ""
    if decided_by_tier != "org_admin":
        raise EffectNotAvailable(
            "mcp_server",
            "Only an Organization Admin can give a business unit an MCP server. "
            "Escalate this request rather than approving it here.",
        )

    tenant_id = request["tenantId"]
    exists = (
        await db.execute(
            text(
                "SELECT 1 FROM mcp_servers WHERE id = CAST(:s AS uuid) "
                "  AND tenant_id = CAST(:t AS uuid)"
            ),
            {"s": target_ref, "t": tenant_id},
        )
    ).first()
    if exists is None:
        raise EffectNotAvailable("mcp_server", "That MCP server no longer exists.")

    await db.execute(
        text(
            "INSERT INTO integration_grants "
            "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
            "VALUES (CAST(:t AS uuid), 'mcp', :r, CAST(:w AS uuid), :by) "
            "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO UPDATE "
            "  SET granted_by = EXCLUDED.granted_by"
        ),
        {
            "t": tenant_id, "r": target_ref, "w": str(workspace_id),
            "by": request.get("decidedBy"),
        },
    )
    logger.info("mcp_server approved: unit %s -> mcp %s", workspace_id, target_ref)
    return "MCP server granted to the business unit."


async def _apply_agent_access(db: AsyncSession, request: dict[str, Any]) -> str:
    """Grant the requester the extra agent access their final approver just
    signed off on — the same field the manual 'grant extra agent access'
    admin action already writes (PRD §43.2 step 3), just reached through the
    two-stage request instead of an admin acting directly.

    Only reached at the FINAL decision. `decide()`'s two-stage block
    (shared/services/governance_requests.py, ~610-667) returns early — before
    apply_on_approve is ever called — whenever stage one's approval advances
    to a stage two (routing.next_agent_access_stage returns non-None). This
    function therefore only ever runs for a genuinely final approval: stage
    two itself, or stage one alone when there is no stage two (the
    `documentation` phase, whose owner IS project_admin).
    """
    payload = request.get("payload") or {}
    phase = payload.get("phase")
    user_id = request.get("requestedById")
    project_id = request.get("projectId")

    if not phase:
        raise EffectNotAvailable("agent_access", "This request names no agent.")
    if not user_id or not project_id:
        raise EffectNotAvailable(
            "agent_access", "This request names no person or project to grant access on."
        )

    row = (
        await db.execute(
            text(
                "SELECT extra_agents FROM role_bindings WHERE user_id = :u "
                "  AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
            ),
            {"u": user_id, "p": project_id},
        )
    ).first()
    if row is None:
        raise EffectNotAvailable(
            "agent_access", "This person no longer holds a role on this project."
        )
    current = list(row.extra_agents or [])
    if phase in current:
        return f"{phase} was already granted."
    current.append(phase)

    await db.execute(
        text(
            "UPDATE role_bindings SET extra_agents = CAST(:a AS jsonb) "
            "WHERE user_id = :u AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
        ),
        {"a": json.dumps(current), "u": user_id, "p": project_id},
    )
    logger.info(
        "governance: agent_access granted request=%s user=%s project=%s phase=%s",
        request["id"], user_id, project_id, phase,
    )
    return f"Granted access to the {phase} agent."


async def _apply_cross_bu_assignment(db: AsyncSession, request: dict[str, Any]) -> str:
    """Record the loan and seat the borrowed contributor on the project.

    Two writes, not one, because they answer different questions later:
    `cross_bu_grants` is the loan itself — whose person, lent from where,
    approved by whom — and is what `GET/DELETE /admin/cross-bu-grants`
    (shared/routers/project_scoped.py) reads and ends. `role_bindings` is the
    actual access, granted exactly like any other project member (the same
    `grant_role` call `add_project_member` makes) — without it the loan would
    be on record and the person still could not open the project.

    ON CONFLICT DO UPDATE rather than DO NOTHING: re-approving the same
    person for the same project (a role change, a second request after the
    first was later ended) is still one seat, not a duplicate — the table's
    own `uq_cross_bu_grant (user_id, project_id)` constraint says so.

    Everything comes from the payload recorded when the request was RAISED
    (`request_cross_bu_member` in shared/routers/project_members.py), not
    from the decision — the approver agreed to a role they could read.
    """
    payload = request.get("payload") or {}
    user_id = request.get("targetRef")
    role_name = (payload.get("roleName") or "").strip()
    project_id = request.get("projectId")
    parent_workspace_id = request.get("workspaceId")

    if not user_id or not project_id:
        raise EffectNotAvailable(
            "cross_bu_assignment", "This request names no person or no project to seat them on."
        )
    if not role_name:
        raise EffectNotAvailable("cross_bu_assignment", "This request records no role to grant.")

    from shared.authz.grant import TierConflictError, grant_role  # noqa: PLC0415 - avoids an import cycle

    try:
        await grant_role(
            user_id, project_id, role_name,
            tenant_id=request["tenantId"], scope_kind="project",
            granted_by=request.get("decidedBy"),
        )
    except (ValueError, TierConflictError) as exc:
        raise EffectNotAvailable("cross_bu_assignment", str(exc))

    await db.execute(
        text(
            "INSERT INTO cross_bu_grants "
            "  (id, tenant_id, user_id, parent_workspace_id, project_id, role, approved_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :u, CAST(:pw AS uuid), "
            "        CAST(:p AS uuid), :r, :ab) "
            "ON CONFLICT (user_id, project_id) DO UPDATE "
            "  SET role = EXCLUDED.role, approved_by = EXCLUDED.approved_by, approved_at = now()"
        ),
        {
            "t": request["tenantId"], "u": user_id, "pw": parent_workspace_id,
            "p": project_id, "r": role_name, "ab": request.get("decidedBy"),
        },
    )
    email = payload.get("email") or user_id
    logger.info(
        "cross_bu_assignment approved: user %s -> project %s as %s (lent from %s)",
        user_id, project_id, role_name, parent_workspace_id,
    )
    return f"{email} joined as {role_name}, on loan from their business unit."


async def _apply_user_onboarding(db: AsyncSession, request: dict[str, Any]) -> str | None:
    """Onboard the person the request named — the exact three acts
    `POST /onboarding` already performs (idempotent account, business-unit
    placement, a `role_assignment` sub-request for the unit's admin), reused
    via `_onboard_person` (shared/routers/onboarding.py) rather than
    duplicated.

    ORG-ADMIN ONLY, regardless of who technically holds `currentApproverRole`
    at this decision — `onboarding.py`'s own module docstring is explicit that
    `POST /onboarding` is "the Organization Admin's half of the handover", and
    a Project or BU Admin approver genuinely lacks the standing to create an
    account. `user_onboarding` is tier-routed rather than type-routed (absent
    from `routing.TYPE_ROUTED`), so a request raised below `org_admin` is
    decided by a Project Admin or BU Admin first and CAN close there without
    ever reaching this tier. Same shape as `_apply_connector_access` and
    `_apply_mcp_server`'s identical unit-tier guards: below `org_admin` this
    records agreement only (as it always did, before this type had an effect)
    until the request actually escalates that far.

    Always onboards as `contributor` into `request["workspaceId"]` — never a
    caller-supplied role. `user_onboarding`'s payload only ever carries an
    email (see `RaiseRequestPrefill.onboardEmail`, `RequestCreateInput`); the
    two-answer choice `POST /onboarding` itself offers (Business Unit Admin or
    Contributor) is deliberately not something a requester picks for someone
    else — an Organization Admin who wants to appoint a co-admin still does
    that directly, from Users.

    NOT authorization-checked the way `POST /onboarding` is: `_onboard_person`
    performs no `is_org_wide`/`assert_can_grant_role` call (both read a live
    HTTP request's session, which a governance decision has none of). This
    effect's own `currentApproverRole == "org_admin"` check IS this path's
    standing check, the same way `_apply_connector_access`'s and
    `_apply_mcp_server`'s `decided_by_tier` checks are theirs.
    """
    payload = request.get("payload") or {}
    email = payload.get("onboardEmail")
    if not email:
        raise EffectNotAvailable("user_onboarding", "This request names no email to onboard.")

    if request.get("currentApproverRole") != "org_admin":
        # Decision-is-the-outcome until the request reaches the tier that can
        # actually admit someone — see the docstring above.
        return None

    workspace_id = request.get("workspaceId")
    if not workspace_id:
        raise EffectNotAvailable(
            "user_onboarding", "This request names no business unit to place them in."
        )

    from shared.routers.onboarding import _onboard_person  # noqa: PLC0415

    result = await _onboard_person(
        db,
        tenant_id=request["tenantId"],
        email=email,
        display_name=None,
        workspace_id=workspace_id,
        role="contributor",
        actor_id=request.get("decidedBy"),
    )
    logger.info(
        "governance: user_onboarding applied request=%s email=%s", request["id"], email
    )
    return f"{result['email']} onboarded to this business unit."
