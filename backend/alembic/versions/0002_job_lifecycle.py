"""Add idempotency, dispatch and retry fields to jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(255)))
        batch_op.add_column(sa.Column("celery_task_id", sa.String(255)))
        batch_op.add_column(
            sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(sa.Column("retry_of_job_id", sa.String(36)))
        batch_op.add_column(
            sa.Column("attempt", sa.Integer(), server_default="1", nullable=False)
        )
        batch_op.create_foreign_key(
            "fk_jobs_retry_of_job_id_jobs", "jobs", ["retry_of_job_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_unique_constraint(
            "uq_jobs_user_id_idempotency_key", ["user_id", "idempotency_key"]
        )
        batch_op.create_index("ix_jobs_celery_task_id", ["celery_task_id"])
        batch_op.create_index("ix_jobs_retry_of_job_id", ["retry_of_job_id"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_retry_of_job_id")
        batch_op.drop_index("ix_jobs_celery_task_id")
        batch_op.drop_constraint("uq_jobs_user_id_idempotency_key", type_="unique")
        batch_op.drop_constraint("fk_jobs_retry_of_job_id_jobs", type_="foreignkey")
        batch_op.drop_column("attempt")
        batch_op.drop_column("retry_of_job_id")
        batch_op.drop_column("cancel_requested")
        batch_op.drop_column("celery_task_id")
        batch_op.drop_column("idempotency_key")

