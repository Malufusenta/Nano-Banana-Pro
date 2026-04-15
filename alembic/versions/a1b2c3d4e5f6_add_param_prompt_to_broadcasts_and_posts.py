"""add param_question and param_prompt to broadcasts and post_configs

Revision ID: a1b2c3d4e5f6
Revises: 6b85c16c8d67
Create Date: 2026-04-09

Optional parameterized prompt: ask user for {value} before generation.
(post_configs = referral/post deep links in this codebase)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "6b85c16c8d67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("broadcasts", sa.Column("param_question", sa.Text(), nullable=True))
    op.add_column("broadcasts", sa.Column("param_prompt", sa.Text(), nullable=True))
    op.add_column("post_configs", sa.Column("param_question", sa.Text(), nullable=True))
    op.add_column("post_configs", sa.Column("param_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("post_configs", "param_prompt")
    op.drop_column("post_configs", "param_question")
    op.drop_column("broadcasts", "param_prompt")
    op.drop_column("broadcasts", "param_question")
