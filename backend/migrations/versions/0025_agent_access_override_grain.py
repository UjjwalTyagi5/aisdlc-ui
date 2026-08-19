"""agent_access_overrides: add the user_id grain alongside the existing role grain.

A row is either role-scoped (role set, user_id null) or person-scoped (user_id set,
role null), never both, never neither — enforced by ck_agent_access_override_grain.
The old single UNIQUE(project_id, role, phase) is replaced by two partial unique
indexes, one per grain, since a plain UNIQUE constraint can't express "unique only
when user_id IS NULL".

Revision ID: 0025_agent_access_override_grain
Revises: 0024_project_track
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025_agent_access_override_grain"
down_revision = "0024_project_track"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old single UNIQUE(project_id, role, phase) constraint — it's replaced
    # below by two partial unique indexes, one per grain. IF EXISTS because this
    # constraint may already be absent depending on the database's migration history.
    op.execute(
        "ALTER TABLE agent_access_overrides DROP CONSTRAINT IF EXISTS uq_agent_access_override"
    )

    # Alter role to be nullable
    op.alter_column("agent_access_overrides", "role", nullable=True)

    # Add the new user_id column
    op.add_column(
        "agent_access_overrides",
        sa.Column("user_id", sa.String(length=255), nullable=True),
    )

    # Add foreign key
    op.create_foreign_key(
        "fk_agent_access_override_user", "agent_access_overrides",
        "users", ["user_id"], ["id"], ondelete="CASCADE",
    )

    # Add check constraint to enforce exactly one of (role, user_id) is set
    op.create_check_constraint(
        "ck_agent_access_override_grain",
        "agent_access_overrides",
        "(role IS NOT NULL AND user_id IS NULL) OR (role IS NULL AND user_id IS NOT NULL)",
    )

    # Create partial unique index for role-scoped rows
    op.create_index(
        "uq_agent_access_override_role", "agent_access_overrides",
        ["project_id", "role", "phase"], unique=True,
        postgresql_where=sa.text("role IS NOT NULL"),
    )

    # Create partial unique index for user_id-scoped rows
    op.create_index(
        "uq_agent_access_override_user", "agent_access_overrides",
        ["project_id", "user_id", "phase"], unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Drop both partial unique indexes
    op.execute("""
    DROP INDEX IF EXISTS uq_agent_access_override_user;
    """)
    op.execute("""
    DROP INDEX IF EXISTS uq_agent_access_override_role;
    """)

    # Drop the check constraint and foreign key
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                   WHERE constraint_name = 'ck_agent_access_override_grain') THEN
            ALTER TABLE agent_access_overrides DROP CONSTRAINT ck_agent_access_override_grain;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                   WHERE constraint_name = 'fk_agent_access_override_user') THEN
            ALTER TABLE agent_access_overrides DROP CONSTRAINT fk_agent_access_override_user;
        END IF;
    END
    $$;
    """)

    # Drop user_id column
    op.execute("""
    ALTER TABLE agent_access_overrides DROP COLUMN IF EXISTS user_id;
    """)

    # Person-level grants (user_id set, role NULL) cannot be represented in the
    # pre-migration schema — the whole point of this migration was to allow that
    # grain. They do not survive a downgrade: delete them before restoring `role` to
    # NOT NULL, or any such row would violate the constraint and abort the downgrade
    # partway through. Filtered on `role IS NULL` rather than `user_id IS NOT NULL`
    # because the `user_id` column has already been dropped by this point — the two
    # predicates were equivalent while both columns existed (guaranteed by
    # ck_agent_access_override_grain, dropped above), and `role IS NULL` still
    # captures exactly the rows that must go now that only `role` remains.
    op.execute("DELETE FROM agent_access_overrides WHERE role IS NULL")

    # Alter role column back to NOT NULL
    op.alter_column("agent_access_overrides", "role", nullable=False)

    # Recreate the original single UNIQUE(project_id, role, phase) constraint
    op.execute("""
    ALTER TABLE agent_access_overrides
    ADD CONSTRAINT uq_agent_access_override UNIQUE (project_id, role, phase);
    """)
