"""create immutable NESO payloads and normalized carbon observations"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_neso_evidence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("source_api", sa.String(120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("validation_fresh", sa.Boolean(), nullable=False),
        sa.Column("validation_error", sa.Text()),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("region_id > 0", name="ck_raw_payload_region_positive"),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_raw_payload_hash_length"),
        sa.CheckConstraint("schema_version > 0", name="ck_raw_payload_schema_version"),
        sa.UniqueConstraint("region_id", "source_api", "payload_sha256", "retrieved_at", name="uq_raw_payload_identity"),
    )
    op.create_index("ix_raw_payloads_region_id", "raw_payloads", ["region_id"])
    op.create_index("ix_raw_payloads_retrieved_at", "raw_payloads", ["retrieved_at"])
    op.create_table(
        "carbon_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_payload_id", sa.Integer(), sa.ForeignKey("raw_payloads.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("region_name", sa.String(160), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("measurement_type", sa.String(16), nullable=False),
        sa.Column("intensity_gco2_per_kwh", sa.Float(), nullable=False),
        sa.Column("intensity_index", sa.String(32), nullable=False),
        sa.Column("interval_status", sa.String(16), nullable=False),
        sa.Column("interval_lag_seconds", sa.Float(), nullable=False),
        sa.Column("is_fresh", sa.Boolean(), nullable=False),
        sa.Column("age_seconds", sa.Float(), nullable=False),
        sa.Column("source_api", sa.String(120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("region_id > 0", name="ck_carbon_observation_region_positive"),
        sa.CheckConstraint("intensity_gco2_per_kwh >= 0 AND intensity_gco2_per_kwh < 'Infinity'::float8", name="ck_carbon_observation_intensity_nonnegative_finite"),
        sa.CheckConstraint("valid_until > observed_at", name="ck_carbon_observation_interval_order"),
        sa.CheckConstraint("measurement_type IN ('actual', 'forecast')", name="ck_carbon_observation_measurement_type"),
        sa.CheckConstraint("interval_status IN ('upcoming', 'in_progress', 'completed')", name="ck_carbon_observation_interval_status"),
        sa.CheckConstraint("interval_lag_seconds >= 0", name="ck_carbon_observation_lag_nonnegative"),
        sa.CheckConstraint("interval_lag_seconds < 'Infinity'::float8", name="ck_carbon_observation_lag_finite"),
        sa.CheckConstraint("age_seconds >= 0 AND age_seconds < 'Infinity'::float8", name="ck_carbon_observation_age_nonnegative_finite"),
        sa.UniqueConstraint("raw_payload_id", "region_id", "observed_at", "valid_until", "measurement_type", name="uq_carbon_observation_identity"),
    )
    op.create_index("ix_carbon_observations_raw_payload_id", "carbon_observations", ["raw_payload_id"])
    op.create_index("ix_carbon_observations_region_id", "carbon_observations", ["region_id"])
    op.create_index("ix_carbon_observations_observed_at", "carbon_observations", ["observed_at"])


def downgrade() -> None:
    op.drop_table("carbon_observations")
    op.drop_table("raw_payloads")
