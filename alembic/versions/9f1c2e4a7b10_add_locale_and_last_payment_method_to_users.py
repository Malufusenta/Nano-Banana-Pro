"""add_locale_and_last_payment_method_to_users

Revision ID: 9f1c2e4a7b10
Revises: 79142c44ab8e
Create Date: 2026-04-30 12:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f1c2e4a7b10"
down_revision: Union[str, Sequence[str], None] = "79142c44ab8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("locale", sa.String(length=8), server_default="en", nullable=False))
    op.add_column("users", sa.Column("last_payment_method", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "last_payment_method")
    op.drop_column("users", "locale")
