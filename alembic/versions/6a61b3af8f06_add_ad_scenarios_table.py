"""add_ad_scenarios_table

Revision ID: 6a61b3af8f06
Revises: d6570713a0c1
Create Date: 2026-02-06 14:09:58.397106

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a61b3af8f06'
down_revision: Union[str, Sequence[str], None] = 'd6570713a0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Создаём таблицу ad_scenarios
    op.create_table(
        'ad_scenarios',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scenario_key', sa.String(50), nullable=False),
        sa.Column('welcome_text', sa.Text(), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('model_type', sa.String(20), server_default='standard', nullable=False),
        sa.Column('aspect_ratio', sa.String(10), server_default='1:1', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('total_starts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_purchases', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scenario_key')
    )
    
    # Добавляем поле active_scenario_id в таблицу users
    op.add_column('users', sa.Column('active_scenario_id', sa.Integer(), nullable=True))

def downgrade():
    op.drop_column('users', 'active_scenario_id')
    op.drop_table('ad_scenarios')