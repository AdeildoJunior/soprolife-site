"""m15.7 — responsáveis legais e contato separado no follow-up

Revision ID: d91e7a2b4c68
Revises: c7d3e91a4f52
Create Date: 2026-07-21 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d91e7a2b4c68"
down_revision = "c7d3e91a4f52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_relationships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("minor_person_id", sa.String(length=36), nullable=False),
        sa.Column("guardian_person_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=30), nullable=False),
        sa.Column("is_legal_guardian", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "minor_person_id <> guardian_person_id",
            name=op.f("ck_person_relationships_pessoas_relacionadas_distintas"),
        ),
        sa.ForeignKeyConstraint(
            ["guardian_person_id"],
            ["people.id"],
            name=op.f(
                "fk_person_relationships_guardian_person_id_people"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["minor_person_id"],
            ["people.id"],
            name=op.f("fk_person_relationships_minor_person_id_people"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person_relationships")),
        sa.UniqueConstraint(
            "minor_person_id",
            "guardian_person_id",
            "relationship_type",
            name="uq_person_relationship_natural_key",
        ),
    )
    with op.batch_alter_table("person_relationships") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_person_relationships_minor_person_id"),
            ["minor_person_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_person_relationships_guardian_person_id"),
            ["guardian_person_id"],
            unique=False,
        )

    with op.batch_alter_table("followups") as batch_op:
        batch_op.add_column(
            sa.Column("contact_person_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_followups_contact_person_id_people"),
            "people",
            ["contact_person_id"],
            ["id"],
        )
        batch_op.create_index(
            batch_op.f("ix_followups_contact_person_id"),
            ["contact_person_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("followups") as batch_op:
        batch_op.drop_index(batch_op.f("ix_followups_contact_person_id"))
        batch_op.drop_constraint(
            batch_op.f("fk_followups_contact_person_id_people"),
            type_="foreignkey",
        )
        batch_op.drop_column("contact_person_id")

    with op.batch_alter_table("person_relationships") as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_person_relationships_guardian_person_id")
        )
        batch_op.drop_index(
            batch_op.f("ix_person_relationships_minor_person_id")
        )
    op.drop_table("person_relationships")
