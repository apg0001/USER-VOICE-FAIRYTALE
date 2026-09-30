"""Create the initial platform schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        *timestamp_columns(),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("external_id"),
    )
    op.create_index("ix_users_external_id", "users", ["external_id"])
    op.create_table(
        "models",
        *timestamp_columns(),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("model_type", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_models_key", "models", ["key"])
    op.create_index("ix_models_model_type", "models", ["model_type"])
    op.create_table(
        "voice_profiles",
        *timestamp_columns(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("consent_version", sa.String(30), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profile_metadata", sa.JSON(), nullable=False),
    )
    op.create_index("ix_voice_profiles_user_id", "voice_profiles", ["user_id"])
    op.create_table(
        "voice_samples",
        *timestamp_columns(),
        sa.Column("voice_profile_id", sa.String(36), sa.ForeignKey("voice_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_storage_key", sa.String(512), nullable=False),
        sa.Column("cleaned_storage_key", sa.String(512)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("sample_rate", sa.Integer()),
        sa.Column("quality_metadata", sa.JSON(), nullable=False),
    )
    op.create_index("ix_voice_samples_voice_profile_id", "voice_samples", ["voice_profile_id"])
    op.create_table(
        "jobs",
        *timestamp_columns(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("voice_profile_id", sa.String(36), sa.ForeignKey("voice_profiles.id", ondelete="SET NULL")),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("estimated_remaining_seconds", sa.Integer()),
        sa.Column("input_storage_key", sa.String(512)),
        sa.Column("input_text", sa.Text()),
        sa.Column("model_key", sa.String(120), nullable=False),
        sa.Column("request_config", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for column in ("user_id", "voice_profile_id", "mode", "status"):
        op.create_index(f"ix_jobs_{column}", "jobs", [column])
    op.create_table(
        "job_outputs",
        *timestamp_columns(),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("output_metadata", sa.JSON(), nullable=False),
    )
    op.create_index("ix_job_outputs_job_id", "job_outputs", ["job_id"])


def downgrade() -> None:
    op.drop_table("job_outputs")
    op.drop_table("jobs")
    op.drop_table("voice_samples")
    op.drop_table("voice_profiles")
    op.drop_table("models")
    op.drop_table("users")

