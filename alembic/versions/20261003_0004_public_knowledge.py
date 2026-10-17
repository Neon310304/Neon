from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261003_0004"
down_revision: str | None = "20261003_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    inspector = sa.inspect(connection)
    if not inspector.has_table("knowledge_documents", schema=schema):
        op.create_table(
            "knowledge_documents",
            sa.Column("id", sa.String(80), primary_key=True),
            sa.Column("source_url", sa.Text(), nullable=False, unique=True),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("document_number", sa.String(80), nullable=False),
            sa.Column("category", sa.String(50), nullable=False),
            sa.Column("publisher", sa.String(200), nullable=False),
            sa.Column("source_type", sa.String(40), nullable=False),
            sa.Column("legal_status", sa.String(40), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_sha256", sa.String(64), nullable=False),
            sa.Column("provenance_json", sa.Text(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
        )
        op.create_index("ix_knowledge_documents_category", "knowledge_documents", ["category"])
    if not inspector.has_table("knowledge_chunks", schema=schema):
        op.create_table(
            "knowledge_chunks",
            sa.Column("id", sa.String(120), primary_key=True),
            sa.Column("document_id", sa.String(80), sa.ForeignKey("knowledge_documents.id"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("pdf_url", sa.Text(), nullable=False),
            sa.Column("pdf_part", sa.Integer(), nullable=False),
            sa.Column("page_number", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("text_sha256", sa.String(64), nullable=False),
        )
        op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_documents_category", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
