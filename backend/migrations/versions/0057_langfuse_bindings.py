"""One Langfuse project per SDLC project, and the table that remembers which.

Traces landed in a single shared Langfuse project and were told apart by a `project:`
tag. That is an application-level promise: the read path injects the right filter and
nothing underneath enforces it, so one wrong predicate shows a project admin another
unit's prompts verbatim. PRD §17 calls organisation-wide trace leakage a release
blocker and §45 names project-level trace isolation as an R1 gate.

Each SDLC project now owns a Langfuse project, inside a Langfuse organization per
business unit, and this table maps ours to theirs. A key pair only reaches its own
project, so isolation is a property of the store rather than of the query — a wrong
filter returns nothing instead of somebody else's data.

WHY THE UNIQUE INDEX IS PARTIAL. `is_active = true` rather than a plain unique on
project_id: re-provisioning after a Langfuse rebuild has to leave the superseded row in
place — it records where a project's traces used to live, which is the only way to find
them afterwards — while exactly one binding is live.

KEYS ARE ENCRYPTED, NOT PLAINTEXT. They open a system holding every prompt and
completion the platform produces. A database dump should not also be a Langfuse
credential dump.

Revision ID: 0057_langfuse_bindings
Revises: 0056_artifact_upload_note
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0057_langfuse_bindings"
down_revision = "0056_artifact_upload_note"
branch_labels = None
depends_on = None

_TABLE = "langfuse_bindings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # RLS anchor. No FK by convention here: tenant_id is a policy column.
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("langfuse_org_id", sa.String(255), nullable=False),
        sa.Column("langfuse_project_id", sa.String(255), nullable=False, index=True),
        sa.Column("langfuse_project_name", sa.String(255), nullable=True),
        # Per row, not from config: a binding must keep pointing at the Langfuse it was
        # created against, or repointing the platform silently reads the wrong host.
        sa.Column("langfuse_host", sa.String(512), nullable=False),
        sa.Column("public_key_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_key_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_langfuse_binding_active_project",
        _TABLE,
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    # Same four statements every tenant-scoped table gets. current_setting(..., true)
    # returns NULL when the GUC is unset, which yields zero rows rather than a leak.
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


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_index("ix_langfuse_binding_active_project", table_name=_TABLE)
    op.drop_table(_TABLE)
