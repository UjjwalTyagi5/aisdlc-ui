"""Per-organization settings: SSO, MFA, and session lifetime.

Enterprise identity configuration lived only in environment variables, which makes it
a property of the DEPLOYMENT rather than of the organization — you cannot point one
org at its own Entra tenant without redeploying, and every org shares one MFA policy.

THE CLIENT SECRET IS NOT STORED HERE. Only `entra_client_secret_ref` — a pointer into
the secret store (Azure Key Vault in production, a Fernet-encrypted row locally). A
column that could hold the secret would eventually hold it: someone would write the
value "just for now", and it would then be in every database backup, every replica,
and every screenshot of a psql session. Making the column a reference removes that
option rather than discouraging it.

`session_timeout_minutes` is here because it is the same kind of decision as MFA — how
much friction this organization wants — and because the frontend session cookie
currently hard-codes eight hours, which is the wrong place for a policy.

Revision ID: 0009_org_settings
Revises: 0008_drop_approval_fallback
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_org_settings"
down_revision = "0008_drop_approval_fallback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_settings",
        # tenant_id IS the primary key: exactly one settings row per organization, so
        # "which row is current" can never be a question.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entra_tenant_id", sa.String(length=64), nullable=True),
        sa.Column("entra_client_id", sa.String(length=64), nullable=True),
        # A REFERENCE, never the secret. See the module docstring.
        sa.Column("entra_client_secret_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "mfa_required", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "session_timeout_minutes",
            sa.Integer(),
            nullable=False,
            server_default="480",
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
        # A timeout of zero would mean "expire immediately" and lock the org out of its
        # own platform; an unbounded one defeats the setting. One week is the ceiling.
        sa.CheckConstraint(
            "session_timeout_minutes BETWEEN 5 AND 10080",
            name="ck_org_settings_session_timeout",
        ),
    )

    op.execute("ALTER TABLE org_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON org_settings "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON org_settings "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE org_settings FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE ON org_settings TO sdlc_app;
                -- No DELETE: settings are edited, never removed. Deleting the row would
                -- silently revert an organization to defaults, including mfa_required.
                REVOKE DELETE ON org_settings FROM sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON org_settings")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON org_settings")
    op.drop_table("org_settings")
