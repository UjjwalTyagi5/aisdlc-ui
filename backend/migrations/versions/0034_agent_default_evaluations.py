"""Add agent_default_evaluations table (Agent Studio sub-project 4: evaluation-gated promotion).

Durable PASS/FAIL record for one evaluation run against one specific AgentProfile
or AgentSkill draft VERSION. Append-only — an evaluation is never edited, only
superseded by a fresh run against a new version. Nothing reads or writes this
table yet (that lands in later tasks of this sub-project); this migration is
storage-layer only.

Revision ID: 0034_agent_default_evaluations
Revises: 0033_project_integration_config
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0034_agent_default_evaluations"
down_revision = "0033_project_integration_config"
branch_labels = None
depends_on = None

_TABLE = "agent_default_evaluations"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 'profile' -> AgentProfile.id, 'skill' -> AgentSkill.id — mirrors
        # effects.py's existing target_ref dual-resolution convention.
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(length=50), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),  # org | workspace | project
        sa.Column("result", sa.String(length=8), nullable=False),  # pass | fail
        # Numeric(5,4), not Float — exact decimal for a 0.0000-1.0000 quality
        # score; Float would drift (same reasoning as eval_records.score).
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("evaluator_id", sa.String(length=255), nullable=False),
        sa.Column("evaluator_role", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_default_evaluations_tenant_id", _TABLE, ["tenant_id"]
    )
    # Supports latest_passing_evaluation's lookup by target.
    op.create_index(
        "ix_agent_default_evaluations_target_id", _TABLE, ["target_id"]
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {_TABLE} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON agent_default_evaluations TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_table(_TABLE)
