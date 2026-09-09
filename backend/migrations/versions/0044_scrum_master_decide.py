"""Grant `governance:decide` to `scrum_master`, completing 0037.

0037 granted this to the six delivery roles that own an agent-access stage-two
decision — ba, architect, qa, security_engineer, devops_engineer, data_engineer —
enumerated from `shared/governance/routing.py::AGENT_OWNER_ROLE`.

IT MISSED scrum_master FOR THE SAME REASON THE WHOLE PHASE EXISTS. That map had no
`plan` entry at all, and `agent_owner_role()` answered a `"project_admin"` default for
any miss. So Plan looked like a project_admin-owned agent, project_admin already held
`governance:decide` since 0019, and nothing appeared wrong. The frontend
(`lib/roles.ts::AGENT_OWNER_ROLE`) said `plan: scrum_master` the whole time.

With `plan -> scrum_master` now correct in the backend map, stage two of an
`agent_access` request for the Plan agent routes to `scrum_master`. `POST
/governance-approvals/{id}/decide` is gated on `governance:decide` with no per-type
carve-out, so without this grant the Plan agent's own owner takes a flat 403 — before
decide()'s `decider_role != request["currentApproverRole"]` check is ever reached.
That is exactly the failure 0037 was written to close, left open for one role.

SAFE TO GRANT, on 0037's reasoning unchanged: `agent_access` stage two is the only
place `scrum_master` is ever `currentApproverRole` — `GOVERNANCE_APPROVER_ROLE` and
`REQUEST_ESCALATION_CHAIN` are exhaustively {project_admin, bu_admin, org_admin} for
every other request type — so decide()'s role-match check narrows this to precisely
the Plan-agent requests actually routed here. It does not let a scrum_master decide
anyone else's request. The cross-project gap 0037 documents (role NAMES compared, not
projectId) applies here identically and is parked with it, not widened by this.

WHY A MIGRATION. `role_permissions` is code-owned and reconciled from
`_ROLE_PERMISSIONS` on boot, but `assert_rbac_catalog` VERIFIES before seeding and
raises `RbacCatalogDriftError` on any difference, self-seeding only when `roles` is
empty. The code-matrix change without this data migration would make every existing
database refuse to start.

`governance:decide` already exists as a permission — this adds one
`role_permissions` edge, no `permissions` row.

Revision ID: 0044_scrum_master_decide

(Kept under 32 characters: `alembic_version.version_num` is varchar(32), and a longer
id fails at the very END — every statement succeeds, then the version stamp raises
StringDataRightTruncation and the whole transaction rolls back. See 0037.)

Revises: 0045_message_agent_id

RE-POINTED WHEN main WAS MERGED IN. This branch and main both grew from
`0043_deployments`, so both numbered their next migration 0044 — main's
`0044_orchestrator_deliverables` / `0045_message_agent_id`, and this one. Two roots on
the same parent is two alembic HEADS, and `alembic upgrade head` refuses to choose
between them.

Splicing this chain onto main's makes one line again:

    0043 -> 0044_orchestrator_deliverables -> 0045_message_agent_id
         -> 0044_scrum_master_decide -> ... -> 0055_artifact_delete

THE FILENAME NUMBERS NOW READ OUT OF ORDER, and that is the lesser evil. Alembic keys
on the `revision` string, not the filename, and every database already stamped at one of
these ids — the dev and test ones are at 0055_artifact_delete — would be orphaned by a
renumber: `alembic_version` would name a revision that no longer exists. Misleading
numbering is a reading annoyance; a stamp pointing at nothing is an unmigratable
database.

Revises (originally): 0043_deployments
"""
from alembic import op

revision = "0044_scrum_master_decide"
down_revision = "0045_message_agent_id"
branch_labels = None
depends_on = None

_PERMISSION = "governance:decide"
_ROLE = "scrum_master"


def upgrade() -> None:
    op.execute(
        "INSERT INTO role_permissions (role_name, permission_name) "
        "VALUES ('%s', '%s') ON CONFLICT (role_name, permission_name) DO NOTHING"
        % (_ROLE, _PERMISSION)
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE role_name = '%s' AND permission_name = '%s'"
        % (_ROLE, _PERMISSION)
    )
