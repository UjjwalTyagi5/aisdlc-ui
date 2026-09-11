"""Track 3 (Code Modernization) Phase 1: its first two agents get somewhere to write.

The Requirements agent in migration-intent mode (`requirements_modernization`) and
Discovery & Assessment (`discovery`) are Track 3's own agents — independent of
Portfolio 1's nine (help/multi-track-agent-access-design.md §1.4) — so each gets:

1. ITS OWN RUN COLUMN. `runs.migration_intent_payload` and `runs.discovery_artifacts`,
   alongside `requirements_payload`, `design_artifacts` and the rest. A migration brief
   is not a story backlog; writing it into `requirements_payload` would hand every
   Portfolio 1 reader of that column (the Requirements list, Design's requirements
   read) a shape it does not understand.

2. A PLACE IN THE DELIVERABLES CHECK. `orchestrator_deliverables.agent_id` is pinned to
   the registry by a CHECK constraint (0044). An agent missing from it has its
   Orchestrator output rejected at write time, which — because capture is deliberately
   non-fatal — surfaces as an agent that produced nothing.

3. A SIGN-OFF PERMISSION ITS OWNER HOLDS. `artifact:approve_requirements_modernization`
   and `artifact:approve_discovery` — both owned by the BA on this track (the design
   doc named the Architect for Discovery; the product decision is the BA) — each also
   held by `project_admin`,
   the fallback approver on every agent. Same reasoning as 0042: `role_permissions` is
   verified against `_ROLE_PERMISSIONS` at boot and a code change without its data
   migration makes an existing database refuse to start; and `assert_agent_ownership`
   refuses to boot if a stage's owner cannot approve it.

Revision ID: 0057_track3_phase1_agents
Revises: 0056_artifact_upload_note
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0057_track3_phase1_agents"
down_revision = "0056_artifact_upload_note"
branch_labels = None
depends_on = None

_TABLE = "orchestrator_deliverables"
_CHECK = "ck_orchestrator_deliverables_agent_id"

_PORTFOLIO_1 = (
    "'requirements','design','plan','development',"
    "'code_review','security','testing','deployment','documentation'"
)
_TRACK_3 = "'requirements_modernization','discovery'"

_GRANTS = (
    ("artifact:approve_requirements_modernization", ("ba", "project_admin")),
    ("artifact:approve_discovery", ("ba", "project_admin")),
)


def upgrade() -> None:
    op.add_column("runs", sa.Column("migration_intent_payload", postgresql.JSONB(), nullable=True))
    op.add_column("runs", sa.Column("discovery_artifacts", postgresql.JSONB(), nullable=True))

    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _CHECK, _TABLE,
        "agent_id IN ('requirements','design','plan','development',"
        "'code_review','security','testing','deployment','documentation',"
        "'requirements_modernization','discovery')",
    )

    for permission, roles in _GRANTS:
        op.execute(
            "INSERT INTO permissions (name) VALUES ('%s') ON CONFLICT (name) DO NOTHING"
            % permission
        )
        for role in roles:
            op.execute(
                "INSERT INTO role_permissions (role_name, permission_name) "
                "VALUES ('%s', '%s') ON CONFLICT (role_name, permission_name) DO NOTHING"
                % (role, permission)
            )


def downgrade() -> None:
    for permission, _roles in _GRANTS:
        # EVERY grant of the permission, not only the ones listed above: boot-time
        # reconciliation (`seed_rbac_catalog`) or an admin may have granted it to another
        # role since, and any remaining edge blocks the permission's own delete (FK).
        op.execute("DELETE FROM role_permissions WHERE permission_name = '%s'" % permission)
        op.execute("DELETE FROM permissions WHERE name = '%s'" % permission)

    # Rows the narrower constraint would refuse have to go before it can come back.
    op.execute(f"DELETE FROM {_TABLE} WHERE agent_id IN ({_TRACK_3})")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, f"agent_id IN ({_PORTFOLIO_1})")

    op.drop_column("runs", "discovery_artifacts")
    op.drop_column("runs", "migration_intent_payload")
