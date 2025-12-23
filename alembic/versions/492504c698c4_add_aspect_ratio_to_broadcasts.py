"""add_aspect_ratio_to_broadcasts

Revision ID: 492504c698c4
Revises: 1e17874b9ce8
Create Date: 2025-12-22 22:33:48.027733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '492504c698c4'
down_revision: Union[str, Sequence[str], None] = '1e17874b9ce8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Добавляем колонку aspect_ratio с дефолтом '1:1'
    op.add_column('broadcasts', sa.Column('aspect_ratio', sa.String(10), server_default='1:1'))


def downgrade():
    # Откатываем изменения
    op.drop_column('broadcasts', 'aspect_ratio')
