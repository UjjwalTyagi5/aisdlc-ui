"""Grant `governance:decide` to the six delivery roles that own an agent-access
stage-two decision.

`agent_access` is a two-stage governance request: stage one is the Project Admin
saying this person should be doing this work, stage two is the AGENT'S OWNER
(`shared/governance/routing.py::AGENT_OWNER_ROLE`) saying the agent should do it
for them — `ba` (requirements), `architect` (design/development/review/discovery/
strategy/migration_mapping), `qa` (testing/validation), `security_engineer`
(security), `devops_engineer` (deployment), `data_engineer` (data_engineering).
Only `documentation` is owned by `project_admin`, which already held
`governance:decide` since 0019.

`POST /governance-approvals/{id}/decide` is gated on `governance:decide`
(shared/routers/governance_requests.py) with no carve-out per request type. Before
this migration, none of the six delivery roles above held it, so nobody who was
actually the agent's owner could ever pass that permission floor to decide stage
two of their own agent's access request — a flat 403 before decide()'s own
`decider_role != request["currentApproverRole"]` check was ever reached. Net
effect: stage two of `agent_access` was reachable only for the `documentation`
phase, permanently stuck for the other twelve. See
`shared/governance/effects.py::_apply_agent_access`, the effect this unblocks.

SAFE TO GRANT BROADLY, not a platform-wide widening: `agent_access` stage two is
the ONLY place any of these six roles is ever `currentApproverRole` —
`GOVERNANCE_APPROVER_ROLE`/`REQUEST_ESCALATION_CHAIN` are exhaustively
{project_admin, bu_admin, org_admin} for every other request type. decide()'s
existing role-match check narrows the grant to exactly the requests actually
routed to each role, so holding `governance:decide` does not let a delivery role
decide anyone else's request. See
tests/test_enterprise_rbac_catalog.py::test_agent_access_stage_two_owner_roles_hold_governance_decide.

WHY A MIGRATION. Same reasoning as 0019: `role_permissions` is code-owned and
reconciled from `_ROLE_PERMISSIONS` on boot, but `assert_rbac_catalog` verifies
BEFORE seeding and raises `RbacCatalogDriftError` on any difference, self-seeding
only when `roles` is empty. A code-matrix change without its data migration
therefore makes an existing database refuse to start.

Unlike 0019, `governance:decide` is not new — no `permissions` row to insert here,
only new `role_permissions` edges for a permission that already exists.

Revision ID: 0037_agent_owner_decide

(The id is kept short deliberately: `alembic_version.version_num` is varchar(32),
and a longer one fails at the very END of the migration — every statement
succeeds, then the version stamp raises StringDataRightTruncation and the whole
transaction rolls back, so the log reads "Running upgrade …" and nothing was
applied. Hit exactly this once while drafting this migration; the original id,
`0037_agent_owner_governance_decide`, was 34 characters.)

Revises: 0036_merge_heads_6
"""
from alembic import op

revision = "0037_agent_owner_decide"
down_revision = "0036_merge_heads_6"
branch_labels = None
depends_on = None

_PERMISSION = "governance:decide"
_ROLES = ["ba", "architect", "qa", "devops_engineer", "security_engineer", "data_engineer"]


def upgrade() -> None:
    for role in _ROLES:
        op.execute(
            "INSERT INTO role_permissions (role_name, permission_name) "
            "VALUES ('%s', '%s') ON CONFLICT (role_name, permission_name) DO NOTHING"
            % (role, _PERMISSION)
        )


def downgrade() -> None:
    for role in _ROLES:
        op.execute(
            "DELETE FROM role_permissions WHERE role_name = '%s' AND permission_name = '%s'"
            % (role, _PERMISSION)
        )
