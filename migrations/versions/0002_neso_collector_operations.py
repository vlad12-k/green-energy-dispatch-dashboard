"""add durable NESO collector run and attempt status"""
import sqlalchemy as sa
from alembic import op

revision = "0002_neso_collector_operations"
down_revision = "0001_neso_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "neso_collector_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), server_default=sa.text("'running'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_category", sa.String(24)),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("payload_sha256", sa.String(64)),
        sa.Column("source_retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("interval_start", sa.DateTime(timezone=True)),
        sa.Column("interval_end", sa.DateTime(timezone=True)),
        sa.Column("measurement_type", sa.String(16)),
        sa.Column("intensity_gco2_per_kwh", sa.Float()),
        sa.Column("source_fresh", sa.Boolean()),
        sa.Column("raw_evidence_inserted", sa.Boolean()),
        sa.Column("observation_inserted", sa.Boolean()),
        sa.Column("duplicate_interval", sa.Boolean()),
        sa.Column("missing_interval_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("gap_start", sa.DateTime(timezone=True)),
        sa.Column("gap_end", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("region_id > 0", name="ck_neso_collector_run_region_positive"),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_neso_collector_run_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_neso_collector_run_attempt_count"),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_neso_collector_run_failure_count"),
        sa.CheckConstraint("missing_interval_count >= 0", name="ck_neso_collector_run_gap_count"),
        sa.CheckConstraint("measurement_type IS NULL OR measurement_type IN ('actual', 'forecast')", name="ck_neso_collector_run_measurement_type"),
        sa.CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="ck_neso_collector_run_duration"),
    )
    op.create_index("ix_neso_collector_runs_region_id", "neso_collector_runs", ["region_id"])
    op.create_index("ix_neso_collector_runs_started_at", "neso_collector_runs", ["started_at"])

    op.create_table(
        "neso_collector_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("neso_collector_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(24), server_default=sa.text("'in_progress'"), nullable=False),
        sa.Column("error_category", sa.String(24)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("retry_after", sa.String(128)),
        sa.Column("retry_delay_seconds", sa.Float()),
        sa.Column("payload_sha256", sa.String(64)),
        sa.Column("source_retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("interval_start", sa.DateTime(timezone=True)),
        sa.Column("interval_end", sa.DateTime(timezone=True)),
        sa.Column("measurement_type", sa.String(16)),
        sa.Column("source_fresh", sa.Boolean()),
        sa.Column("raw_evidence_inserted", sa.Boolean()),
        sa.Column("observation_inserted", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_neso_collector_attempt_number"),
        sa.CheckConstraint(
            "outcome IN ('in_progress', 'success', 'duplicate', 'source_failure', 'validation_failure', 'persistence_failure', 'internal_failure')",
            name="ck_neso_collector_attempt_outcome",
        ),
        sa.CheckConstraint("http_status IS NULL OR http_status BETWEEN 100 AND 599", name="ck_neso_collector_attempt_http_status"),
        sa.CheckConstraint("retry_delay_seconds IS NULL OR retry_delay_seconds >= 0", name="ck_neso_collector_attempt_retry_delay"),
        sa.CheckConstraint("measurement_type IS NULL OR measurement_type IN ('actual', 'forecast')", name="ck_neso_collector_attempt_measurement_type"),
        sa.UniqueConstraint("run_id", "attempt_number", name="uq_neso_collector_attempt_number"),
    )
    op.create_index("ix_neso_collector_attempts_run_id", "neso_collector_attempts", ["run_id"])
    op.create_index("ix_neso_collector_attempts_started_at", "neso_collector_attempts", ["started_at"])


def downgrade() -> None:
    op.drop_table("neso_collector_attempts")
    op.drop_table("neso_collector_runs")
