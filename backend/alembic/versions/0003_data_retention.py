"""Track uploaded inputs and retention deadlines."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("deletion_requested_at", sa.DateTime(timezone=True)))
    op.create_table(
        "input_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("original_storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column("cleaned_storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletion_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(120)),
    )
    op.create_index("ix_input_artifacts_user_id", "input_artifacts", ["user_id"])
    op.create_index(
        "ix_input_artifacts_cleaned_storage_key", "input_artifacts", ["cleaned_storage_key"]
    )
    op.create_index("ix_input_artifacts_expires_at", "input_artifacts", ["expires_at"])
    with op.batch_alter_table("job_outputs") as batch_op:
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True)))
        batch_op.create_index("ix_job_outputs_expires_at", ["expires_at"])


def downgrade() -> None:
    with op.batch_alter_table("job_outputs") as batch_op:
        batch_op.drop_index("ix_job_outputs_expires_at")
        batch_op.drop_column("expires_at")
    op.drop_table("input_artifacts")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("deletion_requested_at")
