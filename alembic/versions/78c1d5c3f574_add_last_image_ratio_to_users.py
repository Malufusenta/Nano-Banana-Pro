"""add last_image_ratio to users

Revision ID: 78c1d5c3f574
Revises: 9aeae61d7d45
Create Date: 2026-05-12 11:06:29.129881

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78c1d5c3f574'
down_revision: Union[str, Sequence[str], None] = '9aeae61d7d45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # This revision is a no-op compatibility branch kept only because it is
    # referenced by the existing merge revision f17b6070203e.
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
