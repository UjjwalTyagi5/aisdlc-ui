"""The org-wide predicate behind every scoped aggregate and the workspace list.

Worth pinning on its own because two separate privilege escalations came from
getting it wrong in the same way — treating `workspace:manage` as org-wide
authority when it is authority over ONE unit:

  * GET  /workspaces  listed every sibling unit to a Business Unit Admin.
  * POST /workspaces  let a Business Unit Admin create sibling units.

Both were previously masked by a filter in the Next.js tier, which disappeared
when the frontend stopped serving fixtures. No database needed: the predicate
reads request.state.permissions and nothing else.
"""
from types import SimpleNamespace

from shared.authz.read_scope import ORG_WIDE_PERMISSIONS, is_org_wide


def _request(permissions):
    return SimpleNamespace(state=SimpleNamespace(permissions=permissions, user_id="u1"))


def test_admin_wildcard_is_org_wide():
    assert is_org_wide(_request(["admin:*"])) is True


def test_settings_manage_is_org_wide():
    # settings:manage is org-admin-only, so it implies whole-organization reach.
    assert is_org_wide(_request(["settings:manage"])) is True


def test_workspace_manage_alone_is_not_org_wide():
    """The regression test. A unit admin manages their unit, not the organization."""
    assert is_org_wide(_request(["workspace:manage"])) is False
    assert is_org_wide(_request(["workspace:manage", "member:manage", "cost:view"])) is False


def test_empty_and_missing_permissions_are_not_org_wide():
    assert is_org_wide(_request([])) is False
    assert is_org_wide(SimpleNamespace(state=SimpleNamespace())) is False


def test_org_wide_set_is_exactly_these_two():
    """A new entry here widens every scoped endpoint at once — make it deliberate."""
    assert set(ORG_WIDE_PERMISSIONS) == {"admin:*", "settings:manage"}
