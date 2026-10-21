"""M26.34 — um documento fiscal pode ter vários exames e um tomador próprio.

O CASO QUE PEDIU ISSO

Dois exames domiciliares de um casal, no mesmo dia, no mesmo município,
quitados por UM Pix de R$ 500,00 pela filha. A nota é uma só, de R$ 500,00, no
CPF dela — que não é paciente de nenhum dos dois exames. O fluxo anterior não
tinha como representar isso: `fiscal_documents.spirometry_exam_id` era NOT NULL
com `uq_fiscal_exam_environment`, e o tomador saía sempre do paciente do exame.
Emitir pelo caminho antigo produziria duas notas de R$ 250,00, cada uma no nome
de um paciente — não a nota que de fato corresponde ao serviço vendido.

O QUE MUDA, E O QUE NÃO MUDA

O fluxo `single` (1 exame → 1 NFS-e) permanece byte-a-byte o mesmo: mesma
coluna, mesmo índice único, mesmo tomador derivado do paciente, mesmo
`amount_snapshot` vindo da receita do exame. Todo documento existente já é
`single` e continua sendo — `kind` entra com default 'single' e server_default
'single', de modo que nenhuma linha precisa ser reescrita para ficar válida.

QUATRO PARTES

1. `fiscal_documents`: `spirometry_exam_id` passa a aceitar NULL, e entram
   `kind` e `recipient_person_id`. Dois CHECKs novos tornam as duas formas
   mutuamente exclusivas (ver o docstring do modelo).

2. `fiscal_document_items`: tabela nova. UM item por exame, com o seu valor.

3. BACKFILL de ocupação — a parte que merece atenção. Com
   `spirometry_exam_id` nulável, `uq_fiscal_exam_environment` deixa de ver os
   exames agrupados, e a pergunta "este exame já foi faturado?" perderia a
   garantia de banco. A trava passa para `uq_fiscal_item_exam_environment`, e
   para que ela valha também sobre o histórico, cria-se um item para cada
   documento já existente. Esse item grava `amount` NULL e
   `financial_entry_id` NULL de propósito: a verdade monetária daqueles
   documentos vive na preparação imutável, e recopiá-la para cá criaria um
   segundo lugar onde ela poderia divergir. O item histórico é registro de
   ocupação, nada mais.

4. `fiscal_preparations`: o CHECK `fiscal_snapshot_source` ganha a terceira
   fonte de valor, `grouped_items.sum`; e `fiscal_issuance_requests`
   .spirometry_exam_id passa a aceitar NULL, porque um pedido de documento
   agrupado não tem "o" exame.

SQLite não altera CHECK nem nullability, então as duas tabelas são
reconstruídas em batch, no mesmo padrão da M57 (fiscal_documents) e da M70
(fiscal_preparations) — incluindo recriar explicitamente os gatilhos
`fiscal_documents_no_delete`/`fiscal_documents_identity` e
`fiscal_preparations_no_update`/`no_delete`, que o rebuild derruba e o Alembic
não repõe. Perder esses gatilhos em silêncio seria pior que o defeito.

Revision ID: b5e2f7a91c38
Revises: a1c7e5d9b204
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b5e2f7a91c38"
down_revision = "a1c7e5d9b204"
branch_labels = None
depends_on = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

STATES = ("blocked", "pending", "issuing", "issued", "simulated", "failed",
          "uncertain", "reconciling", "cancelled")
KIND_CHECK = "kind IN ('single','grouped')"
SHAPE_CHECK = (
    "(kind = 'single' AND spirometry_exam_id IS NOT NULL "
    "AND recipient_person_id IS NULL) OR "
    "(kind = 'grouped' AND spirometry_exam_id IS NULL "
    "AND recipient_person_id IS NOT NULL)"
)
FK_RECIPIENT = "fk_fiscal_documents_recipient_person_id_people"

DOCUMENT_TRIGGERS = (
    "CREATE TRIGGER fiscal_documents_no_delete BEFORE DELETE ON fiscal_documents "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
    # Mesma identidade protegida de antes. `recipient_person_id` fica FORA de
    # propósito: trocar o tomador antes de qualquer envio é uma decisão
    # humana legítima, que o fingerprint já pune deixando o preparo stale.
    """CREATE TRIGGER fiscal_documents_identity BEFORE UPDATE ON fiscal_documents
            WHEN NEW.spirometry_exam_id IS NOT OLD.spirometry_exam_id
              OR NEW.environment IS NOT OLD.environment OR NEW.created_by IS NOT OLD.created_by
              OR NEW.kind IS NOT OLD.kind
              OR NEW.idempotency_key IS NOT OLD.idempotency_key
              OR NEW.idempotency_fingerprint IS NOT OLD.idempotency_fingerprint
            BEGIN SELECT RAISE(ABORT, 'immutable fiscal identity'); END""",
)
# O gatilho de identidade ANTERIOR, recriado tal e qual no downgrade.
DOCUMENT_TRIGGERS_BEFORE = (
    "CREATE TRIGGER fiscal_documents_no_delete BEFORE DELETE ON fiscal_documents "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
    """CREATE TRIGGER fiscal_documents_identity BEFORE UPDATE ON fiscal_documents
            WHEN NEW.spirometry_exam_id IS NOT OLD.spirometry_exam_id
              OR NEW.environment IS NOT OLD.environment OR NEW.created_by IS NOT OLD.created_by
              OR NEW.idempotency_key IS NOT OLD.idempotency_key
              OR NEW.idempotency_fingerprint IS NOT OLD.idempotency_fingerprint
            BEGIN SELECT RAISE(ABORT, 'immutable fiscal identity'); END""",
)
PREPARATION_TRIGGERS = (
    "CREATE TRIGGER fiscal_preparations_no_update BEFORE UPDATE ON fiscal_preparations "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
    "CREATE TRIGGER fiscal_preparations_no_delete BEFORE DELETE ON fiscal_preparations "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
)

PREP_CHECK_BEFORE = (
    "amount_snapshot IS NULL OR (policy_id IS NOT NULL AND amount_snapshot > 0 AND ("
    "(financial_entry_id IS NOT NULL AND partnership_id IS NULL) OR "
    "(financial_entry_id IS NULL AND partnership_id IS NOT NULL AND flow = 'PASTORE' "
    "AND amount_source = 'partnership.valor_recebido_por_exame')))"
)
PREP_CHECK_AFTER = (
    "amount_snapshot IS NULL OR (policy_id IS NOT NULL AND amount_snapshot > 0 AND ("
    "(financial_entry_id IS NOT NULL AND partnership_id IS NULL) OR "
    "(financial_entry_id IS NULL AND partnership_id IS NOT NULL AND flow = 'PASTORE' "
    "AND amount_source = 'partnership.valor_recebido_por_exame') OR "
    "(financial_entry_id IS NULL AND partnership_id IS NULL "
    "AND amount_source = 'grouped_items.sum')))"
)


# No PostgreSQL a identidade imutável é uma FUNÇÃO de gatilho, criada por
# f6a1d9e28b40. Ela é reescrita aqui para incluir `kind` — um documento não
# muda de natureza —, mantendo a paridade com o gatilho SQLite acima. Note
# que `recipient_person_id` fica FORA das duas, deliberadamente: trocar o
# tomador antes de qualquer envio é decisão humana legítima, e o fingerprint
# já a pune deixando a preparação stale.
PG_IDENTITY_AFTER = """
CREATE OR REPLACE FUNCTION fiscal_protect_identity() RETURNS trigger AS $$
            BEGIN
            IF (NEW.spirometry_exam_id, NEW.environment, NEW.created_by, NEW.kind,
                NEW.idempotency_key, NEW.idempotency_fingerprint) IS DISTINCT FROM
               (OLD.spirometry_exam_id, OLD.environment, OLD.created_by, OLD.kind,
                OLD.idempotency_key, OLD.idempotency_fingerprint) THEN
                RAISE EXCEPTION 'immutable fiscal identity';
            END IF;
            RETURN NEW;
            END;
$$ LANGUAGE plpgsql
"""
PG_IDENTITY_BEFORE = """
CREATE OR REPLACE FUNCTION fiscal_protect_identity() RETURNS trigger AS $$
            BEGIN
            IF (NEW.spirometry_exam_id, NEW.environment, NEW.created_by,
                NEW.idempotency_key, NEW.idempotency_fingerprint) IS DISTINCT FROM
               (OLD.spirometry_exam_id, OLD.environment, OLD.created_by,
                OLD.idempotency_key, OLD.idempotency_fingerprint) THEN
                RAISE EXCEPTION 'immutable fiscal identity';
            END IF;
            RETURN NEW;
            END;
$$ LANGUAGE plpgsql
"""


# ------------------------------------------------------------ fiscal_documents

def _documents_table(*, after: bool) -> sa.Table:
    """A tabela como as migrations a deixaram; SQLite não reflete CHECK."""
    columns = [
        sa.Column("id", sa.String(length=36), nullable=False),
        # NOT NULL antes da M26.34; nulável depois dela.
        sa.Column("spirometry_exam_id", sa.String(length=36), nullable=after),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("eligibility", sa.String(length=20), nullable=False),
        sa.Column("blocking_reasons", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]
    constraints = [
        sa.CheckConstraint("environment IN ('mock','restricted','production')",
                           name="fiscal_document_environment"),
        sa.CheckConstraint("state IN (%s)" % ",".join("'%s'" % s for s in STATES),
                           name="fiscal_document_state"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                name="fk_fiscal_documents_created_by_users"),
        sa.ForeignKeyConstraint(["spirometry_exam_id"], ["spirometry_exams.id"],
                                name="fk_fiscal_documents_spirometry_exam_id_spirometry_exams"),
        sa.PrimaryKeyConstraint("id", name="pk_fiscal_documents"),
        sa.UniqueConstraint("idempotency_key", name="uq_fiscal_documents_idempotency_key"),
        sa.UniqueConstraint("spirometry_exam_id", "environment", name="uq_fiscal_exam_environment"),
    ]
    if after:
        columns.append(sa.Column("kind", sa.String(length=10), nullable=False,
                                 server_default="single"))
        columns.append(sa.Column("recipient_person_id", sa.String(length=36), nullable=True))
        constraints.append(sa.CheckConstraint(KIND_CHECK, name="fiscal_document_kind"))
        constraints.append(sa.CheckConstraint(SHAPE_CHECK, name="fiscal_document_shape"))
        constraints.append(sa.ForeignKeyConstraint(
            ["recipient_person_id"], ["people.id"], name=FK_RECIPIENT))
    return sa.Table("fiscal_documents", sa.MetaData(naming_convention=NAMING_CONVENTION),
                    *columns, *constraints)


def _sqlite_documents(*, upgrade: bool) -> None:
    for name in ("fiscal_documents_no_delete", "fiscal_documents_identity"):
        op.execute("DROP TRIGGER IF EXISTS %s" % name)
    with op.batch_alter_table("fiscal_documents",
                              copy_from=_documents_table(after=not upgrade)) as batch_op:
        if upgrade:
            batch_op.add_column(sa.Column("kind", sa.String(length=10), nullable=False,
                                          server_default="single"))
            batch_op.add_column(sa.Column("recipient_person_id", sa.String(length=36),
                                          nullable=True))
            batch_op.alter_column("spirometry_exam_id", existing_type=sa.String(length=36),
                                  nullable=True)
            batch_op.create_foreign_key(FK_RECIPIENT, "people", ["recipient_person_id"], ["id"])
            batch_op.create_check_constraint("fiscal_document_kind", KIND_CHECK)
            batch_op.create_check_constraint("fiscal_document_shape", SHAPE_CHECK)
        else:
            batch_op.drop_constraint("fiscal_document_shape", type_="check")
            batch_op.drop_constraint("fiscal_document_kind", type_="check")
            batch_op.drop_constraint(FK_RECIPIENT, type_="foreignkey")
            batch_op.alter_column("spirometry_exam_id", existing_type=sa.String(length=36),
                                  nullable=False)
            batch_op.drop_column("recipient_person_id")
            batch_op.drop_column("kind")
    for statement in (DOCUMENT_TRIGGERS if upgrade else DOCUMENT_TRIGGERS_BEFORE):
        op.execute(statement)


# -------------------------------------------------------- fiscal_preparations

def _preparations_table() -> sa.Table:
    return sa.Table(
        "fiscal_preparations", sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("financial_entry_id", sa.String(length=36), nullable=True),
        sa.Column("policy_id", sa.String(length=36), nullable=True),
        sa.Column("recipient_person_id", sa.String(length=36), nullable=True),
        sa.Column("flow", sa.String(length=20), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=True),
        sa.Column("competence", sa.Date(), nullable=True),
        sa.Column("amount_snapshot", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("blocking_reasons", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_municipio_ibge", sa.String(length=7), nullable=True),
        sa.Column("amount_source", sa.String(length=40), nullable=True),
        sa.Column("partnership_id", sa.String(length=36), nullable=True),
        sa.Column("partner_unit_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(PREP_CHECK_BEFORE, name="fiscal_snapshot_source"),
        sa.CheckConstraint("service_municipio_ibge IS NULL OR length(service_municipio_ibge) = 7",
                           name="service_municipio_ibge_sete_digitos"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                name="fk_fiscal_preparations_created_by_users"),
        sa.ForeignKeyConstraint(["document_id"], ["fiscal_documents.id"],
                                name="fk_fiscal_preparations_document_id_fiscal_documents"),
        sa.ForeignKeyConstraint(["financial_entry_id"], ["financial_entries.id"],
                                name="fk_fiscal_preparations_financial_entry_id_financial_entries"),
        sa.ForeignKeyConstraint(["partner_unit_id"], ["partner_units.id"],
                                name="fk_fiscal_preparations_partner_unit_id_partner_units"),
        sa.ForeignKeyConstraint(["partnership_id"], ["partnerships.id"],
                                name="fk_fiscal_preparations_partnership_id_partnerships"),
        sa.ForeignKeyConstraint(["policy_id"], ["fiscal_policies.id"],
                                name="fk_fiscal_preparations_policy_id_fiscal_policies"),
        sa.ForeignKeyConstraint(["recipient_person_id"], ["people.id"],
                                name="fk_fiscal_preparations_recipient_person_id_people"),
        sa.PrimaryKeyConstraint("id", name="pk_fiscal_preparations"),
    )


def _sqlite_preparations(*, to_sql: str) -> None:
    for name in ("fiscal_preparations_no_update", "fiscal_preparations_no_delete"):
        op.execute("DROP TRIGGER IF EXISTS %s" % name)
    table = _preparations_table()
    if to_sql == PREP_CHECK_BEFORE:
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.CheckConstraint) and constraint.name == "fiscal_snapshot_source":
                constraint.sqltext = sa.text(PREP_CHECK_AFTER)
    with op.batch_alter_table("fiscal_preparations", copy_from=table) as batch_op:
        batch_op.drop_constraint("fiscal_snapshot_source", type_="check")
        batch_op.create_check_constraint("fiscal_snapshot_source", to_sql)
    op.create_index("ix_fiscal_preparations_document_id", "fiscal_preparations",
                    ["document_id"], unique=False, if_not_exists=True)
    for statement in PREPARATION_TRIGGERS:
        op.execute(statement)


# ---------------------------------------------------- fiscal_issuance_requests

def _requests_table(*, exam_nullable: bool) -> sa.Table:
    return sa.Table(
        "fiscal_issuance_requests", sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("spirometry_exam_id", sa.String(length=36), nullable=exam_nullable),
        sa.Column("preparation_id", sa.String(length=36), nullable=False),
        sa.Column("preparation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("recipient_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("amount_confirmed", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("authorized_by", sa.String(length=36), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operation_id", sa.String(length=36), nullable=True),
        sa.Column("result_code", sa.String(length=60), nullable=True),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("provider_post_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_get_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.CheckConstraint("amount_confirmed > 0", name="fiscal_issuance_request_amount"),
        sa.CheckConstraint("kind IN ('issue','reconcile')", name="fiscal_issuance_request_kind"),
        sa.CheckConstraint("kind = 'issue' OR provider_post_count = 0",
                           name="fiscal_issuance_request_reconcile_never_posts"),
        sa.CheckConstraint("provider_post_count <= 1", name="fiscal_issuance_request_one_post"),
        sa.CheckConstraint(
            "status IN ('authorized','running','issued','rejected','uncertain','reconciled',"
            "'refused','expired','interrupted')", name="fiscal_issuance_request_status"),
        sa.ForeignKeyConstraint(["authorized_by"], ["users.id"],
                                name="fk_fiscal_issuance_requests_authorized_by_users"),
        sa.ForeignKeyConstraint(["document_id"], ["fiscal_documents.id"],
                                name="fk_fiscal_issuance_requests_document_id_fiscal_documents"),
        sa.ForeignKeyConstraint(["preparation_id"], ["fiscal_preparations.id"],
                                name="fk_fiscal_issuance_requests_preparation_id_fiscal_preparations"),
        sa.ForeignKeyConstraint(["spirometry_exam_id"], ["spirometry_exams.id"],
                                name="fk_fiscal_issuance_requests_spirometry_exam_id_spirometry_exams"),
        sa.PrimaryKeyConstraint("id", name="pk_fiscal_issuance_requests"),
        sa.UniqueConstraint("idempotency_key",
                            name="uq_fiscal_issuance_requests_idempotency_key"),
    )


REQUEST_INDEXES = (
    ("uq_fiscal_issuance_request_active", ["document_id"],
     "status IN ('authorized', 'running')"),
    ("uq_fiscal_issuance_request_claimed_issue", ["document_id"],
     "kind = 'issue' AND claimed_at IS NOT NULL AND status != 'refused'"),
)


def _sqlite_requests(*, exam_nullable: bool) -> None:
    with op.batch_alter_table("fiscal_issuance_requests",
                              copy_from=_requests_table(exam_nullable=not exam_nullable)) as batch_op:
        batch_op.alter_column("spirometry_exam_id", existing_type=sa.String(length=36),
                              nullable=exam_nullable)
    # O índice simples de document_id (index=True no modelo) e os dois
    # índices PARCIAIS caem com o rebuild e o Alembic não os repõe.
    op.create_index("ix_fiscal_issuance_requests_document_id",
                    "fiscal_issuance_requests", ["document_id"], unique=False,
                    if_not_exists=True)
    for name, columns, where in REQUEST_INDEXES:
        op.execute("DROP INDEX IF EXISTS %s" % name)
        op.execute("CREATE UNIQUE INDEX %s ON fiscal_issuance_requests (%s) WHERE %s"
                   % (name, ", ".join(columns), where))


# ------------------------------------------------------------------ itens

def _create_items_table() -> None:
    op.create_table(
        "fiscal_document_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("spirometry_exam_id", sa.String(length=36), nullable=False),
        sa.Column("financial_entry_id", sa.String(length=36), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount IS NULL OR amount > 0",
                           name=op.f("ck_fiscal_document_items_fiscal_item_amount_positivo")),
        sa.CheckConstraint("environment IN ('mock','restricted','production')",
                           name=op.f("ck_fiscal_document_items_fiscal_item_environment")),
        sa.CheckConstraint("position >= 1",
                           name=op.f("ck_fiscal_document_items_fiscal_item_position_positiva")),
        sa.ForeignKeyConstraint(["document_id"], ["fiscal_documents.id"],
                                name=op.f("fk_fiscal_document_items_document_id_fiscal_documents")),
        sa.ForeignKeyConstraint(["financial_entry_id"], ["financial_entries.id"],
                                name=op.f("fk_fiscal_document_items_financial_entry_id_financial_entries")),
        sa.ForeignKeyConstraint(["spirometry_exam_id"], ["spirometry_exams.id"],
                                name=op.f("fk_fiscal_document_items_spirometry_exam_id_spirometry_exams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fiscal_document_items")),
        sa.UniqueConstraint("document_id", "position", name=op.f("uq_fiscal_item_position")),
        sa.UniqueConstraint("document_id", "spirometry_exam_id",
                            name=op.f("uq_fiscal_item_document_exam")),
        sa.UniqueConstraint("spirometry_exam_id", "environment",
                            name=op.f("uq_fiscal_item_exam_environment")),
    )
    op.create_index(op.f("ix_fiscal_document_items_document_id"), "fiscal_document_items",
                    ["document_id"])


def _backfill_occupancy() -> int:
    """Um item de OCUPAÇÃO por documento já existente. Sem valor, sem receita:
    a verdade monetária daqueles documentos fica na preparação imutável."""
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, environment, spirometry_exam_id FROM fiscal_documents "
        "WHERE spirometry_exam_id IS NOT NULL "
        "AND id NOT IN (SELECT document_id FROM fiscal_document_items)"
    )).all()
    import uuid
    for document_id, environment, exam_id in rows:
        conn.execute(sa.text(
            "INSERT INTO fiscal_document_items "
            "(id, document_id, environment, spirometry_exam_id, financial_entry_id, "
            " amount, position, created_at) "
            "VALUES (:id, :doc, :env, :exam, NULL, NULL, 1, CURRENT_TIMESTAMP)"
        ), {"id": str(uuid.uuid4()), "doc": document_id, "env": environment, "exam": exam_id})
    return len(rows)


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        _sqlite_documents(upgrade=True)
    else:
        op.add_column("fiscal_documents", sa.Column("kind", sa.String(length=10),
                                                    nullable=False, server_default="single"))
        op.add_column("fiscal_documents", sa.Column("recipient_person_id",
                                                    sa.String(length=36), nullable=True))
        op.alter_column("fiscal_documents", "spirometry_exam_id",
                        existing_type=sa.String(length=36), nullable=True)
        op.create_foreign_key(op.f(FK_RECIPIENT), "fiscal_documents", "people",
                              ["recipient_person_id"], ["id"])
        op.create_check_constraint("fiscal_document_kind", "fiscal_documents", KIND_CHECK)
        op.create_check_constraint("fiscal_document_shape", "fiscal_documents", SHAPE_CHECK)
        op.execute(PG_IDENTITY_AFTER)

    _create_items_table()
    migrados = _backfill_occupancy()

    if sqlite:
        _sqlite_preparations(to_sql=PREP_CHECK_AFTER)
        _sqlite_requests(exam_nullable=True)
    else:
        op.drop_constraint("fiscal_snapshot_source", "fiscal_preparations", type_="check")
        op.create_check_constraint("fiscal_snapshot_source", "fiscal_preparations",
                                   PREP_CHECK_AFTER)
        op.alter_column("fiscal_issuance_requests", "spirometry_exam_id",
                        existing_type=sa.String(length=36), nullable=True)
    print("M26.34: %d documento(s) existente(s) ganharam item de ocupação "
          "(valor NULL de propósito)." % migrados)


def downgrade() -> None:
    # Fail closed: um documento agrupado não cabe no esquema anterior, e
    # apagá-lo destruiria a nota que ele representa.
    conn = op.get_bind()
    agrupados = conn.execute(sa.text(
        "SELECT count(*) FROM fiscal_documents WHERE kind = 'grouped'")).scalar_one()
    if agrupados:
        raise RuntimeError(
            "%d documento(s) fiscal(is) agrupado(s) existem; o downgrade da M26.34 "
            "apagaria evidência fiscal. Abortado." % agrupados)
    sqlite = conn.dialect.name == "sqlite"
    if sqlite:
        _sqlite_requests(exam_nullable=False)
        _sqlite_preparations(to_sql=PREP_CHECK_BEFORE)
    else:
        op.alter_column("fiscal_issuance_requests", "spirometry_exam_id",
                        existing_type=sa.String(length=36), nullable=False)
        op.drop_constraint("fiscal_snapshot_source", "fiscal_preparations", type_="check")
        op.create_check_constraint("fiscal_snapshot_source", "fiscal_preparations",
                                   PREP_CHECK_BEFORE)
    op.drop_index(op.f("ix_fiscal_document_items_document_id"),
                  table_name="fiscal_document_items")
    op.drop_table("fiscal_document_items")
    if sqlite:
        _sqlite_documents(upgrade=False)
    else:
        op.execute(PG_IDENTITY_BEFORE)
        op.drop_constraint("fiscal_document_shape", "fiscal_documents", type_="check")
        op.drop_constraint("fiscal_document_kind", "fiscal_documents", type_="check")
        op.drop_constraint(op.f(FK_RECIPIENT), "fiscal_documents", type_="foreignkey")
        op.alter_column("fiscal_documents", "spirometry_exam_id",
                        existing_type=sa.String(length=36), nullable=False)
        op.drop_column("fiscal_documents", "recipient_person_id")
        op.drop_column("fiscal_documents", "kind")
