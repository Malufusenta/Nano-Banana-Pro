"""add system_status heartbeat table

Revision ID: c8e9f0a1b2c3
Revises: 3d2a0b7c91fe
Create Date: 2026-05-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "3d2a0b7c91fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text("INSERT INTO system_status (id, last_heartbeat) VALUES (1, NOW())")
    )


def downgrade() -> None:
    op.drop_table("system_status")
