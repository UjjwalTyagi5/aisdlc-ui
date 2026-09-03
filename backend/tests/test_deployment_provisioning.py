"""Reading an infrastructure plan before anyone approves it — PHASE 7.

THE CASE THIS FILE EXISTS FOR IS REPLACEMENT. Terraform reports a replace as
["delete", "create"], and any summary that counts actions naively calls it an update. A
replaced storage account is a DELETED storage account, and the gap between "3 updates"
and "1 deletion and 2 updates" is the gap between a routine approval and somebody losing
data.

The second theme is that an unreadable plan is an ERROR. An empty summary and "this
changes nothing" look identical to an approver, and only one of them is safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent.provisioning import (  # noqa: E402
    PlanUnreadable, detect_iac, summarise_plan,
)

pytestmark = pytest.mark.unit


def tf(*changes) -> str:
    return json.dumps({"resource_changes": [
        {"address": a, "type": t, "change": {"actions": list(acts)}}
        for a, t, acts in changes
    ]})


def arm(*changes) -> str:
    return json.dumps({"changes": [
        {"resourceId": rid, "changeType": ct} for rid, ct in changes
    ]})


# -- a replace is a deletion ---------------------------------------------------


@pytest.mark.parametrize("actions", [("delete", "create"), ("create", "delete")])
def test_a_replace_is_recognised_whichever_order_it_runs_in(actions):
    """create-before-destroy and destroy-before-create are both replacements. Only one
    of them starts with "delete", and reading the first action alone misses the other."""
    out = summarise_plan(tf(("azurerm_storage_account.a", "azurerm_storage_account", actions)))
    assert out["counts"]["replace"] == 1


def test_a_replace_counts_as_destructive():
    out = summarise_plan(tf(("azurerm_storage_account.a", "azurerm_storage_account",
                             ("delete", "create"))))
    assert out["destructive"]
    assert out["requires_stricter_approval"] is True


def test_a_replace_is_not_quietly_filed_as_an_update():
    """THE POINT. An approver reading "1 update" approves something entirely different
    from what the plan does."""
    out = summarise_plan(tf(("azurerm_storage_account.a", "azurerm_storage_account",
                             ("delete", "create"))))
    assert out["counts"].get("update", 0) == 0
    assert "REPLACED" in " ".join(out["warnings"])


def test_the_warning_says_a_replace_destroys_and_recreates():
    out = summarise_plan(tf(("azurerm_storage_account.a", "azurerm_storage_account",
                             ("delete", "create"))))
    assert "destroys and recreates" in " ".join(out["warnings"])


# -- what disappears -----------------------------------------------------------


def test_deletions_are_counted_and_named():
    out = summarise_plan(tf(("azurerm_resource_group.old", "azurerm_resource_group",
                             ("delete",))))
    assert out["counts"]["delete"] == 1
    assert "azurerm_resource_group.old" in " ".join(out["warnings"])


def test_losing_something_that_holds_state_is_called_out_separately():
    """Re-running the plan recreates the resource, not the data in it."""
    out = summarise_plan(tf(("azurerm_postgresql_server.db", "azurerm_postgresql_server",
                             ("delete",))))
    assert out["destroys_state"] is True
    assert "backup" in " ".join(out["warnings"])


def test_a_replaced_database_also_counts_as_losing_state():
    out = summarise_plan(tf(("azurerm_mssql_database.d", "azurerm_mssql_database",
                             ("delete", "create"))))
    assert out["destroys_state"] is True


def test_destroying_something_stateless_does_not_raise_the_data_alarm():
    """Crying wolf over a load balancer teaches people to skim the warning that
    matters."""
    out = summarise_plan(tf(("azurerm_lb.x", "azurerm_lb", ("delete",))))
    assert out["destroys_state"] is False


# -- an ordinary plan ----------------------------------------------------------


def test_a_create_only_plan_needs_no_stricter_approval():
    out = summarise_plan(tf(("azurerm_lb.x", "azurerm_lb", ("create",)),
                            ("azurerm_lb.y", "azurerm_lb", ("create",))))
    assert out["counts"]["create"] == 2
    assert out["destructive"] == []
    assert out["requires_stricter_approval"] is False


def test_no_ops_are_not_reported_as_changes():
    out = summarise_plan(tf(("azurerm_lb.x", "azurerm_lb", ("no-op",)),
                            ("azurerm_lb.y", "azurerm_lb", ("create",))))
    assert out["counts"]["unchanged"] == 1
    assert out["counts"]["create"] == 1


def test_the_summary_line_reads_as_a_sentence():
    out = summarise_plan(tf(("a.a", "azurerm_lb", ("create",)),
                            ("b.b", "azurerm_lb", ("delete",))))
    assert "1 to create" in out["summary"]
    assert "1 to delete" in out["summary"]


# -- Azure what-if -------------------------------------------------------------


def test_an_azure_what_if_result_is_understood_too():
    """An approver should not have to learn two vocabularies for the same question."""
    out = summarise_plan(arm(
        ("/subscriptions/s/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/a",
         "Delete"),
    ))
    assert out["format"] == "arm_what_if"
    assert out["counts"]["delete"] == 1


def test_the_resource_type_is_recovered_from_an_arm_id():
    """Without the type there is no way to tell a load balancer from a database."""
    out = summarise_plan(arm(
        ("/subscriptions/s/resourceGroups/rg/providers/Microsoft.Sql/servers/db", "Delete"),
    ))
    assert out["changes"][0]["type"] == "Microsoft.Sql/servers"
    assert out["destroys_state"] is True


def test_an_arm_modify_is_an_update():
    out = summarise_plan(arm(("/subscriptions/s/x", "Modify")))
    assert out["counts"]["update"] == 1


def test_an_unsupported_arm_change_is_unknown_not_safe():
    out = summarise_plan(arm(("/subscriptions/s/x", "Unsupported")))
    assert out["counts"]["unknown"] == 1
    assert out["requires_stricter_approval"] is True


# -- unreadable is not empty ---------------------------------------------------


def test_an_empty_plan_string_is_an_error():
    """An empty summary and "this changes nothing" look identical to an approver."""
    with pytest.raises(PlanUnreadable, match="safe to approve"):
        summarise_plan("")


def test_malformed_json_is_an_error():
    with pytest.raises(PlanUnreadable, match="not valid JSON"):
        summarise_plan("{not json")


def test_an_unrecognised_format_says_what_it_expected():
    with pytest.raises(PlanUnreadable, match="Terraform"):
        summarise_plan(json.dumps({"something": "else"}))


def test_a_json_array_is_refused():
    with pytest.raises(PlanUnreadable, match="not a JSON object"):
        summarise_plan("[]")


def test_a_terraform_action_it_has_never_seen_is_unknown_not_assumed_safe():
    out = summarise_plan(tf(("a.a", "azurerm_lb", ("teleport",))))
    assert out["counts"]["unknown"] == 1
    assert "could not be classified" in " ".join(out["warnings"])


def test_a_plan_with_no_changes_at_all_is_still_a_valid_read():
    """Distinct from unreadable: this one was read, and it really is empty."""
    out = summarise_plan(json.dumps({"resource_changes": []}))
    assert out["total"] == 0
    assert out["requires_stricter_approval"] is False


# -- finding the code ----------------------------------------------------------


def test_it_reports_what_it_found_without_claiming_to_understand_it():
    """Reading the files is not knowing their effect, and implying otherwise is how a
    plan gets approved on the strength of a description nobody verified."""
    out = detect_iac({"terraform": ["infra/main.tf"], "bicep": [], "arm_templates": []})
    assert out["kinds"] == ["terraform"]
    assert "not evaluated" in out["note"]


def test_it_asks_for_a_plan_rather_than_guessing():
    out = detect_iac({"terraform": ["main.tf"]})
    assert "needs a plan" in out["note"]


def test_a_repo_with_no_iac_says_so():
    out = detect_iac({})
    assert out["kinds"] == []
    assert "No infrastructure-as-code" in out["note"]
