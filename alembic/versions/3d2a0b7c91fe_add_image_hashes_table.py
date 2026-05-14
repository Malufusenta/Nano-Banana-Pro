"""add image_hashes table

Revision ID: 3d2a0b7c91fe
Revises: f17b6070203e
Create Date: 2026-05-14 17:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3d2a0b7c91fe"
down_revision: Union[str, Sequence[str], None] = "f17b6070203e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "image_hashes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hash", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_image_hashes_hash", "image_hashes", ["hash"], unique=False)
    op.create_index("ix_image_hashes_created_at", "image_hashes", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_image_hashes_created_at", table_name="image_hashes")
    op.drop_index("ix_image_hashes_hash", table_name="image_hashes")
    op.drop_table("image_hashes")
