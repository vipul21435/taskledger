"""Near-duplicate signatures and LSH band buckets.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create task_signatures and lsh_buckets."""
    op.create_table(
        "task_signatures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("solution", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name="fk_task_signatures_submission_id_submissions",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_signatures_task_id_tasks"),
        sa.PrimaryKeyConstraint("id", name="pk_task_signatures"),
        sa.UniqueConstraint("submission_id", name="uq_task_signatures_submission_id"),
    )
    op.create_index("ix_task_signatures_task_id", "task_signatures", ["task_id"])
    op.create_table(
        "lsh_buckets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bucket", sa.String(48), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_lsh_buckets_task_id_tasks"),
        sa.PrimaryKeyConstraint("id", name="pk_lsh_buckets"),
    )
    op.create_index("ix_lsh_buckets_bucket", "lsh_buckets", ["bucket"])
    op.create_index("ix_lsh_buckets_task_id", "lsh_buckets", ["task_id"])


def downgrade() -> None:
    """Drop the near-duplicate tables."""
    op.drop_table("lsh_buckets")
    op.drop_table("task_signatures")
