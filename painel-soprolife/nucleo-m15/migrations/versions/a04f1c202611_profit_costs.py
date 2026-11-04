"""Analytic cost rule versions and operational sessions; no historical backfill.

Revision ID: a04f1c202611
Revises: d7b2f3a9c1e0
"""
from alembic import op
import sqlalchemy as sa
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

revision = "a04f1c202611"
down_revision = "d7b2f3a9c1e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profit_cost_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.CheckConstraint("value >= 0", name="profit_cost_rule_nonnegative"),
    )
    op.create_index("ix_profit_cost_rules_key_effective_from", "profit_cost_rules", ["key", "effective_from"])
    op.create_table(
        "profit_revenue_basis",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("spirometry_exam_id", sa.String(36), sa.ForeignKey("spirometry_exams.id"), nullable=False),
        sa.Column("financial_entry_id", sa.String(36), sa.ForeignKey("financial_entries.id"), nullable=False),
        sa.Column("entry_amount_snapshot", sa.Numeric(12, 2), nullable=False),
        sa.Column("basis", sa.String(10), nullable=False),
        sa.Column("gross_amount", sa.Numeric(12, 2)),
        sa.Column("evidence_note", sa.String(300), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("basis IN ('bruto','liquido')", name="profit_revenue_basis_kind"),
        sa.CheckConstraint("gross_amount IS NULL OR gross_amount > 0", name="profit_revenue_basis_gross_positive"),
        sa.CheckConstraint("entry_amount_snapshot > 0", name="profit_revenue_basis_entry_positive"),
    )
    op.create_index("ix_profit_revenue_basis_spirometry_exam_id", "profit_revenue_basis", ["spirometry_exam_id"])
    op.create_table(
        "operational_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("place", sa.String(30), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.String(5)),
        sa.Column("end_time", sa.String(5)),
        sa.Column("billable_hours", sa.Numeric(6, 2)),
        sa.Column("parking_override", sa.Numeric(12, 2)),
        sa.Column("fuel_override", sa.Numeric(12, 2)),
        sa.Column("room_override", sa.Numeric(12, 2)),
        sa.Column("other_shared", sa.Numeric(12, 2)),
        sa.Column("note", sa.String(500)),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("billable_hours IS NULL OR billable_hours > 0", name="session_hours_positive"),
        sa.CheckConstraint("parking_override IS NULL OR parking_override >= 0", name="session_parking_nonnegative"),
        sa.CheckConstraint("fuel_override IS NULL OR fuel_override >= 0", name="session_fuel_nonnegative"),
        sa.CheckConstraint("room_override IS NULL OR room_override >= 0", name="session_room_nonnegative"),
        sa.CheckConstraint("other_shared IS NULL OR other_shared >= 0", name="session_other_nonnegative"),
    )
    op.create_index("ix_operational_sessions_service_date", "operational_sessions", ["service_date"])
    op.create_table(
        "operational_session_exams",
        sa.Column("session_id", sa.String(36), sa.ForeignKey("operational_sessions.id"), primary_key=True),
        sa.Column("spirometry_exam_id", sa.String(36), sa.ForeignKey("spirometry_exams.id"), primary_key=True, unique=True),
    )
    initial = {
        "material": "10.00", "card_percent": "5.00", "tax_percent": "6.00", "technician": "0.00",
        "downtown.room_hour": "60.00", "downtown.parking": "25.00", "downtown.fuel": "10.00",
        "nova_america.room_hour": "40.00", "nova_america.parking": "25.00", "nova_america.fuel": "10.00",
        "pastore.parking": "6.00", "pastore.fuel": "30.00",
        "maclin.partner_percent": "30.00", "maclin.parking": "10.00", "maclin.fuel": "0.00",
        "alergolife.partner_percent": "30.00", "alergolife.parking": "10.00", "alergolife.fuel": "10.00",
        "home.parking": "0.00", "home.fuel": "10.00",
    }
    op.bulk_insert(sa.table("profit_cost_rules",
        sa.column("id", sa.String), sa.column("key", sa.String),
        sa.column("effective_from", sa.Date), sa.column("value", sa.Numeric),
        sa.column("created_by_user_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("reason", sa.String)),
        [{"id": str(uuid4()), "key": key, "effective_from": date(2026, 7, 1) if key.startswith("pastore.") else date(2026, 11, 4),
          "value": Decimal(value), "created_by_user_id": None,
          "created_at": datetime.now(timezone.utc), "reason": "Configuração inicial aprovada em 2026-11-04"}
         for key, value in initial.items()])


def downgrade():
    op.drop_table("operational_session_exams")
    op.drop_index("ix_operational_sessions_service_date", table_name="operational_sessions")
    op.drop_table("operational_sessions")
    op.drop_index("ix_profit_revenue_basis_spirometry_exam_id", table_name="profit_revenue_basis")
    op.drop_table("profit_revenue_basis")
    op.drop_index("ix_profit_cost_rules_key_effective_from", table_name="profit_cost_rules")
    op.drop_table("profit_cost_rules")
