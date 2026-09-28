"""Baseline: tasks, submissions, content_hashes and an append-only audit_log.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APPEND_ONLY = "audit_log is append-only"

SQLITE_TRIGGERS = (
    "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
    f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END",
    "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
    f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END",
)

POSTGRES_TRIGGERS = (
    "CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS "
    f"$$ BEGIN RAISE EXCEPTION '{APPEND_ONLY}'; END; $$",
    "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
    "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()",
    "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()",
)


def upgrade() -> None:
    """Create the baseline schema."""
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("id_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.UniqueConstraint("slug", name="uq_tasks_slug"),
        sa.UniqueConstraint("id_key", name="uq_tasks_id_key"),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("submitted_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_submissions_task_id_tasks"),
        sa.PrimaryKeyConstraint("id", name="pk_submissions"),
    )
    op.create_index("ix_submissions_task_id", "submissions", ["task_id"])
    op.create_table(
        "content_hashes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(71), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name="fk_content_hashes_submission_id_submissions",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_content_hashes_task_id_tasks"),
        sa.PrimaryKeyConstraint("id", name="pk_content_hashes"),
        sa.UniqueConstraint("hash", name="uq_content_hashes_hash"),
    )
    op.create_index("ix_content_hashes_task_id", "content_hashes", ["task_id"])
    op.create_table(
        "audit_log",
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("at", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("task_slug", sa.String(64), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("seq", name="pk_audit_log"),
        sa.UniqueConstraint("prev_hash", name="uq_audit_log_prev_hash"),
        sa.UniqueConstraint("row_hash", name="uq_audit_log_row_hash"),
    )
    op.create_index("ix_audit_log_task_slug", "audit_log", ["task_slug"])
    dialect = op.get_bind().dialect.name
    triggers = {"sqlite": SQLITE_TRIGGERS, "postgresql": POSTGRES_TRIGGERS}.get(dialect, ())
    for statement in triggers:
        op.execute(statement)


def downgrade() -> None:
    """Drop everything the baseline created."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TABLE audit_log")
        op.execute("DROP FUNCTION audit_log_append_only()")
    else:
        op.drop_table("audit_log")
    op.drop_table("content_hashes")
    op.drop_table("submissions")
    op.drop_table("tasks")
