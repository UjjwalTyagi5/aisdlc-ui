"""merge budgets + requirements_artifacts heads

Revision ID: 0033_merge_budgets_reqs
Revises: 0031, 0032_budgets
Create Date: 2026-07-08 00:08:06.507292

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0033_merge_budgets_reqs'
down_revision: Union[str, Sequence[str], None] = ('0031', '0032_budgets')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
