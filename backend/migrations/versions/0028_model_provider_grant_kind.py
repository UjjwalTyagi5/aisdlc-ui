"""Allow integration_grants to carry model-provider grants.

Reuses the existing integration_grants table (0015_integration_grants) rather than
adding a parallel one — same reasoning as that migration's own docstring: "the same
decision made about two kinds of thing." A model_provider grant means "this Business
Unit may use provider X" — no model, no key, exactly like a connector grant.

Revision ID: 0028_model_provider_grant_kind
Revises: 0027_merge_heads_3
"""
from alembic import op

revision = "0028_model_provider_grant_kind"
down_revision = "0027_merge_heads_3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE integration_grants DROP CONSTRAINT ck_integration_grant_kind")
    op.execute(
        "ALTER TABLE integration_grants ADD CONSTRAINT ck_integration_grant_kind "
        "CHECK (kind IN ('connector', 'mcp', 'model_provider'))"
    )


def downgrade() -> None:
    # THE ROWS GO FIRST, and they have to.
    #
    # This downgrade returns the table to a schema where a model_provider grant cannot
    # be represented at all. Narrowing the constraint while such rows are still present
    # makes Postgres reject the ALTER outright — "check constraint
    # ck_integration_grant_kind is violated by some row" — so the downgrade failed on
    # any database that had ever granted a model provider. That is every real one, and
    # it is what broke test_alembic_migration_cycle.
    #
    # YES, THIS DELETES DATA. That is what downgrading this migration means: the rows
    # express a fact the older schema has no way to hold. The alternative — leaving
    # them and skipping the constraint — would silently produce a database that claims
    # to be at revision 0027 while holding rows 0027 forbids, which is worse than an
    # honest deletion. Re-granting is a UI action; a half-applied schema is not
    # something anybody can see, let alone undo.
    op.execute("DELETE FROM integration_grants WHERE kind = 'model_provider'")
    op.execute("ALTER TABLE integration_grants DROP CONSTRAINT ck_integration_grant_kind")
    op.execute(
        "ALTER TABLE integration_grants ADD CONSTRAINT ck_integration_grant_kind "
        "CHECK (kind IN ('connector', 'mcp'))"
    )
