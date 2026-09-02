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
    text,
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
    # M25.2 — dado demográfico exigido pelo laudo médico. Opcional e NUNCA
    # inferido: sem informação cadastrada o laudo imprime "não informado".
    sexo: Mapped[str | None] = mapped_column(String(20))
    observacao: Mapped[str | None] = mapped_column(Text)
    # M25.17 — cadastro interno de teste, fora da operação normal.
    #
    # Segue a MESMA convenção de `Partner.arquivado` (M20): o registro nunca é
    # apagado — sai das listas operacionais e continua íntegro para auditoria,
    # versões e hashes. É o que permite tirar os cenários das M25.13/M25.14 da
    # fila da médica sem destruir a evidência que provou aquelas missões.
    #
    # É um SINALIZADOR EXPLÍCITO, marcado registro a registro. A alternativa
    # tentadora — esconder quem se chama "TESTE…" — transformaria o nome do
    # paciente em regra de negócio: um paciente real chamado Teste sumiria da
    # fila, e um registro de teste renomeado voltaria a aparecer.
    # M25.18 — CPF do paciente, exigido pela CFM 2.381/2024 "quando houver".
    #
    # OPCIONAL de propósito: existe paciente sem CPF aplicável, e um campo
    # obrigatório produziria número inventado só para destravar o cadastro.
    # Guardado somente em dígitos (máscara é apresentação) e único quando
    # preenchido — dois cadastros com o mesmo CPF são a mesma pessoa, e é
    # esse índice que impede o prontuário partido em dois.
    cpf: Mapped[str | None] = mapped_column(String(11), unique=True, index=True)
    arquivado: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )
    arquivado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arquivado_motivo: Mapped[str | None] = mapped_column(String(200))
    contacts: Mapped[list["PersonContact"]] = relationship(
        back_populates="person", lazy="selectin"
    )
    __table_args__ = (
        # `length` é portável entre SQLite e PostgreSQL; a checagem de que
        # são DÍGITOS (e de que os verificadores fecham) vive em
        # `services/cpf.py`, único ponto por onde o valor entra. Um GLOB ou
        # regex aqui só funcionaria num dos dois bancos.
        CheckConstraint(
            "cpf IS NULL OR length(cpf) = 11",
            name="cpf_com_onze_digitos",
        ),
        CheckConstraint(
            "(arquivado = 0 AND arquivado_em IS NULL "
            "AND arquivado_motivo IS NULL) OR "
            "(arquivado = 1 AND arquivado_em IS NOT NULL "
            "AND arquivado_motivo IS NOT NULL)",
            name="arquivamento_com_evidencia",
        ),
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
    # M25.2 — campos exigidos pelo laudo médico. Ambos opcionais e nunca
    # inventados: ausentes, o laudo imprime "não informada/não informada".
    hora_exame: Mapped[str | None] = mapped_column(String(5))  # HH:MM
    indicacao_clinica: Mapped[str | None] = mapped_column(Text)
    observacao: Mapped[str | None] = mapped_column(Text)
    # M25.24 — ENCERRAMENTO OPERACIONAL do exame.
    #
    # Responde a uma pergunta que `status` não responde e não deveria:
    # "este exame ainda gera trabalho para alguém?". `status` descreve o
    # ATO — o exame foi realizado, liberado etc. — e é dado clínico/
    # operacional do atendimento. Reaproveitá-lo para dizer "não me mostre
    # mais" misturaria as duas coisas e, pior, faria a fila depender de um
    # campo que a operação edita por outros motivos.
    #
    # O caso concreto que criou isto: exames antigos cujos pacientes já
    # receberam o laudo POR FORA da plataforma. Eles não são pendência —
    # mas também não são "laudados aqui", e fabricar um laudo inexistente
    # para calá-los seria falsificar prontuário. O encerramento diz a
    # verdade: houve laudo, ele não passou por aqui, e não há o que fazer.
    #
    # NADA é apagado. Paciente, exame, MIR, laudos, versões e auditoria
    # continuam intactos e localizáveis na visão de históricos. É por isso
    # que o encerramento é REVERSÍVEL: reabrir apenas limpa estes quatro
    # campos e o exame volta para a fila como estava.
    #
    # Por exame, e nunca pela PESSOA: arquivar o cadastro inteiro (M25.17)
    # é para cenário de teste, e esconderia também os exames futuros de um
    # paciente real que continua sendo atendido.
    encerramento_motivo: Mapped[str | None] = mapped_column(String(40))
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encerrado_por_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    encerramento_observacao: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        # Mesma forma da `arquivamento_com_evidencia` de `people`: ou os
        # quatro campos estão vazios, ou os quatro estão preenchidos. Um
        # encerramento sem quem, quando e por quê é exatamente o registro
        # que ninguém consegue justificar seis meses depois.
        CheckConstraint(
            "(encerramento_motivo IS NULL AND encerrado_em IS NULL "
            "AND encerrado_por_user_id IS NULL "
            "AND encerramento_observacao IS NULL) OR "
            "(encerramento_motivo IS NOT NULL AND encerrado_em IS NOT NULL "
            "AND encerrado_por_user_id IS NOT NULL "
            "AND encerramento_observacao IS NOT NULL)",
            name="encerramento_com_evidencia",
        ),
    )


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
    # M25.2 — endereço institucional estruturado da unidade. Serve ao "local
    # de realização" do laudo (app/services/report_locations.py) para que
    # nenhum endereço fique fixo no template do PDF. São dados da CLÍNICA,
    # nunca do paciente.
    logradouro: Mapped[str | None] = mapped_column(String(200))
    uf: Mapped[str | None] = mapped_column(String(2))
    cep: Mapped[str | None] = mapped_column(String(12))
    telefone_central: Mapped[str | None] = mapped_column(String(40))
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
    # Dinheiro que SAI da SoproLife para o parceiro. Direção oposta ao
    # recebimento — nunca reaproveitar estes três campos para preço recebido.
    modelo_repasse: Mapped[str] = mapped_column(String(20), default="indefinido", nullable=False)
    percentual_repasse: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    valor_repasse_fixo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # M26.3 — dinheiro que ENTRA na SoproLife por exame feito na parceria.
    # Campos próprios justamente para não contaminar relatório de custo com
    # receita (`repasse_pastore` é somado em `custo_total`, docs/parceria-
    # pastore-planilha.md:130). O único lugar que os lê é
    # services/partner_pricing.py — ver o porquê lá.
    modelo_recebimento: Mapped[str] = mapped_column(
        String(20), default="indefinido", nullable=False
    )  # indefinido|valor_por_exame
    valor_recebido_por_exame: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    vigencia_inicio: Mapped[date | None] = mapped_column(Date)
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
        CheckConstraint(
            "valor_recebido_por_exame IS NULL OR valor_recebido_por_exame >= 0",
            name="valor_recebido_nao_negativo",
        ),
        # Uma regra de recebimento só é regra se disser QUANTO e A PARTIR DE
        # QUANDO. Sem as duas coisas o sistema não pode prever valor nenhum,
        # e é melhor o banco recusar do que o painel exibir um número que
        # ninguém consegue justificar contra o extrato.
        CheckConstraint(
            "modelo_recebimento <> 'valor_por_exame' OR ("
            " valor_recebido_por_exame IS NOT NULL AND vigencia_inicio IS NOT NULL)",
            name="recebimento_por_exame_completo",
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
    # M26 — ORDEM DO FECHAMENTO DENTRO DA MESMA COMPETÊNCIA.
    #
    # Até aqui a chave era (parceiro, unidade, competência), o que assumia
    # que um mês fecha uma vez só. A operação real desmentiu isso: o
    # fechamento de 2026-08 foi criado no dia 11, com os 3 exames que
    # existiam; os outros 14 do mesmo mês chegaram entre 15 e 29/08 e ficaram
    # sem destino — o mês já estava "ocupado" e não havia rota para anexar
    # exame a fechamento existente.
    #
    # A saída NÃO é reabrir um fechamento já valorado. O de agosto declara
    # R$ 328,50 para exatamente 3 exames, valor conferido contra o extrato do
    # parceiro; enfiar mais 14 exames ali dentro tornaria esse número mentira.
    # O que nasce é um fechamento COMPLEMENTAR — mesma competência, sequência
    # seguinte, valor próprio, recibo próprio. Cada número continua dizendo a
    # verdade sobre o conjunto de exames que ele cobre.
    sequencia: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "valor_total IS NULL OR valor_total >= 0", name="valor_total_nao_negativo"
        ),
        CheckConstraint("sequencia >= 1", name="sequencia_minima"),
        UniqueConstraint(
            "partner_id",
            "partner_unit_id",
            "competencia",
            "sequencia",
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

# M23.1 — predicados dos dois índices parciais de receita própria. Entradas
# novas chegam ao banco já com a categoria canônica exata; lower + trim de
# whitespace ASCII faz o backstop também abranger variantes históricas de
# caixa/espaço. PostgreSQL e SQLite dão nomes diferentes à função chr/char,
# por isso os predicados são explícitos por dialeto. NULL/vazio era o bypass
# de "categoria ausente" e, com o vínculo técnico, é a receita própria.
_SQLITE_CATEGORY_TRIM = (
    "trim(categoria, ' ' || char(9) || char(10) || char(11) "
    "|| char(12) || char(13))"
)
_POSTGRES_CATEGORY_TRIM = (
    "btrim(categoria, ' ' || chr(9) || chr(10) || chr(11) "
    "|| chr(12) || chr(13))"
)
RECEITA_ESPIROMETRIA_INDEX_PREDICATE_SQLITE = (
    "tipo = 'receita' AND spirometry_exam_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_SQLITE_CATEGORY_TRIM} = '' "
    f"OR lower({_SQLITE_CATEGORY_TRIM}) = 'espirometria')"
)
RECEITA_ESPIROMETRIA_INDEX_PREDICATE_POSTGRES = (
    "tipo = 'receita' AND spirometry_exam_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_POSTGRES_CATEGORY_TRIM} = '' "
    f"OR lower({_POSTGRES_CATEGORY_TRIM}) = 'espirometria')"
)
RECEITA_CONSULTA_INDEX_PREDICATE_SQLITE = (
    "tipo = 'receita' AND consultation_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_SQLITE_CATEGORY_TRIM} = '' "
    f"OR lower({_SQLITE_CATEGORY_TRIM}) = 'consulta')"
)
RECEITA_CONSULTA_INDEX_PREDICATE_POSTGRES = (
    "tipo = 'receita' AND consultation_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_POSTGRES_CATEGORY_TRIM} = '' "
    f"OR lower({_POSTGRES_CATEGORY_TRIM}) = 'consulta')"
)


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
        Index(
            "uq_financial_entries_receita_espirometria",
            "spirometry_exam_id",
            unique=True,
            sqlite_where=text(RECEITA_ESPIROMETRIA_INDEX_PREDICATE_SQLITE),
            postgresql_where=text(RECEITA_ESPIROMETRIA_INDEX_PREDICATE_POSTGRES),
        ),
        Index(
            "uq_financial_entries_receita_consulta",
            "consultation_id",
            unique=True,
            sqlite_where=text(RECEITA_CONSULTA_INDEX_PREDICATE_SQLITE),
            postgresql_where=text(RECEITA_CONSULTA_INDEX_PREDICATE_POSTGRES),
        ),
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


# ------------------------------------------------------------ M24C — laudos
#
# ReportDocument = um "caso" de laudo (1 exame -> N documentos ao longo do
# tempo; uma correção depois de finalizado nasce como um NOVO documento que
# supersede o anterior — o documento finalizado antigo nunca é mutado).
# ReportDocumentVersion = cada arquivo PDF de fato gravado em disco (o
# original enviado, cada rascunho composto, e a versão finalizada) — nunca é
# sobrescrita, sempre um arquivo novo (app/services/report_storage.py).
# ReportTemplate = registro de abreviações/textos de interpretação — nasce
# vazio/administrável; texto clínico de produção é decisão humana (item 17).
# ReportSignature = fronteira de integração de assinatura digital (ICP-
# Brasil futura) — nenhum provedor real está conectado nesta etapa; o único
# status possível hoje é "pendente".

STATUS_LAUDO_ATRIBUIDO = "atribuido"
STATUS_LAUDO_EM_ELABORACAO = "em_elaboracao"
STATUS_LAUDO_ASSINATURA_PENDENTE = "assinatura_pendente"
STATUS_LAUDO_ASSINADO = "assinado"
# M25.2 — liberação institucional: a médica atribuída, autenticada na
# própria sessão, executa a ação consciente "Assinar e liberar laudo". O
# conteúdo é congelado e o PDF nativo do laudo é gerado e bloqueado.
#
# `liberado` é DELIBERADAMENTE distinto de `assinado`. `assinado` continua
# reservado para uma assinatura eletrônica QUALIFICADA (PAdES/ICP-Brasil)
# de um provedor real, que não existe nesta etapa. Uma imagem de assinatura
# manuscrita não é certificado ICP-Brasil e nunca é tratada como tal.
STATUS_LAUDO_LIBERADO = "liberado"
# Estados legados M24A permanecem reconhecidos para que a migration seja
# aditiva e não reescreva evidência preexistente. Nenhum endpoint M24C cria
# novos documentos nesses estados.
STATUS_LAUDO_RASCUNHO = "rascunho"
STATUS_LAUDO_EM_REVISAO = "em_revisao"
STATUS_LAUDO_FINALIZADO = "finalizado"
STATUS_LAUDO_VALUES = (
    STATUS_LAUDO_ATRIBUIDO,
    STATUS_LAUDO_EM_ELABORACAO,
    STATUS_LAUDO_ASSINATURA_PENDENTE,
    STATUS_LAUDO_ASSINADO,
    STATUS_LAUDO_LIBERADO,
    STATUS_LAUDO_RASCUNHO,
    STATUS_LAUDO_EM_REVISAO,
    STATUS_LAUDO_FINALIZADO,
)

VERSAO_TIPO_ORIGINAL = "original"
VERSAO_TIPO_RASCUNHO = "rascunho"
VERSAO_TIPO_ASSINATURA_PENDENTE = "assinatura_pendente"
VERSAO_TIPO_ASSINADO = "assinado"
VERSAO_TIPO_FINALIZADO = "finalizado"
# M25.2 — o laudo SoproLife é um documento PRÓPRIO, gerado nativamente, e
# NUNCA uma composição sobre o PDF técnico da MIR. O PDF da MIR permanece
# intacto na versão `original` e continua disponível separadamente.
VERSAO_TIPO_LAUDO_PREVIA = "laudo_previa"
VERSAO_TIPO_LAUDO_LIBERADO = "laudo_liberado"
VERSAO_TIPO_LAUDO_ADENDO = "laudo_adendo"
# M25.20 — o PDF que VOLTA assinado por fora, com o certificado da médica.
#
# É uma versão NOVA e separada: o laudo concluído para assinatura e o PDF
# técnico da MIR continuam intactos, cada um na sua versão. Sobrescrever
# qualquer um dos dois apagaria justamente a prova de que o arquivo assinado
# corresponde ao que foi concluído.
#
# O nome NÃO diz "validado": receber um PDF que parece assinado não é o
# mesmo que ter conferido a cadeia ICP-Brasil. O que o sistema fez até aqui
# foi RECEBER e PAREAR — a validação vive em `external_signed_documents`.
VERSAO_TIPO_LAUDO_ASSINADO_EXTERNO = "laudo_assinado_externo_recebido"
VERSAO_TIPO_VALUES = (
    VERSAO_TIPO_ORIGINAL,
    VERSAO_TIPO_RASCUNHO,
    VERSAO_TIPO_ASSINATURA_PENDENTE,
    VERSAO_TIPO_ASSINADO,
    VERSAO_TIPO_FINALIZADO,
    VERSAO_TIPO_LAUDO_PREVIA,
    VERSAO_TIPO_LAUDO_LIBERADO,
    VERSAO_TIPO_LAUDO_ADENDO,
    VERSAO_TIPO_LAUDO_ASSINADO_EXTERNO,
)
# Versões que representam o laudo médico próprio (baixável separadamente do
# PDF técnico da MIR).
VERSAO_TIPOS_LAUDO_NATIVO = (
    VERSAO_TIPO_LAUDO_PREVIA,
    VERSAO_TIPO_LAUDO_LIBERADO,
    VERSAO_TIPO_LAUDO_ADENDO,
)

# "signing-required" (item 7 do pedido M24A) é representado aqui como o
# único valor hoje alcançável de signature_status: um laudo finalizado
# SEMPRE entra em ASSINATURA_PENDENTE, porque nenhum provedor ICP-Brasil
# real está configurado nesta etapa. ASSINADA/REJEITADA existem só para o
# adapter futuro (app/services/signature_provider.py) — nenhum caminho de
# código nesta etapa consegue gravá-los.
SIGNATURE_STATUS_PENDENTE = "assinatura_pendente"
SIGNATURE_STATUS_ASSINADA = "assinada"
SIGNATURE_STATUS_REJEITADA = "rejeitada"
# M25.2 — liberação institucional autenticada. NÃO é assinatura qualificada
# ICP-Brasil: representa a ação consciente da médica atribuída na própria
# sessão individual, com trilha de auditoria e hash do PDF liberado. O
# próprio documento declara essa natureza de forma explícita.
SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL = "liberada_institucional"
SIGNATURE_STATUS_VALUES = (
    SIGNATURE_STATUS_PENDENTE,
    SIGNATURE_STATUS_ASSINADA,
    SIGNATURE_STATUS_REJEITADA,
    SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL,
)
# M25.14 — largura das colunas que guardam esses valores. Era VARCHAR(20) nos
# dois lugares (report_documents.signature_status e report_signatures.status),
# mas "liberada_institucional" tem 22 caracteres: em PostgreSQL a liberação
# abortava com StringDataRightTruncation, e em SQLite passava porque lá o
# limite de VARCHAR é ignorado. A largura é derivada dos próprios valores,
# com folga, para não voltar a divergir quando um status novo entrar na lista.
SIGNATURE_STATUS_LEN = 40
assert SIGNATURE_STATUS_LEN >= max(len(v) for v in SIGNATURE_STATUS_VALUES), (
    "SIGNATURE_STATUS_LEN não comporta todos os SIGNATURE_STATUS_VALUES"
)

BRAZIL_UF_VALUES = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT",
    "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO",
    "RR", "SC", "SP", "SE", "TO",
)
PHYSICIAN_VERIFICATION_VALUES = ("pending", "verified", "rejected")
REPORT_ORIGIN_VALUES = (
    "pastore",
    "coworking",
    "residencial",
    "clinica_parceira",
    "empresa_pcmso",
    "outro",
)
REPORT_ASSIGNMENT_REASON_VALUES = (
    "initial_assignment",
    "assignment_correction",
    "physician_unavailable",
    "profile_suspended",
    "operational_redistribution",
    "corrective_document",
    # M24D — recuperação admin-only de laudo preso em elaboração clínica
    # quando o médico atribuído fica indisponível DEPOIS do primeiro
    # rascunho (reatribuição comum só é permitida antes disso).
    "physician_unavailable_after_draft",
)
REPORT_ASSIGNMENT_EVENT_VALUES = (
    "assigned",
    "reassigned",
    "corrective_assigned",
    "recovered_after_draft",
)
REPORT_TEMPLATE_STATUS_VALUES = ("draft", "approved", "retired")
REPORT_FOOTER_STATUS_VALUES = ("test", "draft", "approved", "retired")


class PhysicianProfile(Base, TimestampMixin):
    """Perfil profissional um-para-um, separado da conta de autenticação."""

    __tablename__ = "physician_profiles"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    professional_name: Mapped[str] = mapped_column(String(220), nullable=False)
    crm_number: Mapped[str] = mapped_column(String(12), nullable=False)
    crm_state: Mapped[str] = mapped_column(String(2), nullable=False)
    rqe: Mapped[str | None] = mapped_column(String(30))
    # M25.2 — apresentação humana do CRM no laudo (ex.: "52.62307-5"). O
    # armazenamento canônico continua normalizado em dígitos; este campo é
    # só formatação e é validado para conter EXATAMENTE os mesmos dígitos
    # de `crm_number` (ver ck_physician_profiles_crm_display_digits).
    crm_display: Mapped[str | None] = mapped_column(String(30))
    # M25.8 — certificado ICP-Brasil com que a médica assina fora do painel.
    # Vinculado na PRIMEIRA assinatura validada e conferido em todas as
    # seguintes: é isso que faz o sistema recusar um laudo assinado por
    # outra pessoa. Guarda o subject do certificado — dado profissional
    # público. Nunca CPF, nunca chave, nunca biometria.
    icp_signer_subject: Mapped[str | None] = mapped_column(String(300))
    icp_signer_bound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Especialidade impressa no bloco médico (ex.: "Médica Pneumologista").
    especialidade: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    # M24D — referência técnica segura da verificação (código/ID de processo
    # de checagem CRM/UF, nunca texto livre nem dado de paciente). Some-se à
    # exigência de quatro olhos: quem verifica nunca pode ser o próprio
    # perfil verificado (ver ck_physician_profiles_verification_not_self).
    verification_reference: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint(
            "length(trim(professional_name)) >= 2",
            name="professional_name_present",
        ),
        CheckConstraint(
            "length(crm_number) BETWEEN 1 AND 12",
            name="crm_number_length",
        ),
        CheckConstraint(
            f"crm_state IN {BRAZIL_UF_VALUES!r}",
            name="crm_state_valid",
        ),
        CheckConstraint(
            f"verification_status IN {PHYSICIAN_VERIFICATION_VALUES!r}",
            name="verification_status_valid",
        ),
        CheckConstraint(
            "("
            "verification_status = 'verified' AND "
            "verified_at IS NOT NULL AND verified_by_user_id IS NOT NULL AND "
            "verification_reference IS NOT NULL AND "
            "length(trim(verification_reference)) >= 4"
            ") OR ("
            "verification_status <> 'verified' AND "
            "verified_at IS NULL AND verified_by_user_id IS NULL AND "
            "verification_reference IS NULL"
            ")",
            name="verification_evidence_complete",
        ),
        CheckConstraint(
            "verified_by_user_id IS NULL OR verified_by_user_id <> user_id",
            name="verification_not_self",
        ),
        # A igualdade de dígitos entre `crm_display` e `crm_number` é
        # validada na aplicação (normalize.crm_display_matches) porque uma
        # extração de dígitos portátil entre SQLite e PostgreSQL não cabe
        # num CHECK. Aqui só garantimos que o campo não fique vazio.
        CheckConstraint(
            "crm_display IS NULL OR length(trim(crm_display)) >= 1",
            name="crm_display_present",
        ),
        CheckConstraint(
            "especialidade IS NULL OR length(trim(especialidade)) >= 2",
            name="especialidade_present",
        ),
    )


Index(
    "uq_physician_profiles_active_crm_uf",
    PhysicianProfile.crm_state,
    PhysicianProfile.crm_number,
    unique=True,
    sqlite_where=PhysicianProfile.active.is_(True),
    postgresql_where=PhysicianProfile.active.is_(True),
)


class ReportTemplate(Base, TimestampMixin):
    """Registro de templates de interpretação — abreviação + texto completo.

    Nasce vazio/administrável (item 9 do M24A): esta tabela só guarda a
    ESTRUTURA. Nenhum texto clínico de produção é semeado por esta etapa.
    """

    __tablename__ = "report_templates"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    codigo: Mapped[str] = mapped_column(String(48), nullable=False)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    texto_tooltip: Mapped[str | None] = mapped_column(String(240))
    texto_completo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    versao: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    clinically_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    supersedes_template_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("report_templates.id")
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_por: Mapped[str | None] = mapped_column(String(UUID_LEN))
    __table_args__ = (
        UniqueConstraint("codigo", "versao", name="uq_report_template_code_version"),
        UniqueConstraint(
            "supersedes_template_id",
            name="uq_report_template_supersedes_template_id",
        ),
        CheckConstraint("versao > 0", name="version_positive"),
        CheckConstraint(
            f"status IN {REPORT_TEMPLATE_STATUS_VALUES!r}",
            name="status_valid",
        ),
        CheckConstraint(
            "("
            "clinically_approved = true AND status = 'approved' AND "
            "approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL"
            ") OR ("
            "clinically_approved = false AND status <> 'approved' AND "
            "approved_by_user_id IS NULL AND approved_at IS NULL"
            ")",
            name="clinical_approval_evidence",
        ),
    )


class ReportFooterTemplate(Base, TimestampMixin):
    """Texto institucional versionado; M24C semeia somente o modelo TEST."""

    __tablename__ = "report_footer_templates"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="test")
    production_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_report_footer_code_version"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            f"status IN {REPORT_FOOTER_STATUS_VALUES!r}",
            name="status_valid",
        ),
        CheckConstraint(
            "production_approved = false OR status = 'approved'",
            name="production_approval_coherent",
        ),
    )


class ReportDocument(Base, TimestampMixin):
    """Um "caso" de laudo PDF vinculado a UM exame de espirometria."""

    __tablename__ = "report_documents"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    spirometry_exam_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("spirometry_exams.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), default=STATUS_LAUDO_ATRIBUIDO, nullable=False
    )
    origin_type: Mapped[str | None] = mapped_column(String(30))
    origin_label: Mapped[str | None] = mapped_column(String(120))
    origin_partner_unit_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("partner_units.id")
    )
    # Preenchido apenas quando `status == finalizado`. Representa o
    # "signing-required" do item 7 — sempre PENDENTE nesta etapa.
    signature_status: Mapped[str | None] = mapped_column(
        String(SIGNATURE_STATUS_LEN)
    )
    # Sem FK de verdade de propósito: report_document_versions referencia
    # report_documents (não o contrário) — declarar os dois lados como FK
    # criaria uma dependência circular entre as tabelas. A referência é
    # validada na camada de aplicação (sempre aponta para uma versão desta
    # mesma linha); a integridade "de verdade" já está garantida pelo lado
    # versão->documento, que é obrigatório e indexado.
    current_version_id: Mapped[str | None] = mapped_column(String(UUID_LEN))
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("report_documents.id")
    )
    # Invariante inverso e persistido da cadeia corretiva. A unicidade
    # garante no banco que um predecessor finalizado tenha no máximo UM
    # sucessor, inclusive sob duas transações concorrentes.
    corrects_document_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("report_documents.id")
    )
    created_by_user_id: Mapped[str] = mapped_column(String(UUID_LEN), ForeignKey("users.id"), nullable=False)
    reviewer_user_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("users.id"))
    finalized_by_user_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("users.id"))
    submitted_for_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clinical_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_for_signature_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_reason_code: Mapped[str | None] = mapped_column(String(40))
    # ------------------------------------------------------------ M25.2
    # Liberação institucional do laudo próprio da SoproLife.
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    released_physician_profile_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("physician_profiles.id")
    )
    # Código único e opaco de verificação impresso no laudo (texto + QR).
    # Não é derivado de dado do paciente e não revela nada por si só.
    validation_code: Mapped[str | None] = mapped_column(String(24), unique=True)
    # M25.8 — rastreio da assinatura externa em lote. `signature_prepared_at`
    # marca o congelamento do PDF para assinatura; `signature_downloaded_at`
    # marca a saída dele no ZIP. É o que separa "revisado" de "já baixado".
    signature_prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    signature_downloaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    __table_args__ = (
        CheckConstraint(
            f"status IN {STATUS_LAUDO_VALUES!r}", name="status_valido",
        ),
        UniqueConstraint(
            "corrects_document_id",
            name="uq_report_documents_corrects_document_id",
        ),
        CheckConstraint(
            f"origin_type IS NULL OR origin_type IN {REPORT_ORIGIN_VALUES!r}",
            name="origin_type_valid",
        ),
        CheckConstraint(
            "("
            "status = 'assinado' AND signature_status = 'assinada' AND "
            "signed_at IS NOT NULL"
            ") OR status <> 'assinado'",
            name="signed_state_complete",
        ),
        CheckConstraint(
            "("
            "status NOT IN ("
            "'atribuido', 'em_elaboracao', "
            "'assinatura_pendente', 'assinado', 'liberado'"
            ")"
            ") OR ("
            "status = 'liberado' AND "
            "clinical_started_at IS NOT NULL AND "
            "ready_for_signature_at IS NOT NULL AND "
            "released_at IS NOT NULL AND "
            "released_by_user_id IS NOT NULL AND "
            "released_physician_profile_id IS NOT NULL AND "
            "validation_code IS NOT NULL AND "
            "signed_at IS NULL AND "
            "signature_status = 'liberada_institucional'"
            ") OR ("
            "status = 'atribuido' AND "
            "clinical_started_at IS NULL AND "
            "ready_for_signature_at IS NULL AND "
            "signed_at IS NULL AND signature_status IS NULL"
            ") OR ("
            "status = 'em_elaboracao' AND "
            "clinical_started_at IS NOT NULL AND "
            "ready_for_signature_at IS NULL AND "
            "signed_at IS NULL AND signature_status IS NULL"
            ") OR ("
            "status = 'assinatura_pendente' AND "
            "clinical_started_at IS NOT NULL AND "
            "ready_for_signature_at IS NOT NULL AND "
            "signed_at IS NULL AND "
            "signature_status = 'assinatura_pendente'"
            ") OR ("
            "status = 'assinado' AND "
            "clinical_started_at IS NOT NULL AND "
            "ready_for_signature_at IS NOT NULL AND "
            "signed_at IS NOT NULL AND signature_status = 'assinada'"
            ")",
            name="clinical_state_coherent",
        ),
        # M25.2 — evidência de liberação só existe em documento liberado.
        # Nenhum estado anterior pode carregar carimbo de liberação, e
        # nenhum código de validação é alocado antes da ação consciente.
        # M25.8 — `validation_code` deixou de ser exclusivo de 'liberado'.
        # Ele é IMPRESSO no PDF (texto e QR) e precisa existir ANTES da
        # assinatura externa, quando o laudo está em 'assinatura_pendente':
        # depois de assinado o arquivo não pode mais ser alterado para
        # receber o código. O que é evidência de LIBERAÇÃO de verdade —
        # `released_at`, `released_by_user_id` e
        # `released_physician_profile_id` — continua exclusivo de 'liberado'.
        CheckConstraint(
            "status = 'liberado' OR ("
            "released_at IS NULL AND "
            "released_by_user_id IS NULL AND "
            "released_physician_profile_id IS NULL AND ("
            "validation_code IS NULL OR "
            "status IN ('assinatura_pendente', 'assinado')"
            "))",
            name="release_evidence_coherent",
        ),
    )


class ReportAssignment(Base):
    """Atribuição atual + histórico preservado; nunca apaga linhas antigas."""

    __tablename__ = "report_assignments"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    report_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("report_documents.id"), nullable=False, index=True
    )
    physician_profile_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("physician_profiles.id"), nullable=False, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    supersedes_assignment_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("report_assignments.id")
    )
    __table_args__ = (
        UniqueConstraint(
            "supersedes_assignment_id",
            name="uq_report_assignment_supersedes_assignment_id",
        ),
        CheckConstraint(
            f"reason_code IN {REPORT_ASSIGNMENT_REASON_VALUES!r}",
            name="reason_code_valid",
        ),
        CheckConstraint(
            "(active = true AND ended_at IS NULL) OR "
            "(active = false AND ended_at IS NOT NULL)",
            name="active_period_coherent",
        ),
        CheckConstraint(
            "("
            "reason_code IN ('initial_assignment', 'corrective_document') AND "
            "supersedes_assignment_id IS NULL"
            ") OR ("
            "reason_code NOT IN ('initial_assignment', 'corrective_document') AND "
            "supersedes_assignment_id IS NOT NULL"
            ")",
            name="reason_supersession_coherent",
        ),
    )


Index(
    "uq_report_assignments_one_active_per_document",
    ReportAssignment.report_document_id,
    unique=True,
    sqlite_where=ReportAssignment.active.is_(True),
    postgresql_where=ReportAssignment.active.is_(True),
)


class ReportAssignmentEvent(Base):
    """Evento técnico append-only; sem texto livre, paciente ou dado clínico."""

    __tablename__ = "report_assignment_events"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    report_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("report_documents.id"), nullable=False, index=True
    )
    assignment_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("report_assignments.id"), nullable=False, unique=True
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    physician_profile_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("physician_profiles.id"), nullable=False
    )
    previous_physician_profile_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("physician_profiles.id")
    )
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    performed_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    __table_args__ = (
        CheckConstraint(
            f"event_type IN {REPORT_ASSIGNMENT_EVENT_VALUES!r}",
            name="event_type_valid",
        ),
        CheckConstraint(
            f"reason_code IN {REPORT_ASSIGNMENT_REASON_VALUES!r}",
            name="reason_code_valid",
        ),
    )


class ReportDocumentVersion(Base):
    """Um arquivo PDF de fato gravado — imutável desde a criação da linha.

    `storage_path` é um caminho INTERNO relativo à raiz configurada
    (M15_REPORTS_STORAGE_DIR); nunca é devolvido bruto por uma resposta de
    API (item 11 do M24A) — só serve para o serviço de armazenamento montar
    o caminho absoluto.
    """

    __tablename__ = "report_document_versions"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    report_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("report_documents.id"), index=True, nullable=False
    )
    # M25.20 — 40 caracteres. Era String(20), estreito demais para
    # `laudo_assinado_externo_recebido`. Alargar é aditivo: nenhum valor
    # existente muda, e o CHECK `kind_valido` continua sendo quem decide
    # quais tipos são aceitos de fato.
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # original|rascunho|finalizado
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(40), default="application/pdf", nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("report_templates.id"))
    # Evidência clínica imutável da composição. `template_id` identifica o
    # registro editável; estes quatro campos congelam o conteúdo efetivamente
    # usado por esta versão e são copiados sem alteração para a finalizada.
    template_code_snapshot: Mapped[str | None] = mapped_column(String(20))
    template_version_snapshot: Mapped[int | None] = mapped_column(Integer)
    template_text_snapshot: Mapped[str | None] = mapped_column(Text)
    template_text_sha256: Mapped[str | None] = mapped_column(String(64))
    interpretation_text_snapshot: Mapped[str | None] = mapped_column(Text)
    interpretation_text_sha256: Mapped[str | None] = mapped_column(String(64))
    physician_profile_id_snapshot: Mapped[str | None] = mapped_column(String(UUID_LEN))
    physician_name_snapshot: Mapped[str | None] = mapped_column(String(220))
    physician_crm_number_snapshot: Mapped[str | None] = mapped_column(String(12))
    physician_crm_state_snapshot: Mapped[str | None] = mapped_column(String(2))
    physician_rqe_snapshot: Mapped[str | None] = mapped_column(String(30))
    origin_type_snapshot: Mapped[str | None] = mapped_column(String(30))
    origin_label_snapshot: Mapped[str | None] = mapped_column(String(120))
    origin_partner_unit_id_snapshot: Mapped[str | None] = mapped_column(
        String(UUID_LEN)
    )
    footer_template_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("report_footer_templates.id")
    )
    footer_code_snapshot: Mapped[str | None] = mapped_column(String(40))
    footer_version_snapshot: Mapped[int | None] = mapped_column(Integer)
    footer_text_snapshot: Mapped[str | None] = mapped_column(Text)
    footer_text_sha256: Mapped[str | None] = mapped_column(String(64))
    issued_at_snapshot: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    placement: Mapped[str | None] = mapped_column(String(20))
    # ------------------------------------------------------------ M25.2
    # Evidência clínica congelada do laudo NATIVO. Preenchida somente nas
    # versões `laudo_previa`/`laudo_liberado`/`laudo_adendo`; a versão
    # `original` (PDF técnico da MIR) nunca recebe estes campos.
    #
    # `interpretation_text_snapshot` continua guardando o texto efetivamente
    # assinado; os campos abaixo guardam a ESCOLHA de catálogo que originou
    # aquele texto, para que a decisão da médica fique auditável mesmo
    # depois de ela editar livremente a redação.
    conclusion_code_snapshot: Mapped[str | None] = mapped_column(String(40))
    conclusion_text_snapshot: Mapped[str | None] = mapped_column(Text)
    bronchodilator_code_snapshot: Mapped[str | None] = mapped_column(String(40))
    bronchodilator_text_snapshot: Mapped[str | None] = mapped_column(Text)
    observations_snapshot: Mapped[str | None] = mapped_column(Text)
    exam_has_post_bd_snapshot: Mapped[bool | None] = mapped_column(Boolean)
    # Local de realização estruturado (app/services/report_locations.py).
    location_name_snapshot: Mapped[str | None] = mapped_column(String(240))
    location_address_snapshot: Mapped[str | None] = mapped_column(String(300))
    location_contact_snapshot: Mapped[str | None] = mapped_column(String(120))
    location_partner_unit_id_snapshot: Mapped[str | None] = mapped_column(
        String(UUID_LEN)
    )
    location_source_snapshot: Mapped[str | None] = mapped_column(String(40))
    # Código impresso/QR e evidência da liberação institucional.
    validation_code_snapshot: Mapped[str | None] = mapped_column(String(24))
    released_at_snapshot: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Referência técnica do ativo manuscrito usado. NUNCA os bytes da
    # imagem, NUNCA um caminho absoluto — só id opaco e hash.
    signature_asset_id_snapshot: Mapped[str | None] = mapped_column(
        String(UUID_LEN)
    )
    signature_asset_sha256_snapshot: Mapped[str | None] = mapped_column(
        String(64)
    )
    addendum_sequence: Mapped[int | None] = mapped_column(Integer)
    created_by_user_id: Mapped[str] = mapped_column(String(UUID_LEN), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("report_document_id", "version_number", name="uq_versao_numero"),
        CheckConstraint(f"kind IN {VERSAO_TIPO_VALUES!r}", name="kind_valido"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positivo"),
        CheckConstraint("page_count > 0", name="page_count_positivo"),
        CheckConstraint(
            "("
            "template_code_snapshot IS NULL AND "
            "template_version_snapshot IS NULL AND "
            "template_text_snapshot IS NULL AND "
            "template_text_sha256 IS NULL"
            ") OR ("
            "template_code_snapshot IS NOT NULL AND "
            "template_version_snapshot IS NOT NULL AND "
            "template_version_snapshot > 0 AND "
            "template_text_snapshot IS NOT NULL AND "
            "template_text_sha256 IS NOT NULL"
            ")",
            name="template_snapshot_completo",
        ),
        CheckConstraint(
            "("
            "interpretation_text_snapshot IS NULL AND "
            "interpretation_text_sha256 IS NULL"
            ") OR ("
            "interpretation_text_snapshot IS NOT NULL AND "
            "interpretation_text_sha256 IS NOT NULL"
            ")",
            name="interpretation_snapshot_complete",
        ),
        CheckConstraint(
            "("
            "physician_profile_id_snapshot IS NULL AND "
            "physician_name_snapshot IS NULL AND "
            "physician_crm_number_snapshot IS NULL AND "
            "physician_crm_state_snapshot IS NULL AND "
            "origin_type_snapshot IS NULL"
            ") OR ("
            "physician_profile_id_snapshot IS NOT NULL AND "
            "physician_name_snapshot IS NOT NULL AND "
            "physician_crm_number_snapshot IS NOT NULL AND "
            f"physician_crm_state_snapshot IN {BRAZIL_UF_VALUES!r} AND "
            f"origin_type_snapshot IN {REPORT_ORIGIN_VALUES!r}"
            ")",
            name="physician_origin_snapshot_complete",
        ),
        CheckConstraint(
            "("
            "footer_template_id IS NULL AND "
            "footer_code_snapshot IS NULL AND "
            "footer_version_snapshot IS NULL AND "
            "footer_text_snapshot IS NULL AND "
            "footer_text_sha256 IS NULL AND "
            "issued_at_snapshot IS NULL"
            ") OR ("
            "footer_template_id IS NOT NULL AND "
            "footer_code_snapshot IS NOT NULL AND "
            "footer_version_snapshot IS NOT NULL AND "
            "footer_version_snapshot > 0 AND "
            "footer_text_snapshot IS NOT NULL AND "
            "footer_text_sha256 IS NOT NULL AND "
            "issued_at_snapshot IS NOT NULL"
            ")",
            name="footer_snapshot_complete",
        ),
        # ------------------------------------------------------------ M25.2
        CheckConstraint(
            "("
            "conclusion_code_snapshot IS NULL AND "
            "conclusion_text_snapshot IS NULL"
            ") OR ("
            "conclusion_code_snapshot IS NOT NULL AND "
            "conclusion_text_snapshot IS NOT NULL"
            ")",
            name="conclusion_snapshot_complete",
        ),
        CheckConstraint(
            "("
            "bronchodilator_code_snapshot IS NULL AND "
            "bronchodilator_text_snapshot IS NULL"
            ") OR ("
            "bronchodilator_code_snapshot IS NOT NULL AND "
            "bronchodilator_text_snapshot IS NOT NULL"
            ")",
            name="bronchodilator_snapshot_complete",
        ),
        CheckConstraint(
            "("
            "location_name_snapshot IS NULL AND "
            "location_source_snapshot IS NULL"
            ") OR ("
            "location_name_snapshot IS NOT NULL AND "
            "location_source_snapshot IS NOT NULL"
            ")",
            name="location_snapshot_complete",
        ),
        CheckConstraint(
            "("
            "signature_asset_id_snapshot IS NULL AND "
            "signature_asset_sha256_snapshot IS NULL"
            ") OR ("
            "signature_asset_id_snapshot IS NOT NULL AND "
            "signature_asset_sha256_snapshot IS NOT NULL"
            ")",
            name="signature_asset_snapshot_complete",
        ),
        CheckConstraint(
            "addendum_sequence IS NULL OR addendum_sequence > 0",
            name="addendum_sequence_positive",
        ),
        # Somente uma versão de laudo nativo carrega evidência de liberação;
        # a versão `original` (PDF da MIR) permanece intacta e sem carimbo.
        CheckConstraint(
            "("
            "kind = 'laudo_liberado' OR kind = 'laudo_adendo'"
            ") OR ("
            "released_at_snapshot IS NULL AND "
            "validation_code_snapshot IS NULL"
            ")",
            name="release_snapshot_only_on_released_kind",
        ),
    )


class ReportSignature(Base, TimestampMixin):
    """Fronteira de assinatura digital (ICP-Brasil futura) — M24A item 12.

    Nenhum provedor real está conectado nesta etapa. `status` só pode ser
    PENDENTE em qualquer caminho de código atual; ASSINADA/REJEITADA existem
    só para o adapter futuro (app/services/signature_provider.py) poder
    gravar um resultado real quando um provedor/certificado existir.
    """

    __tablename__ = "report_signatures"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    report_document_version_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_document_versions.id"),
        unique=True,
        nullable=False,
    )
    provider: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(
        String(SIGNATURE_STATUS_LEN),
        default=SIGNATURE_STATUS_PENDENTE,
        nullable=False,
    )
    external_reference: Mapped[str | None] = mapped_column(String(120))
    requested_by_user_id: Mapped[str | None] = mapped_column(String(UUID_LEN), ForeignKey("users.id"))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_metadata: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (
        CheckConstraint(f"status IN {SIGNATURE_STATUS_VALUES!r}", name="status_valido"),
    )


# ----------------------------------------------- M25.20 — assinatura externa
# em lote. A médica lauda um a um; o trabalho burocrático seguinte é o que
# acontece em lote.


# Direção de um lote. Baixar e devolver são eventos distintos e auditáveis
# separadamente: um lote baixado pode nunca voltar, e um lote que volta pode
# misturar laudos de vários downloads.
BATCH_DIRECAO_DOWNLOAD = "download"
BATCH_DIRECAO_UPLOAD = "upload"
BATCH_DIRECAO_VALUES = (BATCH_DIRECAO_DOWNLOAD, BATCH_DIRECAO_UPLOAD)

# Como o arquivo devolvido foi amarrado ao laudo de origem. A ordem é a da
# confiança: metadado carimbado > código impresso no conteúdo > código de
# verificação. O nome do arquivo NUNCA aparece aqui, porque nome de arquivo
# não é identificação — é pista.
PAREAMENTO_METADADO = "metadado_soprolife"
PAREAMENTO_CODIGO_LAUDO = "codigo_laudo_no_conteudo"
PAREAMENTO_CODIGO_VALIDACAO = "codigo_validacao_no_conteudo"
PAREAMENTO_VALUES = (
    PAREAMENTO_METADADO,
    PAREAMENTO_CODIGO_LAUDO,
    PAREAMENTO_CODIGO_VALIDACAO,
)

# Ciclo do documento assinado que voltou.
#
# `recebido_validacao_pendente` é o estado HONESTO logo após o upload:
# o arquivo chegou e foi pareado, mas ninguém verificou criptograficamente a
# cadeia ICP-Brasil. Só a conferência externa registrada por um administrador
# leva a `validado_externamente` — e mesmo esse nome diz quem validou (alguém,
# fora daqui), não que a SoproLife validou.
ASSINADO_EM_CONFERENCIA = "em_conferencia"
ASSINADO_RECEBIDO_VALIDACAO_PENDENTE = "recebido_validacao_pendente"
ASSINADO_VALIDADO_EXTERNAMENTE = "validado_externamente"
ASSINADO_ENTREGUE = "entregue"
# M25.29G — um arquivo recebido pode simplesmente NÃO servir: uma prévia
# assinada, um PDF idêntico ao final (devolvido sem assinar), um documento
# que não corresponde à versão final. O estado é UM só, genérico e
# reutilizável; o MOTIVO detalhado vive na auditoria, onde cabe texto e onde
# não é preciso criar um status novo a cada modo de falha descoberto.
#
# Recusado preserva tudo — blob, hash, versão histórica e trilha — e apenas
# deixa de contar como o documento assinado operacional.
ASSINADO_RECUSADO = "recusado"
# M25.29H — o estado do fluxo NOVO. O arquivo assinado voltou, foi associado
# ao laudo final por evidência objetiva e passou pelas guardas documentais da
# SoproLife. É o equivalente operacional de "pronto para entrega".
#
# O nome diz o que aconteceu e para no que aconteceu: recebido, assinado.
# Não diz validado, não diz conferido, não diz ICP-Brasil — a SoproLife
# continua sem verificar criptograficamente a cadeia da assinatura, e
# `qualified_signature` continua falso em toda a trilha.
#
# `recebido_validacao_pendente` deixa de nascer neste fluxo. Ele permanece
# no domínio porque documentos históricos estão nele e a auditoria precisa
# lê-los; nenhum documento novo passa por ali.
ASSINADO_ACEITO = "recebido_assinado"
ASSINADO_STATUS_VALUES = (
    ASSINADO_EM_CONFERENCIA,
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
    ASSINADO_ACEITO,
    ASSINADO_VALIDADO_EXTERNAMENTE,
    ASSINADO_ENTREGUE,
    ASSINADO_RECUSADO,
)


class ExternalSignatureBatch(Base):
    """Um download ou uma devolução em lote — BAT-000001.

    Existe para auditoria: responde "que arquivos saíram juntos, para quem,
    quando" sem precisar reconstruir isso a partir de timestamps soltos. O
    código público NÃO é protagonista na interface (M25.20 §19): a tela fala
    em médica, data e quantidade.
    """

    __tablename__ = "external_signature_batches"
    id: Mapped[str] = mapped_column(
        String(UUID_LEN), primary_key=True, default=new_uuid
    )
    public_code: Mapped[str] = mapped_column(
        String(12), unique=True, nullable=False
    )
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    physician_profile_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("physician_profiles.id"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False
    )
    # Quantos documentos o lote carregou. No download é o que entrou no ZIP;
    # no upload é quantos ARQUIVOS chegaram — inclusive os não identificados,
    # que não viram linha em `external_signed_documents` mas precisam ser
    # contados para a conferência bater.
    document_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            f"direction IN {BATCH_DIRECAO_VALUES!r}", name="direction_valida"
        ),
        CheckConstraint("document_count >= 0", name="document_count_nao_negativo"),
    )


class ExternalSignedDocument(Base):
    """Um PDF assinado que VOLTOU e foi pareado com segurança.

    Um arquivo que não pôde ser identificado com segurança NUNCA vira linha
    aqui: ele é reportado na conferência e devolvido à médica como "não
    identificado". Associar por semelhança de nome seria o modo mais fácil de
    anexar o laudo assinado ao paciente errado.

    A versão do PDF recebido é uma versão NOVA em `report_document_versions`;
    esta tabela guarda a evidência do recebimento e o ciclo de validação.
    """

    __tablename__ = "external_signed_documents"
    id: Mapped[str] = mapped_column(
        String(UUID_LEN), primary_key=True, default=new_uuid
    )
    report_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_documents.id"),
        nullable=False,
        index=True,
    )
    # A versão criada para os bytes assinados recebidos.
    report_document_version_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_document_versions.id"),
        unique=True,
        nullable=False,
    )
    # A versão CONCLUÍDA que originou o arquivo — o "laudo para assinatura"
    # que a médica levou para fora. Sem ela não há como provar depois que o
    # assinado corresponde ao que foi concluído.
    source_version_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_document_versions.id"),
        nullable=False,
    )
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("external_signature_batches.id"),
        nullable=False,
        index=True,
    )
    physician_profile_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("physician_profiles.id"),
        nullable=False,
        index=True,
    )
    uploader_user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nome COMO CHEGOU, para a médica reconhecer o arquivo na conferência.
    # Pode ter sido reescrito pelo iPhone ou pelo assinador; é registro do
    # que aconteceu, nunca critério de pareamento.
    received_filename: Mapped[str | None] = mapped_column(String(260))
    match_method: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default=ASSINADO_EM_CONFERENCIA, nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # ------------------------------------------------ conferência externa
    # Quem conferiu a assinatura FORA daqui (Validar ITI ou equivalente) e
    # registrou o resultado. Nunca guarda senha, certificado nem chave.
    validated_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    validation_method: Mapped[str | None] = mapped_column(String(40))
    validation_reference: Mapped[str | None] = mapped_column(String(200))
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    delivered_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    __table_args__ = (
        # Idempotência: o MESMO arquivo reenviado para o MESMO laudo não cria
        # uma segunda versão. É o caso real de quem manda o lote duas vezes
        # por não ter certeza se o primeiro envio funcionou.
        UniqueConstraint(
            "report_document_id", "sha256", name="uq_assinado_documento_sha256"
        ),
        CheckConstraint(
            f"match_method IN {PAREAMENTO_VALUES!r}", name="match_method_valido"
        ),
        CheckConstraint(
            f"status IN {ASSINADO_STATUS_VALUES!r}", name="assinado_status_valido"
        ),
        CheckConstraint("size_bytes > 0", name="assinado_size_bytes_positivo"),
        # Validação externa é tudo-ou-nada: quem validou, quando e por qual
        # método andam juntos. Meia evidência não é evidência.
        CheckConstraint(
            "("
            "validated_by_user_id IS NULL AND validated_at IS NULL AND "
            "validation_method IS NULL"
            ") OR ("
            "validated_by_user_id IS NOT NULL AND validated_at IS NOT NULL AND "
            "validation_method IS NOT NULL"
            ")",
            name="validacao_externa_coerente",
        ),
        # Não existe "validado externamente" sem quem validou.
        #
        # M25.29H — `entregue` saiu desta exigência. Ela fazia sentido quando
        # o único caminho até a entrega passava por uma conferência humana:
        # entregar sem validador seria entregar sem etapa. Com o aceite
        # automático o caminho normal não tem validador NENHUM, por desenho,
        # e manter `entregue` aqui impediria de entregar exatamente os
        # documentos que o sistema aprovou sozinho. O que a constraint
        # protege continua protegido: `validado_externamente` só existe com
        # quem o afirmou.
        CheckConstraint(
            "status <> 'validado_externamente' OR "
            "validated_by_user_id IS NOT NULL",
            name="status_validado_exige_validador",
        ),
    )


# ------------------------------------------------------------ M25.2 — laudo
# próprio da SoproLife: ativo manuscrito e adendos.


class PhysicianSignatureAsset(Base):
    """Imagem de assinatura manuscrita autorizada de um perfil médico.

    O arquivo vive SOMENTE na raiz privada de laudos
    (``M15_REPORTS_STORAGE_DIR``), fora do Git. Esta tabela guarda apenas
    referência técnica: caminho relativo interno, hash e dimensões. A imagem
    nunca é servida como recurso próprio, nunca é devolvida por API, nunca
    entra em JavaScript, em URL pública, em log, em fixture ou em teste —
    ela só é desenhada dentro do PDF do laudo já liberado.

    Uma imagem de assinatura NÃO é assinatura digital qualificada. Este
    registro não altera esse fato em nenhum ponto do sistema.
    """

    __tablename__ = "physician_signature_assets"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    physician_profile_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("physician_profiles.id"),
        nullable=False,
        index=True,
    )
    storage_path: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(40), nullable=False)
    image_width: Mapped[int] = mapped_column(Integer, nullable=False)
    image_height: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="size_bytes_positivo"),
        CheckConstraint("image_width > 0", name="image_width_positivo"),
        CheckConstraint("image_height > 0", name="image_height_positivo"),
        CheckConstraint("mime_type = 'image/png'", name="mime_type_valido"),
        CheckConstraint(
            "(active = true AND revoked_at IS NULL AND revoked_by_user_id IS NULL) "
            "OR (active = false AND revoked_at IS NOT NULL AND "
            "revoked_by_user_id IS NOT NULL)",
            name="revocation_coherent",
        ),
    )


Index(
    "uq_signature_assets_one_active_per_profile",
    PhysicianSignatureAsset.physician_profile_id,
    unique=True,
    sqlite_where=PhysicianSignatureAsset.active.is_(True),
    postgresql_where=PhysicianSignatureAsset.active.is_(True),
)


class ReportAddendum(Base):
    """Adendo append-only a um laudo já liberado.

    Correção posterior NUNCA reescreve o laudo anterior: ou nasce um
    documento corretivo separado, ou entra um adendo numerado que gera uma
    NOVA versão de PDF preservando integralmente a versão liberada.
    """

    __tablename__ = "report_addenda"
    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    report_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_documents.id"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    physician_profile_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("physician_profiles.id"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False
    )
    report_document_version_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_document_versions.id"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    __table_args__ = (
        UniqueConstraint(
            "report_document_id", "sequence", name="uq_report_addendum_sequence"
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "length(trim(body_text)) >= 3", name="body_text_present"
        ),
    )


# ------------------------------------------------- M25.7 — VIDaaS/IntegraICP
#
# Assinatura QUALIFICADA ICP-Brasil pelo certificado em nuvem da médica.
# Esta tabela guarda o CICLO DE VIDA da solicitação — nunca a chave privada,
# nunca o certificado da médica, nunca o PDF e nunca dado de paciente.
#
# Segredos transitórios (o code_verifier do PKCE) ficam CIFRADOS na coluna
# dedicada; state e nonce só existem como hash, para comparação de uso único.

# Estados do fluxo. A ordem aqui é a ordem em que acontecem; os quatro
# últimos são terminais ou quase — "falha_recuperavel" é o único estado de
# erro do qual a médica pode tentar de novo.
QSR_RASCUNHO = "rascunho"
QSR_AGUARDANDO_AUTENTICACAO = "aguardando_autenticacao"
QSR_AGUARDANDO_AUTORIZACAO = "aguardando_autorizacao"
QSR_ASSINATURA_RECEBIDA = "assinatura_recebida"
QSR_VALIDANDO = "validando"
QSR_ASSINADO_LIBERADO = "assinado_liberado"
QSR_RECUSADO = "recusado"
QSR_EXPIRADO = "expirado"
QSR_FALHA_RECUPERAVEL = "falha_recuperavel"
QSR_FALHA_DEFINITIVA = "falha_definitiva"

QUALIFIED_SIGNATURE_STATUSES = (
    QSR_RASCUNHO,
    QSR_AGUARDANDO_AUTENTICACAO,
    QSR_AGUARDANDO_AUTORIZACAO,
    QSR_ASSINATURA_RECEBIDA,
    QSR_VALIDANDO,
    QSR_ASSINADO_LIBERADO,
    QSR_RECUSADO,
    QSR_EXPIRADO,
    QSR_FALHA_RECUPERAVEL,
    QSR_FALHA_DEFINITIVA,
)

# Estados em que uma NOVA solicitação para o mesmo laudo é proibida: já
# existe uma viva. É isto que impede solicitação duplicada quando a médica
# atualiza a página ou clica duas vezes.
QUALIFIED_SIGNATURE_ACTIVE_STATUSES = (
    QSR_RASCUNHO,
    QSR_AGUARDANDO_AUTENTICACAO,
    QSR_AGUARDANDO_AUTORIZACAO,
    QSR_ASSINATURA_RECEBIDA,
    QSR_VALIDANDO,
)


class QualifiedSignatureRequest(Base, TimestampMixin):
    """Uma tentativa de assinatura qualificada de UMA versão do laudo.

    Guarda os DOIS hashes separadamente, de propósito:
    `signed_digest_sha256` é o que foi enviado à AC (o conteúdo coberto pelo
    ByteRange) e `final_sha256` é o arquivo depois da injeção do CMS. Os dois
    nunca coincidem, e tratá-los como um só é o erro clássico deste fluxo.
    """

    __tablename__ = "qualified_signature_requests"

    id: Mapped[str] = mapped_column(String(UUID_LEN), primary_key=True, default=new_uuid)
    report_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("report_documents.id"), nullable=False, index=True
    )
    # Versão PREPARADA (com o campo de assinatura vazio) que originou o
    # digest. Amarra a solicitação a bytes exatos: se a médica reeditar o
    # laudo, a versão muda e a solicitação antiga deixa de servir.
    report_document_version_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("report_document_versions.id"), nullable=False
    )
    physician_profile_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("physician_profiles.id"), nullable=False
    )
    requested_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default=QSR_RASCUNHO, nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)

    # --- amarração do retorno (uso único) -------------------------------
    # Só o hash: quem tiver acesso de leitura ao banco não consegue forjar
    # um callback, porque não recupera o valor original a partir do hash.
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # code_verifier do PKCE, CIFRADO. É segredo de curta duração e some
    # assim que a solicitação chega a um estado terminal.
    pkce_verifier_encrypted: Mapped[str | None] = mapped_column(Text)
    callback_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # --- os dois hashes -------------------------------------------------
    prepared_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    final_sha256: Mapped[str | None] = mapped_column(String(64))

    # --- evidência devolvida pela AC ------------------------------------
    external_reference: Mapped[str | None] = mapped_column(String(120))
    # Identificador da credencial guardado como HASH: serve para conferir
    # que o callback seguinte é da mesma credencial, sem reter o valor.
    credential_id_hash: Mapped[str | None] = mapped_column(String(64))
    signer_subject: Mapped[str | None] = mapped_column(String(300))
    signer_issuer: Mapped[str | None] = mapped_column(String(300))
    signer_serial: Mapped[str | None] = mapped_column(String(80))
    certificate_not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    certificate_not_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Nível PAdES REALMENTE obtido e validado. Nunca preenchido com o nível
    # desejado — só com o que a validação provou.
    pades_level: Mapped[str | None] = mapped_column(String(20))

    # --- janelas de validade --------------------------------------------
    clearance_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    credential_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- diagnóstico ----------------------------------------------------
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(300))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"status IN {QUALIFIED_SIGNATURE_STATUSES!r}",
            name="qualified_signature_status_valido",
        ),
        CheckConstraint("attempts >= 0", name="qualified_signature_attempts"),
        # Um laudo assinado e liberado só pode ter UMA solicitação vencedora.
        Index(
            "uq_qualified_signature_liberado",
            "report_document_id",
            unique=True,
            sqlite_where=text("status = 'assinado_liberado'"),
            postgresql_where=text("status = 'assinado_liberado'"),
        ),
    )


# ------------------------------------------- M26.4 — portal de resultados
#
# Estado de ENTREGA, deliberadamente separado do estado CLÍNICO.
#
# `ExternalSignedDocument.status` responde "o documento assinado voltou e
# passou nas guardas?". Ele não deve responder também "o paciente já abriu?",
# porque as duas perguntas mudam por motivos diferentes e em ritmos
# diferentes: revogar um link não desfaz uma assinatura, e receber um PDF
# novo não apaga o fato de o paciente ter aberto o anterior. Misturá-las
# criaria o clássico estado que ninguém consegue explicar seis meses depois.

RESULTADO_DISPONIVEL = "disponivel"
RESULTADO_ENVIADO = "enviado"
RESULTADO_ACESSADO = "acessado"
RESULTADO_REVOGADO = "revogado"
RESULTADO_STATUS_VALUES = (
    RESULTADO_DISPONIVEL,
    RESULTADO_ENVIADO,
    RESULTADO_ACESSADO,
    RESULTADO_REVOGADO,
)

# Quantas tentativas de data de nascimento cabem antes do resfriamento, e por
# quanto tempo o acesso fica em cooldown. Ficam aqui, e não só no serviço,
# porque a coluna `locked_until` é o que faz o bloqueio sobreviver a um
# restart do processo público.
RESULTADO_MAX_TENTATIVAS = 5
RESULTADO_COOLDOWN_MINUTOS = 15
# Prazo do acesso externo. Resultado de exame é consultado depois — 90 dias é
# o meio-termo entre "o paciente perdeu o laudo na véspera da consulta" e
# "um link de documento médico vivo para sempre". Renovável pelo admin.
RESULTADO_VALIDADE_DIAS = 90


class PatientResultAccess(Base):
    """O acesso de UM paciente a UM resultado — a ponte entre laudo e portal.

    O segredo do link NÃO mora aqui. A coluna guarda apenas
    `sha256(token)`: o processo público compara hashes e nunca tem como
    reconstruir um link a partir do banco. O painel privado consegue
    re-exibir o mesmo link porque o token é DERIVADO
    (`HMAC(chave_privada, id || geração)`) — a chave vive só no
    EnvironmentFile do serviço interno, nunca no do serviço público.
    Regenerar é incrementar `generation`: o hash muda, e o link antigo morre
    no mesmo instante, sem apagar linha nenhuma.
    """

    __tablename__ = "patient_result_accesses"
    id: Mapped[str] = mapped_column(
        String(UUID_LEN), primary_key=True, default=new_uuid
    )
    person_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("people.id"), nullable=False, index=True
    )
    spirometry_exam_id: Mapped[str] = mapped_column(
        String(UUID_LEN), ForeignKey("spirometry_exams.id"), nullable=False
    )
    # Um laudo, um acesso. A unicidade é o que impede dois links vivos para o
    # mesmo documento — dois links seriam duas revogações a lembrar.
    report_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_documents.id"),
        nullable=False,
        unique=True,
    )
    # A VERSÃO ASSINADA VIGENTE. É daqui que o download sai, e não de
    # `current_version_id` nem de `source_version_id`. Um PDF assinado novo
    # que substitua o anterior REAPONTA estas duas colunas.
    signed_document_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("external_signed_documents.id"),
        nullable=False,
    )
    report_document_version_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("report_document_versions.id"),
        nullable=False,
    )
    token_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=RESULTADO_DISPONIVEL, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Timestamps OBJETIVOS. Nenhum deles afirma que alguém LEU o laudo:
    # dizem que foi disponibilizado, que o operador abriu o envio, que a
    # página foi autenticada e que um PDF saiu.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_access_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_access_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_download_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    download_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_motivo: Mapped[str | None] = mapped_column(String(120))
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    # Nulo quando o acesso nasceu do gatilho automático — que é o caminho
    # normal. Preenchido quando um administrador gerou o acesso à mão para
    # um laudo histórico.
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LEN), ForeignKey("users.id")
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    __table_args__ = (
        CheckConstraint(
            f"status IN {RESULTADO_STATUS_VALUES!r}",
            name="resultado_status_valido",
        ),
        CheckConstraint("generation >= 1", name="resultado_generation_positiva"),
        CheckConstraint("failed_attempts >= 0", name="resultado_tentativas"),
        CheckConstraint("download_count >= 0", name="resultado_downloads"),
        # Revogado é tudo-ou-nada: quando e por quê andam juntos. Meia
        # evidência de revogação não explica nada meses depois.
        CheckConstraint(
            "(status <> 'revogado' AND revoked_at IS NULL) OR "
            "(status = 'revogado' AND revoked_at IS NOT NULL "
            "AND revoked_motivo IS NOT NULL)",
            name="resultado_revogacao_coerente",
        ),
    )


class PatientResultSession(Base):
    """Sessão curta do paciente, criada só depois dos DOIS fatores.

    Guarda o hash do segredo, nunca o segredo. Não guarda IP, user-agent nem
    nada que identifique o aparelho: o portal não precisa disso para
    funcionar, e o que não é guardado não vaza.
    """

    __tablename__ = "patient_result_sessions"
    id: Mapped[str] = mapped_column(
        String(UUID_LEN), primary_key=True, default=new_uuid
    )
    access_id: Mapped[str] = mapped_column(
        String(UUID_LEN),
        ForeignKey("patient_result_accesses.id"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
