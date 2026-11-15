"""policy viewer: title and Markdown content of each policy document

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-15 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('policy_documents', sa.Column('title', sa.String(length=200), nullable=True))
    op.add_column('policy_documents', sa.Column('content', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('policy_documents', 'content')
    op.drop_column('policy_documents', 'title')
