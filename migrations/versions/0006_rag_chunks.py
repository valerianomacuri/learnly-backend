"""RAG: policy_chunks (pgvector + tsvector) and embedding metadata on documents

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15 15:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.add_column(
        'policy_documents', sa.Column('embedding_model', sa.String(length=100), nullable=True)
    )
    op.add_column('policy_documents', sa.Column('embedding_dim', sa.Integer(), nullable=True))
    op.create_table(
        'policy_chunks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('document_id', sa.Uuid(), nullable=False),
        sa.Column('parent_section_id', sa.Uuid(), nullable=True),
        sa.Column('section_path', sa.Text(), nullable=False),
        sa.Column('heading_level', sa.Integer(), nullable=False),
        sa.Column('chunk_type', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedded_text', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(get_settings().embedding_dim), nullable=False),
        sa.Column(
            'tsv',
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('spanish', content)", persisted=True),
            nullable=False,
        ),
        sa.Column('tags', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('token_count', sa.Integer(), nullable=False),
        sa.Column('source_lines', sa.String(length=32), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['document_id'], ['policy_documents.id'],
            name=op.f('fk_policy_chunks_document_id_policy_documents'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['parent_section_id'], ['policy_chunks.id'],
            name=op.f('fk_policy_chunks_parent_section_id_policy_chunks'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_policy_chunks')),
    )
    op.create_index(
        'ix_policy_chunks_embedding', 'policy_chunks', ['embedding'],
        postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'},
    )
    op.create_index('ix_policy_chunks_tsv', 'policy_chunks', ['tsv'], postgresql_using='gin')
    op.create_index('ix_policy_chunks_document_id', 'policy_chunks', ['document_id'])


def downgrade() -> None:
    op.drop_table('policy_chunks')
    op.drop_column('policy_documents', 'embedding_dim')
    op.drop_column('policy_documents', 'embedding_model')
