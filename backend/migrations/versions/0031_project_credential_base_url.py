"""Add project_integration_credentials.base_url.

A credential is only usable if it says WHERE it authenticates. Until now the
site URL — Jira/Confluence site, Azure DevOps organization, SonarQube server —
was resolved tenant-wide (secret-store key `jira-url`, `ado-org-url`, … or the
matching env var), which quietly asserted that every project in a tenant points
at the same instance. Different projects legitimately point at different ones,
and there was no UI anywhere to say so.

PLAINTEXT, like `account` and unlike `secret_ref`. A site URL is configuration,
not a credential: it has to stay queryable and re-editable, and the secret store
holds single opaque strings, so folding a URL in beside the token would mean
encoding a structure into the one slot reserved for the secret itself.

Nullable: rows written before this column existed carry no URL and fall back to
the tenant-wide chain exactly as they did, rather than failing to load.

Revision ID: 0031_project_credential_base_url
Revises: 0030_credential_label
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_project_credential_base_url"
down_revision = "0030_credential_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_integration_credentials",
        sa.Column("base_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_integration_credentials", "base_url")
