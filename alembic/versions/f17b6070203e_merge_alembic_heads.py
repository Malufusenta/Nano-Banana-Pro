"""merge alembic heads

Revision ID: f17b6070203e
Revises: 75caa9693942, 78c1d5c3f574
Create Date: 2026-05-14 11:59:36.233975

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f17b6070203e'
down_revision: Union[str, Sequence[str], None] = ('75caa9693942', '78c1d5c3f574')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
