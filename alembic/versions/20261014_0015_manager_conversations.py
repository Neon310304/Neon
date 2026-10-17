import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20261014_0015"
down_revision = "20261014_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manager_conversations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("auth_subject", sa.String(160), nullable=False),
        sa.Column("binding", sa.String(64), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("reset_generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["auth_subject"], ["users.auth_subject"], ondelete="CASCADE"),
    )
    op.create_index("ix_manager_conversations_expires_at", "manager_conversations", ["expires_at"])
    op.create_table(
        "manager_executions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("conversation_id", sa.String(32), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["manager_conversations.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("manager_executions")
    op.drop_table("manager_conversations")
