"""add latency columns to signal_outcomes

Adds short_latency_sec, medium_latency_sec, long_latency_sec to signal_outcomes.
These track how many seconds after the target horizon timestamp the actual price
tick was found — enabling data-freshness and execution-realism analysis.

Revision ID: cc8b1d659275
Revises: cda4d308bbb0
Create Date: 2026-04-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = 'cc8b1d659275'
down_revision: Union[str, Sequence[str], None] = 'cda4d308bbb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("signal_outcomes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("short_latency_sec",  sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("medium_latency_sec", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("long_latency_sec",   sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("signal_outcomes", schema=None) as batch_op:
        batch_op.drop_column("long_latency_sec")
        batch_op.drop_column("medium_latency_sec")
        batch_op.drop_column("short_latency_sec")
