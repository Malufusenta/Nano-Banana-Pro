"""add_payment_analytics_and_kie_tracking

Revision ID: e2a0865012ee
Revises: f7b4a214edbf
Create Date: 2026-03-08 15:42:15.535354

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a0865012ee'
down_revision: Union[str, Sequence[str], None] = 'f7b4a214edbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS post_id VARCHAR')
    op.execute('ALTER TABLE purchases ADD COLUMN IF NOT EXISTS payment_method VARCHAR')
    op.execute('ALTER TABLE purchases ADD COLUMN IF NOT EXISTS income_amount INTEGER')
    op.execute('ALTER TABLE purchases ADD COLUMN IF NOT EXISTS is_first_purchase BOOLEAN NOT NULL DEFAULT FALSE')
    op.execute('ALTER TABLE banana_transactions ADD COLUMN IF NOT EXISTS kie_credits_cost INTEGER')
    op.execute('ALTER TABLE banana_transactions ADD COLUMN IF NOT EXISTS model_type VARCHAR')
    op.execute('ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS kie_credits_cost INTEGER')
    op.execute('ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS model_type VARCHAR')


def downgrade() -> None:
    op.execute('ALTER TABLE generation_tasks DROP COLUMN IF EXISTS model_type')
    op.execute('ALTER TABLE generation_tasks DROP COLUMN IF EXISTS kie_credits_cost')
    op.execute('ALTER TABLE banana_transactions DROP COLUMN IF EXISTS model_type')
    op.execute('ALTER TABLE banana_transactions DROP COLUMN IF EXISTS kie_credits_cost')
    op.execute('ALTER TABLE purchases DROP COLUMN IF EXISTS is_first_purchase')
    op.execute('ALTER TABLE purchases DROP COLUMN IF EXISTS income_amount')
    op.execute('ALTER TABLE purchases DROP COLUMN IF EXISTS payment_method')
    op.execute('ALTER TABLE generation_tasks DROP COLUMN IF EXISTS post_id')
