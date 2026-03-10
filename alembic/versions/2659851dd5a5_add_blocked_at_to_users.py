"""add_blocked_at_to_users

Revision ID: 2659851dd5a5
Revises: e30778c9c8b3
Create Date: 2026-03-10 10:23:39.622568

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2659851dd5a5'
down_revision: Union[str, Sequence[str], None] = 'e30778c9c8b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('blocked_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'blocked_at')
