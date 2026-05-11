"""add intraday_ticks and eod_prices tables

Split price_history into two purpose-specific tables:
  - intraday_ticks : 15-second live poll snapshots  (source != 'historical')
  - eod_prices     : daily OHLCV rows               (source == 'historical')

Backfill:
  INSERT INTO intraday_ticks SELECT ... FROM price_history WHERE source != 'historical'
  INSERT INTO eod_prices     SELECT ... FROM price_history WHERE source  = 'historical'

price_history is left intact (no DROP) so the data is safe during the transition.
It will be removed in a future cleanup migration once the codebase fully migrates.

Revision ID: a1b2c3d4e5f6
Revises: d207d4a4ab7a
Create Date: 2026-05-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd207d4a4ab7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create intraday_ticks ─────────────────────────────────────────────
    op.create_table(
        'intraday_ticks',
        sa.Column('id',         sa.Integer(),      primary_key=True, autoincrement=True, nullable=False),
        sa.Column('symbol',     sa.String(16),     nullable=False),
        sa.Column('sector',     sa.String(64),     nullable=True),
        sa.Column('ldcp',       sa.Float(),        nullable=True),
        sa.Column('open_price', sa.Float(),        nullable=True),
        sa.Column('high',       sa.Float(),        nullable=True),
        sa.Column('low',        sa.Float(),        nullable=True),
        sa.Column('close',      sa.Float(),        nullable=False),
        sa.Column('volume',     sa.Integer(),      nullable=True),
        sa.Column('change_pct', sa.Float(),        nullable=True),
        sa.Column('source',     sa.String(8),      nullable=False, server_default='live'),
        sa.Column('scraped_at', sa.Integer(),      nullable=False),
    )
    with op.batch_alter_table('intraday_ticks') as batch_op:
        batch_op.create_index('ix_it_symbol',      ['symbol'],              unique=False)
        batch_op.create_index('ix_it_scraped_at',  ['scraped_at'],          unique=False)
        batch_op.create_index('ix_it_symbol_time', ['symbol', 'scraped_at'], unique=False)

    # ── Create eod_prices ─────────────────────────────────────────────────
    op.create_table(
        'eod_prices',
        sa.Column('id',         sa.Integer(),      primary_key=True, autoincrement=True, nullable=False),
        sa.Column('symbol',     sa.String(16),     nullable=False),
        sa.Column('date_key',   sa.Integer(),      nullable=False),   # YYYYMMDD
        sa.Column('sector',     sa.String(64),     nullable=True),
        sa.Column('open_price', sa.Float(),        nullable=True),
        sa.Column('high',       sa.Float(),        nullable=True),
        sa.Column('low',        sa.Float(),        nullable=True),
        sa.Column('close',      sa.Float(),        nullable=False),
        sa.Column('volume',     sa.Integer(),      nullable=True),
        sa.Column('change_pct', sa.Float(),        nullable=True),
        sa.Column('ldcp',       sa.Float(),        nullable=True),
        sa.Column('scraped_at', sa.Integer(),      nullable=False),
        sa.UniqueConstraint('symbol', 'date_key', name='uq_eod_symbol_date'),
    )
    with op.batch_alter_table('eod_prices') as batch_op:
        batch_op.create_index('ix_eod_symbol',      ['symbol'],              unique=False)
        batch_op.create_index('ix_eod_date_key',    ['date_key'],            unique=False)
        batch_op.create_index('ix_eod_symbol_date', ['symbol', 'date_key'],  unique=False)

    # ── Backfill intraday_ticks from price_history ────────────────────────
    # Rows where source is anything other than 'historical' are live scrape ticks.
    op.execute(
        """
        INSERT INTO intraday_ticks
            (symbol, sector, ldcp, open_price, high, low, close, volume,
             change_pct, source, scraped_at)
        SELECT
            symbol, sector, ldcp, open_price, high, low, close, volume,
            change_pct, source, scraped_at
        FROM price_history
        WHERE source != 'historical'
        """
    )

    # ── Backfill eod_prices from price_history ────────────────────────────
    # date_key is derived from scraped_at using strftime in SQLite.
    # IGNORE duplicate (symbol, date_key) pairs — keeps the last written row
    # per day which is the most accurate close for that day.
    op.execute(
        """
        INSERT OR IGNORE INTO eod_prices
            (symbol, date_key, sector, open_price, high, low, close, volume,
             change_pct, ldcp, scraped_at)
        SELECT
            symbol,
            CAST(strftime('%Y%m%d', scraped_at, 'unixepoch') AS INTEGER) AS date_key,
            sector, open_price, high, low, close, volume, change_pct, ldcp, scraped_at
        FROM price_history
        WHERE source = 'historical'
        ORDER BY scraped_at ASC
        """
    )


def downgrade() -> None:
    with op.batch_alter_table('intraday_ticks') as batch_op:
        batch_op.drop_index('ix_it_symbol_time')
        batch_op.drop_index('ix_it_scraped_at')
        batch_op.drop_index('ix_it_symbol')

    op.drop_table('intraday_ticks')

    with op.batch_alter_table('eod_prices') as batch_op:
        batch_op.drop_index('ix_eod_symbol_date')
        batch_op.drop_index('ix_eod_date_key')
        batch_op.drop_index('ix_eod_symbol')

    op.drop_table('eod_prices')
