"""Modelos SQLAlchemy 2 do Núcleo Operacional Nativo M15.

Convenções:
- id interno: UUID (string 36) gerado pelo servidor;
- public_code: código humano sequencial (PES-000001) via code_sequences;
- legacy_source / legacy_id / import_batch_id preservam a origem histórica;
- datas de negócio incompletas guardam data_original, precisao_original,
  dia_assumido;
- timestamps sempre em UTC (apresentação converte para America/Sao_Paulo);
- financeiro NUNCA guarda nome, telefone, CPF ou dado clínico — só IDs técnicos.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .ids import new_uuid


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


UUID_LEN = 36
FINGERPRINT_LEN = 64


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class LegacyMixin:
    legacy_source: Mapped[str | None] = mapped_column(String(120))
    legacy_id: Mapped[str | None] = mapped_column(String(120))
    import_batch_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("import_batches.id")
    )


# ---------------------------------------------------------------- núcleo

class CodeSequence(Base):
    __tablename__ = "code_sequences"
    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(400), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    roles: Mapped[list[Role]] = relationship(secondary="user_roles", lazy="selectin")


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id"), primary_key=True)


class AuthSession(Base):
    """Sessão de navegador (M21) — nunca guarda o segredo, só o hash dele.

    O cookie enviado ao navegador é "id.segredo.assinatura"; aqui ficam
    somente sha256(segredo) e sha256(csrf), de modo que um vazamento desta
    tabela não permite montar um cookie válido. `revoked_at` dá ao logout
    poder real de invalidação (o token bearer stateless não tinha isso) e
    `password_fingerprint` mantém a revogação automática já existente ao
    redefinir a senha. Nenhuma coluna aceita PII.
    """

    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(FINGERPRINT_LEN), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(FINGERPRINT_LEN), nullable=False)
    password_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    persistente: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_motivo: Mapped[str | None] = mapped_column(String(40))


class AuditLog(Base):
    """Trilha append-only: sem endpoint de update/delete; nunca guardar PII em detalhes."""

    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(UUID_LEN), index=True)
    acao: Mapped[str] = mapped_column(String(80), nullable=False)
    entidade: Mapped[str | None] = mapped_column(String(60), index=True)
    entidade_id: Mapped[str | None] = mapped_column(String(UUID_LEN), index=True)
    detalhes: Mapped[dict | None] = mapped_column(JSON)


class Person(Base, TimestampMixin, LegacyMixin):
    """Cadastro canônico: uma pessoa = um registro, seja lead, paciente ou ambos."""

    __tablename__ = "people"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    nome_completo: Mapped[str] = mapped_column(String(300), nullable=False)
    nome_normalizado: Mapped[str] = mapped_column(String(300), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ativo", nullable=False)
    nao_contatar: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    observacao: Mapped[str | None] = mapped_column(Text)
    contacts: Mapped[list["PersonContact"]] = relationship(
        back_populates="person", lazy="selectin"
    )


class PersonContact(Base, TimestampMixin):
    __tablename__ = "person_contacts"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)  # whatsapp|telefone|email|outro
    valor: Mapped[str] = mapped_column(String(200), nullable=False)
    valor_normalizado: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    principal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # dados sintéticos (seed/demonstração) nunca geram URL de WhatsApp
    nao_discavel: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    person: Mapped[Person] = relationship(back_populates="contacts")


class PersonRelationship(Base, TimestampMixin):
    """Vínculo explícito menor–responsável; nunca transfere contatos.

    A linha é a verdade corrente do vínculo. Criação é idempotente pela chave
    natural e desativação é auditada pela camada de serviço/API; não existe
    cascata de exclusão nem inferência por nome, telefone ou outros atributos.
    """

    __tablename__ = "person_relationships"
    id: Mapped[str] = mapped_column(
        String(UUID_LEN), primary_key=True, default=new_uuid
    )
    minor_person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    guardian_person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_legal_guardian: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "minor_person_id <> guardian_person_id",
            name="pessoas_relacionadas_distintas",
        ),
        UniqueConstraint(
            "minor_person_id",
            "guardian_person_id",
            "relationship_type",
            name="uq_person_relationship_natural_key",
        ),
    )


class Consent(Base):
    """Histórico rastreável de consentimento por canal (append-only por linha)."""

    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    canal: Mapped[str] = mapped_column(String(20), nullable=False)  # whatsapp|telefone|email
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # concedido|revogado|desconhecido
    origem: Mapped[str | None] = mapped_column(String(120))
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    registrado_por: Mapped[str | None] = mapped_column(String(UUID_LEN))
    observacao: Mapped[str | None] = mapped_column(Text)


class LegacyAlias(Base):
    """Vínculo id antigo -> entidade nova. Nunca renumerar, nunca perder o id legado."""

    __tablename__ = "legacy_aliases"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    entidade: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(UUID_LEN), index=True, nullable=False)
    legacy_source: Mapped[str] = mapped_column(String(120), nullable=False)
    legacy_id: Mapped[str] = mapped_column(String(120), nullable=False)
    import_batch_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("import_batches.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("entidade", "legacy_source", "legacy_id", name="uq_legacy_alias"),
    )


# ---------------------------------------------------------------- operação

class Lead(Base, TimestampMixin, LegacyMixin):
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    origem: Mapped[str | None] = mapped_column(String(120))
    canal_entrada: Mapped[str | None] = mapped_column(String(60))
    servico_interesse: Mapped[str | None] = mapped_column(String(60))  # espirometria|consulta|ambos
    modalidade: Mapped[str | None] = mapped_column(
        String(30)
    )  # residencial|cowork|clinica_parceira|indefinida
    etapa: Mapped[str] = mapped_column(String(30), default="novo", nullable=False)
    data_primeiro_contato: Mapped[date | None] = mapped_column(Date)
    data_primeiro_contato_original: Mapped[str | None] = mapped_column(String(40))
    data_primeiro_contato_precisao: Mapped[str | None] = mapped_column(String(15))
    data_primeiro_contato_dia_assumido: Mapped[bool] = mapped_column(Boolean, default=False)
    data_retomada_manual: Mapped[date | None] = mapped_column(Date)
    responsavel: Mapped[str | None] = mapped_column(String(120))
    observacao: Mapped[str | None] = mapped_column(Text)


class SpirometryExam(Base, TimestampMixin, LegacyMixin):
    __tablename__ = "spirometry_exams"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    data_exame: Mapped[date | None] = mapped_column(Date, index=True)
    data_exame_original: Mapped[str | None] = mapped_column(String(40))
    data_exame_precisao: Mapped[str | None] = mapped_column(String(15))
    data_exame_dia_assumido: Mapped[bool] = mapped_column(Boolean, default=False)
    modalidade: Mapped[str | None] = mapped_column(
        String(30)
    )  # residencial|cowork|clinica_parceira
    local_atendimento: Mapped[str | None] = mapped_column(String(200))
    partner_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("partners.id"))
    partner_unit_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_units.id")
    )
    status: Mapped[str] = mapped_column(String(20), default="Aguardando", nullable=False)
    # None = não informado (registros históricos); True/False = com/sem BD
    broncodilatador: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    origem: Mapped[str | None] = mapped_column(String(120))
    responsavel: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(FINGERPRINT_LEN))
    observacao: Mapped[str | None] = mapped_column(Text)


class Consultation(Base, TimestampMixin, LegacyMixin):
    __tablename__ = "consultations"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    data_consulta: Mapped[date | None] = mapped_column(Date, index=True)
    data_consulta_original: Mapped[str | None] = mapped_column(String(40))
    data_consulta_precisao: Mapped[str | None] = mapped_column(String(15))
    data_consulta_dia_assumido: Mapped[bool] = mapped_column(Boolean, default=False)
    modalidade: Mapped[str | None] = mapped_column(
        String(30)
    )  # teleconsulta|residencial|cowork|clinica_parceira
    profissional: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="Agendada", nullable=False)
    origem: Mapped[str | None] = mapped_column(String(120))
    responsavel: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(FINGERPRINT_LEN))
    observacao: Mapped[str | None] = mapped_column(Text)


class Interaction(Base):
    __tablename__ = "interactions"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    canal: Mapped[str] = mapped_column(String(20), nullable=False)
    direcao: Mapped[str] = mapped_column(String(10), default="enviado", nullable=False)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resumo: Mapped[str | None] = mapped_column(Text)
    resultado: Mapped[str | None] = mapped_column(String(60))
    user_id: Mapped[str | None] = mapped_column(String(UUID_LEN))
    followup_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("followups.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Followup(Base, TimestampMixin):
    __tablename__ = "followups"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    # ``person_id`` permanece como paciente por compatibilidade. O contato
    # pode ser outra pessoa (por exemplo, responsável legal do menor).
    contact_person_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True
    )

    @property
    def patient_person_id(self) -> str:
        return self.person_id
    tipo: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # pos_exame|pos_consulta|lead_sem_atendimento|encaminhamento_parceiro|manual
    origem_entidade: Mapped[str | None] = mapped_column(String(40))
    origem_id: Mapped[str | None] = mapped_column(String(UUID_LEN), index=True)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    due_date_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pendente", nullable=False)
    controlado_por_parceiro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    partner_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("partners.id"))
    responsavel: Mapped[str | None] = mapped_column(String(120))
    tentativas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluido_por: Mapped[str | None] = mapped_column(String(UUID_LEN))
    resultado: Mapped[str | None] = mapped_column(String(120))
    observacao: Mapped[str | None] = mapped_column(Text)


# Índices parciais únicos: no máximo UM follow-up pendente por pessoa/tipo/origem.
# Dois índices porque UNIQUE com coluna NULL não deduplica (SQLite e PostgreSQL
# tratam cada NULL como distinto) — um cobre origem preenchida, outro origem nula.
Index(
    "uq_followup_pendente_origem",
    Followup.person_id,
    Followup.tipo,
    Followup.origem_id,
    unique=True,
    sqlite_where=(Followup.status == "pendente") & (Followup.origem_id.is_not(None)),
    postgresql_where=(Followup.status == "pendente") & (Followup.origem_id.is_not(None)),
)
Index(
    "uq_followup_pendente_sem_origem",
    Followup.person_id,
    Followup.tipo,
    unique=True,
    sqlite_where=(Followup.status == "pendente") & (Followup.origem_id.is_(None)),
    postgresql_where=(Followup.status == "pendente") & (Followup.origem_id.is_(None)),
)


# ---------------------------------------------------------------- parceiros

class Partner(Base, TimestampMixin, LegacyMixin):
    __tablename__ = "partners"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), default="clinica", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="prospecto", nullable=False)
    cidade: Mapped[str | None] = mapped_column(String(120))
    observacao: Mapped[str | None] = mapped_column(Text)
    # M20: duplicata consolidada. O registro NUNCA é apagado — sai das listas
    # operacionais comuns e passa a resolver para o parceiro canônico, que
    # herda o código público antigo como alias legado.
    arquivado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    merged_into_partner_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partners.id")
    )
    __table_args__ = (
        CheckConstraint(
            "merged_into_partner_id IS NULL OR merged_into_partner_id <> id",
            name="partner_nao_mescla_em_si",
        ),
    )


class PartnerUnit(Base, TimestampMixin):
    __tablename__ = "partner_units"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    partner_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partners.id"), index=True, nullable=False
    )
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    bairro: Mapped[str | None] = mapped_column(String(120))
    cidade: Mapped[str | None] = mapped_column(String(120))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    observacao: Mapped[str | None] = mapped_column(Text)


class PartnerContact(Base, TimestampMixin):
    __tablename__ = "partner_contacts"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    partner_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partners.id"), index=True, nullable=False
    )
    partner_unit_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_units.id")
    )
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    cargo: Mapped[str | None] = mapped_column(String(120))
    telefone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(200))
    principal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    observacao: Mapped[str | None] = mapped_column(Text)


class PartnerUnitConfig(Base, TimestampMixin):
    """Configuração operacional de unidade parceira (M15.6C, ex-Pastore Config).

    NUNCA carrega valor monetário: colunas de preço/repasse/custo da fonte
    são referência bloqueada (M14.2) e ficam fora do modelo por contrato.
    """

    __tablename__ = "partner_unit_configs"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    partner_unit_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_units.id"), index=True, nullable=False
    )
    status: Mapped[str | None] = mapped_column(String(40))
    dia_semana: Mapped[str | None] = mapped_column(String(40))
    horario_inicio: Mapped[str | None] = mapped_column(String(20))
    horario_fim: Mapped[str | None] = mapped_column(String(20))
    capacidade_estimada_por_turno: Mapped[str | None] = mapped_column(String(40))
    observacao: Mapped[str | None] = mapped_column(Text)


class Partnership(Base, TimestampMixin):
    __tablename__ = "partnerships"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    partner_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partners.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), default="em_negociacao", nullable=False)
    data_inicio: Mapped[date | None] = mapped_column(Date)
    data_inicio_original: Mapped[str | None] = mapped_column(String(40))
    data_inicio_precisao: Mapped[str | None] = mapped_column(String(15))
    data_inicio_dia_assumido: Mapped[bool] = mapped_column(Boolean, default=False)
    modelo_repasse: Mapped[str] = mapped_column(String(20), default="indefinido", nullable=False)
    percentual_repasse: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    valor_repasse_fixo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    responsavel_soprolife: Mapped[str | None] = mapped_column(String(120))
    responsavel_followup: Mapped[str] = mapped_column(
        String(20), default="soprolife", nullable=False
    )  # soprolife|parceiro
    observacao: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint(
            "percentual_repasse IS NULL OR (percentual_repasse >= 0 AND percentual_repasse <= 100)",
            name="percentual_faixa",
        ),
        CheckConstraint(
            "valor_repasse_fixo IS NULL OR valor_repasse_fixo >= 0",
            name="valor_fixo_nao_negativo",
        ),
    )


class PartnerReferral(Base, TimestampMixin, LegacyMixin):
    """Pessoa <-> Encaminhamento <-> Parceiro <-> Unidade <-> Atendimento."""

    __tablename__ = "partner_referrals"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    partner_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partners.id"), index=True, nullable=False
    )
    partner_unit_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_units.id")
    )
    partner_contact_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_contacts.id")
    )
    data_encaminhamento: Mapped[date | None] = mapped_column(Date)
    data_encaminhamento_original: Mapped[str | None] = mapped_column(String(40))
    data_encaminhamento_precisao: Mapped[str | None] = mapped_column(String(15))
    data_encaminhamento_dia_assumido: Mapped[bool] = mapped_column(Boolean, default=False)
    servico_solicitado: Mapped[str | None] = mapped_column(String(60))
    data_agendada: Mapped[date | None] = mapped_column(Date)
    data_realizacao: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(40), default="Recebido da clínica", nullable=False)
    spirometry_exam_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("spirometry_exams.id")
    )
    consultation_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("consultations.id")
    )
    # Lado do ciclo financial_entries<->partner_referrals quebrado com
    # use_alter: a FK nasce depois, via op.create_foreign_key nomeada.
    financial_entry_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN),
        ForeignKey(
            "financial_entries.id",
            use_alter=True,
            name="fk_partner_referrals_financial_entry_id_financial_entries",
        ),
    )
    valor_cobrado: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valor_recebido: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    tipo_repasse: Mapped[str | None] = mapped_column(String(20))  # percentual|fixo|nenhum
    valor_repasse: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    percentual_repasse: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    status_repasse: Mapped[str | None] = mapped_column(String(30))
    laudo_enviado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_envio_laudo: Mapped[date | None] = mapped_column(Date)
    responsavel_soprolife: Mapped[str | None] = mapped_column(String(120))
    observacao_operacional: Mapped[str | None] = mapped_column(Text)
    autorizacao_contato_soprolife: Mapped[bool | None] = mapped_column(Boolean)
    responsavel_followup: Mapped[str] = mapped_column(
        String(20), default="soprolife", nullable=False
    )
    proximo_followup: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (
        CheckConstraint(
            "percentual_repasse IS NULL OR (percentual_repasse >= 0 AND percentual_repasse <= 100)",
            name="percentual_faixa",
        ),
        CheckConstraint(
            "valor_cobrado IS NULL OR valor_cobrado >= 0", name="valor_cobrado_nao_negativo"
        ),
        CheckConstraint(
            "valor_recebido IS NULL OR valor_recebido >= 0", name="valor_recebido_nao_negativo"
        ),
        CheckConstraint(
            "valor_repasse IS NULL OR valor_repasse >= 0", name="valor_repasse_nao_negativo"
        ),
    )


class PartnerSettlement(Base, TimestampMixin):
    __tablename__ = "partner_settlements"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    partner_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partners.id"), index=True, nullable=False
    )
    partnership_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partnerships.id")
    )
    partner_unit_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_units.id"), index=True
    )
    # Primeiro dia do mês de competência. O valor é derivado do mês dos
    # exames incluídos, nunca da data do recebimento.
    competencia: Mapped[date | None] = mapped_column(Date, index=True)
    periodo_inicio: Mapped[date | None] = mapped_column(Date)
    periodo_fim: Mapped[date | None] = mapped_column(Date)
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(20), default="aberto", nullable=False)
    data_envio: Mapped[date | None] = mapped_column(Date)
    observacao: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint(
            "valor_total IS NULL OR valor_total >= 0", name="valor_total_nao_negativo"
        ),
        UniqueConstraint(
            "partner_id",
            "partner_unit_id",
            "competencia",
            name="uq_partner_settlement_competencia_unidade",
        ),
    )


class PartnerSettlementItem(Base):
    """Exame individual pertencente a um fechamento mensal de parceiro.

    O vínculo é não monetário. A unicidade global do exame impede que ele
    participe de dois fechamentos; um fechamento cancelado deve ser reaberto
    ou corrigido, preservando a trilha, em vez de duplicado.
    """

    __tablename__ = "partner_settlement_items"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    settlement_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_settlements.id"), index=True, nullable=False
    )
    spirometry_exam_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("spirometry_exams.id"),
        unique=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


# ---------------------------------------------------------------- financeiro

class FinancialEntry(Base, TimestampMixin, LegacyMixin):
    """Fonte financeira canônica. Vinculada só por IDs técnicos — sem PII."""

    __tablename__ = "financial_entries"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)  # receita|despesa|repasse
    categoria: Mapped[str | None] = mapped_column(String(60))
    descricao: Mapped[str | None] = mapped_column(String(300))
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    moeda: Mapped[str] = mapped_column(String(3), default="BRL", nullable=False)
    data_competencia: Mapped[date | None] = mapped_column(Date, index=True)
    data_competencia_original: Mapped[str | None] = mapped_column(String(40))
    data_competencia_precisao: Mapped[str | None] = mapped_column(String(15))
    data_competencia_dia_assumido: Mapped[bool] = mapped_column(Boolean, default=False)
    data_recebimento: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="Pendente", nullable=False)
    forma_pagamento: Mapped[str | None] = mapped_column(String(20))
    origem_preco: Mapped[str | None] = mapped_column(String(20))
    spirometry_exam_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("spirometry_exams.id")
    )
    consultation_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("consultations.id")
    )
    partner_referral_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_referrals.id")
    )
    # Recebimento agregado de parceiro: no máximo UM lançamento por
    # fechamento. Nunca se liga aos pacientes/exames individualmente.
    partner_settlement_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN),
        ForeignKey("partner_settlements.id"),
        unique=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(FINGERPRINT_LEN))
    __table_args__ = (
        CheckConstraint("valor > 0", name="valor_positivo"),
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    financial_entry_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("financial_entries.id"), index=True, nullable=False
    )
    alvo_tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    alvo_id: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint("valor > 0", name="valor_positivo"),
    )


class PartnerTransfer(Base, TimestampMixin):
    __tablename__ = "partner_transfers"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    partner_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("partners.id"), index=True, nullable=False
    )
    partnership_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partnerships.id")
    )
    partner_referral_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_referrals.id")
    )
    settlement_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_settlements.id")
    )
    financial_entry_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("financial_entries.id")
    )
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="previsto", nullable=False)
    data_prevista: Mapped[date | None] = mapped_column(Date)
    data_pagamento: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(FINGERPRINT_LEN))
    __table_args__ = (
        CheckConstraint("valor > 0", name="valor_positivo"),
    )


# ---------------------------------------------------------------- migração

class ImportSnapshot(Base, TimestampMixin):
    """Snapshot privado imutável registrado a partir de um manifesto (M15.6A).

    Identidade = (workbook_alias, sheet_alias, sha256) — o mesmo conteúdo do
    mesmo lugar só pode ser registrado UMA vez. Aliases nunca guardam ID de
    planilha, credencial ou hostname; o arquivo fica no diretório privado
    aprovado (fora do Git) e aqui só entram metadados sem PII.
    """

    __tablename__ = "import_snapshots"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    workbook_alias: Mapped[str] = mapped_column(String(120), nullable=False)
    sheet_alias: Mapped[str] = mapped_column(String(120), nullable=False)
    snapshot_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arquivo: Mapped[str] = mapped_column(String(200), nullable=False)  # nome, nunca caminho
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(40), nullable=False)
    encoding: Mapped[str] = mapped_column(String(20), nullable=False)
    delimiter: Mapped[str] = mapped_column(String(4), nullable=False)
    headers: Mapped[list | None] = mapped_column(JSON)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    linha_inicial_dados: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="registrado", nullable=False)
    dry_run_batch_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("import_batches.id")
    )
    execute_batch_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("import_batches.id")
    )
    registered_by: Mapped[str | None] = mapped_column(String(UUID_LEN))
    __table_args__ = (
        UniqueConstraint(
            "workbook_alias", "sheet_alias", "sha256",
            name="uq_import_snapshot_identidade",
        ),
    )


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_name: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    modo: Mapped[str] = mapped_column(String(15), nullable=False)  # dry_run|executado
    # 64 comporta o maior status ("executado_aguardando_reconciliacao", 34)
    status: Mapped[str] = mapped_column(String(64), default="dry_run", nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ambiguous_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String(UUID_LEN))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Um mesmo arquivo (tipo+sha256) só pode ter UMA execução real — reexecução
# idempotente e proteção contra dois --execute simultâneos.
Index(
    "uq_import_batch_executado",
    ImportBatch.source_type,
    ImportBatch.sha256,
    unique=True,
    sqlite_where=ImportBatch.modo == "executado",
    postgresql_where=ImportBatch.modo == "executado",
)


class ImportRow(Base):
    __tablename__ = "import_rows"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    batch_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("import_batches.id"), index=True, nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    legacy_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(300))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(UUID_LEN))
    # SEM coluna de conteúdo bruto: a linha original pode conter PII; rastreio
    # é feito por (batch, número da linha, hash, motivo sanitizado).
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdentityCandidate(Base):
    """Correspondência provável entre registro novo e pessoa existente.

    Telefone/nome são CANDIDATOS, nunca prova. Nenhuma fusão automática.
    """

    __tablename__ = "identity_candidates"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    origem: Mapped[str] = mapped_column(String(20), default="import", nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("import_batches.id"))
    import_row_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("import_rows.id"))
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), index=True, nullable=False
    )
    candidate_person_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("people.id"))
    motivo: Mapped[str] = mapped_column(String(60), nullable=False)
    detalhes: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pendente", nullable=False)
    decidido_por: Mapped[str | None] = mapped_column(String(UUID_LEN))
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Sem candidatos pendentes duplicados para o mesmo par/motivo.
Index(
    "uq_identity_candidate_pendente",
    IdentityCandidate.person_id,
    IdentityCandidate.candidate_person_id,
    IdentityCandidate.motivo,
    unique=True,
    sqlite_where=IdentityCandidate.status == "pendente",
    postgresql_where=IdentityCandidate.status == "pendente",
)


class MigrationProvenance(Base):
    """Razão de proveniência da execução multiaba (M15.6C) — append-only.

    Uma linha por registro operacional criado pelo lote de execução:
    lote, tabela alvo, id criado, domínio de origem, fingerprint irreversível
    da referência privada (sha256 — nunca o valor), versão de mapeamento e
    chave de idempotência determinística ÚNICA. Reexecutar o mesmo plano
    encontra as chaves e não cria nada; o rollback seletivo usa SOMENTE os
    entity_id daqui, em ordem reversa de criação (coluna ordem).
    """

    __tablename__ = "migration_provenance"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    batch_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("import_batches.id"), index=True, nullable=False
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    entidade: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(UUID_LEN), index=True, nullable=False)
    source_domain: Mapped[str] = mapped_column(String(60), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("batch_id", "ordem", name="uq_migration_provenance_ordem"),
    )


class MigrationDecision(Base):
    """Decisões humanas de migração — append-only."""

    __tablename__ = "migration_decisions"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    identity_candidate_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("identity_candidates.id")
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    decisao: Mapped[str] = mapped_column(String(300), nullable=False)
    decidido_por: Mapped[str | None] = mapped_column(String(UUID_LEN))
    detalhes: Mapped[dict | None] = mapped_column(JSON)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
