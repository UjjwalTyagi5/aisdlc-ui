"""Reading an infrastructure plan before anybody approves it — phase 7.

YOU CANNOT APPROVE WHAT YOU CANNOT READ. Provisioning is the most consequential thing
this platform will ever do: a pipeline run deploys code that can be rolled back, and a
resource plan can delete a database. Before an Azure connector creates anything, the
approval gate needs a request an approver can actually understand — which means the
plan has to be summarised into "what appears, what changes, and what disappears".

THE CASE THIS FILE EXISTS FOR IS REPLACEMENT. Terraform reports a replace as
`["delete", "create"]` and Azure's what-if as its own change type, and both read like an
update in any summary that counts actions naively. A replaced storage account is a
DELETED storage account, and the difference between "3 updates" and "1 deletion and 2
updates" is the difference between a routine approval and somebody losing data.

WHAT NO PLAN MEANS. A plan that could not be read is not an empty plan. "Nothing will
change" and "nobody looked" are different sentences, and only one of them is safe to
approve — so an unreadable plan is an error, never a zero.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

#: Terraform action lists → what actually happens. The two replace forms are the point:
#: a replace destroys the resource, whichever order it does it in.
_TF_ACTIONS: Dict[tuple, str] = {
    ("no-op",): "unchanged",
    ("read",): "unchanged",
    ("create",): "create",
    ("update",): "update",
    ("delete",): "delete",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
}

#: Azure "what-if" change types → the same vocabulary.
_ARM_CHANGES: Dict[str, str] = {
    "Create": "create",
    "Delete": "delete",
    "Modify": "update",
    "Deploy": "update",
    "NoChange": "unchanged",
    "Ignore": "unchanged",
    "Unsupported": "unknown",
}

#: Changes that destroy something. A replace is here because it is a delete wearing an
#: update's clothes.
DESTRUCTIVE = ("delete", "replace")

#: Resource types whose loss is not recoverable by re-running the plan. Naming them is
#: not a substitute for reading the plan, but a deletion in this list is the one an
#: approver must not skim past.
_STATEFUL_HINTS = (
    "sql", "postgres", "mysql", "mariadb", "cosmos", "storage", "database",
    "keyvault", "key_vault", "disk", "backup", "redis",
)


class PlanUnreadable(Exception):
    """The plan could not be parsed.

    Raised rather than returning an empty summary, because an empty summary is
    indistinguishable from "this plan changes nothing" — and that is the one wrong
    answer that gets approved without a second thought.
    """


def _is_stateful(resource_type: str) -> bool:
    low = (resource_type or "").lower()
    return any(h in low for h in _STATEFUL_HINTS)


def _from_terraform(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rc in doc.get("resource_changes") or []:
        change = rc.get("change") or {}
        actions = tuple(change.get("actions") or [])
        out.append({
            "address": rc.get("address") or rc.get("name") or "?",
            "type": rc.get("type") or "",
            "action": _TF_ACTIONS.get(actions, "unknown"),
            "raw_action": list(actions),
        })
    return out


def _from_arm_whatif(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ch in doc.get("changes") or []:
        rid = ch.get("resourceId") or ""
        # An ARM resource id ends /providers/<ns>/<type>/<name>; the type is what tells
        # a reader whether this is a load balancer or a database.
        rtype = ""
        parts = [p for p in str(rid).split("/") if p]
        if "providers" in parts:
            i = parts.index("providers")
            rtype = "/".join(parts[i + 1:i + 3]) if len(parts) > i + 2 else ""
        out.append({
            "address": rid or "?",
            "type": rtype,
            "action": _ARM_CHANGES.get(str(ch.get("changeType") or ""), "unknown"),
            "raw_action": [ch.get("changeType")],
        })
    return out


def summarise_plan(plan_json: str) -> Dict[str, Any]:
    """Turn a Terraform plan or an Azure what-if result into something approvable.

    Accepts either format and reports the same shape, because an approver should not
    have to learn two vocabularies to answer the same question.
    """
    if not (plan_json or "").strip():
        raise PlanUnreadable(
            "No plan was given. An empty plan and an unread plan look identical in a "
            "summary, and only one of them is safe to approve."
        )
    try:
        doc = json.loads(plan_json)
    except (ValueError, TypeError) as exc:
        raise PlanUnreadable(f"The plan is not valid JSON ({type(exc).__name__}).") from None
    if not isinstance(doc, dict):
        raise PlanUnreadable("The plan is not a JSON object.")

    if "resource_changes" in doc:
        fmt, changes = "terraform", _from_terraform(doc)
    elif "changes" in doc:
        fmt, changes = "arm_what_if", _from_arm_whatif(doc)
    else:
        raise PlanUnreadable(
            "Unrecognised plan format — expected Terraform `terraform show -json` "
            "output (resource_changes) or an Azure what-if result (changes)."
        )

    counts: Dict[str, int] = {}
    for c in changes:
        counts[c["action"]] = counts.get(c["action"], 0) + 1

    destructive = [c for c in changes if c["action"] in DESTRUCTIVE]
    unknown = [c for c in changes if c["action"] == "unknown"]
    stateful_losses = [c for c in destructive if _is_stateful(c["type"])]

    warnings: List[str] = []
    if destructive:
        replaced = [c["address"] for c in destructive if c["action"] == "replace"]
        if replaced:
            warnings.append(
                f"{len(replaced)} resource(s) will be REPLACED, which destroys and "
                f"recreates them: {', '.join(replaced[:5])}. A replace reads like an "
                "update and is not one."
            )
        deleted = [c["address"] for c in destructive if c["action"] == "delete"]
        if deleted:
            warnings.append(
                f"{len(deleted)} resource(s) will be DELETED: {', '.join(deleted[:5])}."
            )
    if stateful_losses:
        warnings.append(
            "Some of what is being destroyed holds state — "
            f"{', '.join(c['address'] for c in stateful_losses[:5])}. Re-running the "
            "plan recreates the resource, not the data in it. Confirm there is a "
            "backup, and that somebody has restored from it before."
        )
    if unknown:
        warnings.append(
            f"{len(unknown)} change(s) could not be classified and are reported as "
            "unknown rather than assumed safe."
        )

    return {
        "format": fmt,
        "total": len(changes),
        "counts": counts,
        "changes": changes[:200],
        "destructive": destructive,
        "destroys_state": bool(stateful_losses),
        "unknown": unknown,
        "warnings": warnings,
        "requires_stricter_approval": bool(destructive or unknown),
        "summary": (
            f"{counts.get('create', 0)} to create, {counts.get('update', 0)} to update, "
            f"{counts.get('replace', 0)} to replace, {counts.get('delete', 0)} to delete"
            + (f", {counts.get('unchanged', 0)} unchanged" if counts.get("unchanged") else "")
        ),
    }


def detect_iac(markers: Dict[str, List[str]]) -> Dict[str, Any]:
    """Which infrastructure-as-code lives in this repo, from inspect_repo's markers.

    Reports what is there. It does NOT report what those files would do — that needs a
    plan, which needs credentials and a `terraform plan` this platform does not run.
    Saying "3 Terraform files found" and stopping is honest; implying they were
    understood is not.
    """
    found = {
        "terraform": list(markers.get("terraform") or []),
        "bicep": list(markers.get("bicep") or []),
        "arm": list(markers.get("arm_templates") or []),
    }
    kinds = [k for k, v in found.items() if v]
    return {
        "found": found,
        "kinds": kinds,
        "note": (
            "These files were found, not evaluated. What they would actually change "
            "needs a plan (`terraform show -json` or an Azure what-if), which this "
            "platform does not run — provide one and it can be summarised for approval."
        ) if kinds else "No infrastructure-as-code found in this repository.",
    }
