"""merge alembic heads and add crypto_pay_invoices

Revision ID: c7e8f9a0b1c2
Revises: 9f1c2e4a7b10, b2c3d4e5f6a7
Create Date: 2026-04-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = ("9f1c2e4a7b10", "b2c3d4e5f6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crypto_pay_invoices",
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("package_key", sa.String(length=16), nullable=False),
        sa.Column("bananas", sa.Integer(), nullable=False),
        sa.Column("credited_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("invoice_id"),
    )


def downgrade() -> None:
    op.drop_table("crypto_pay_invoices")
