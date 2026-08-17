"""Single-use, expiring tokens for setting a password — invite and forgot-password.

Two flows, one table, because they are the same mechanism pointed at different moments:

  invite  an Org Admin onboards somebody. The account is created with NO password and an
          emailed link is the only way to set one.
  reset   somebody forgot theirs and asked for a link.

WHY ONLY A HASH IS STORED. `token_hash` holds SHA-256 of the token; the token itself is in
the email and nowhere else. A stolen database dump therefore yields no working links —
the same reasoning as `org_settings.entra_client_secret_ref`, where the column was made a
pointer so it could not eventually hold the secret. A reset token IS a credential for the
window it is alive, and a table of live credentials is the thing this design refuses to
create.

WHY GLOBAL, NOT TENANT-SCOPED. It joins `users`, which is deliberately global and non-RLS
(D-08): the lookup happens before any tenant context exists, because the caller presenting
a reset link is by definition not authenticated and has told us nothing but the token.
Putting this table under RLS would make it unreadable at exactly the moment it is needed.
Cross-tenant leakage is not a risk here — the token resolves to one `user_id` and grants
nothing except the ability to set that user's password.

SINGLE USE is enforced by `used_at`, not by deleting the row: a consumed token that is
still present lets "this link has already been used" be distinguished from "this link
never existed", which is the difference between a helpful page and a dead end. Expired and
consumed rows are pruned on issue rather than by a sweep job.
"""
import sqlalchemy as sa
from alembic import op
# Imported explicitly rather than reached through `sa.dialects.postgresql`: that
# attribute only exists once something else has imported the submodule, so relying on
# it makes this file's correctness depend on import order elsewhere.
from sqlalchemy.dialects import postgresql

revision = "0021_password_reset_tokens"
down_revision = "0020_project_update_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Real FK: a token for a deleted account is meaningless, and CASCADE means
        # removing a user cannot leave a live credential pointing at nothing.
        sa.Column(
            "user_id",
            sa.String(length=255),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SHA-256 hex of the token. Unique so a (vanishingly unlikely) collision is a
        # constraint violation rather than two accounts sharing one link.
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "purpose IN ('invite', 'reset')", name="ck_password_reset_purpose"
        ),
    )
    # The only lookup on the hot path: resolve a presented token.
    op.create_index(
        "ix_password_reset_tokens_hash", "password_reset_tokens", ["token_hash"]
    )
    # Supports "invalidate this user's outstanding tokens" when a new one is issued, so
    # requesting a second link retires the first rather than leaving two live.
    op.create_index(
        "ix_password_reset_tokens_user", "password_reset_tokens", ["user_id"]
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON password_reset_tokens TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_user", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
