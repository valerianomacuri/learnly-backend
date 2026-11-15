"""refund request_hash for idempotent creation

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15 08:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('refund_requests', sa.Column('request_hash', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('refund_requests', 'request_hash')
