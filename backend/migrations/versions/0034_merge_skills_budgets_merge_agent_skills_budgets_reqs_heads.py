"""merge agent_skills + budgets/reqs heads

Revision ID: 0034_merge_skills_budgets
Revises: 0033_merge_budgets_reqs, 0032_agent_skills
Create Date: 2026-07-09 22:56:33.942467

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0034_merge_skills_budgets'
down_revision: Union[str, Sequence[str], None] = ('0033_merge_budgets_reqs', '0032_agent_skills')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
