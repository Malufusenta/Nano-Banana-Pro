"""drop param_prompt from broadcasts and post_configs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-09

Main prompt (hidden_prompt / prompt) already contains {value}; param_prompt column removed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("post_configs", "param_prompt")
    op.drop_column("broadcasts", "param_prompt")


def downgrade() -> None:
    op.add_column("broadcasts", sa.Column("param_prompt", sa.Text(), nullable=True))
    op.add_column("post_configs", sa.Column("param_prompt", sa.Text(), nullable=True))
