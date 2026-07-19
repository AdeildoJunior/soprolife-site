"""Schemas Pydantic de entrada — validação fail-closed (extra='forbid').

PCMSO saiu da operação ativa: nenhuma modalidade, funil ou origem PCMSO
existe na M15. Dados históricos permanecem apenas nas fontes legadas.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .normalize import contains_clinical_info, contains_pii_like

# Dinheiro: Decimal com 2 casas (quantização ROUND_HALF_UP na camada de
# serviço); percentual: Decimal 0..100. Nunca float.
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
MoneyOrZero = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]

Modalidade = Literal["residencial", "cowork", "clinica_parceira", "indefinida"]
ModalidadeConsulta = Literal["teleconsulta", "residencial", "cowork", "clinica_parceira"]
ServicoInteresse = Literal["espirometria", "consulta", "ambos", "outro"]
EtapaLead = Literal[
    "novo", "em_contato", "agendado", "convertido", "perdido",
    "nao_respondeu", "aguardando_retomada",
]
StatusExame = Literal["Aguardando", "Realizado", "Cancelado", "Remarcado"]
StatusConsulta = Literal["Agendada", "Realizada", "Cancelada", "Remarcada", "Não compareceu"]
StatusEncaminhamento = Literal[
    "Recebido da clínica", "Aguardando contato", "Contato realizado", "Agendado",
    "Realizado", "Laudo enviado", "Cancelado", "Não compareceu",
    "Aguardando pagamento", "Concluído",
]
StatusPagamento = Literal["Recebido", "Pendente", "Parcial", "Cortesia", "Cancelado"]
FormaPagamento = Literal["Pix", "Dinheiro", "Cartão", "Outro"]
OrigemPreco = Literal["Tabela", "Promoção", "Parceria", "Negociação", "Cortesia"]
TipoRepasse = Literal["percentual", "fixo", "nenhum"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TokenRequest(StrictModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


# ------------------------------------------------------ administração (M15.3A)

PapelUsuario = Literal["admin", "gestor", "operacional", "leitura"]

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

# Senha nunca trafega em URL nem aparece em log/auditoria — só em corpo POST.
SENHA_MIN = 10


class AdminUserCreate(StrictModel):
    email: str = Field(min_length=5, max_length=200, pattern=_EMAIL_RE)
    nome: str = Field(min_length=2, max_length=200)
    papel: PapelUsuario = "leitura"
    senha: str = Field(min_length=SENHA_MIN, max_length=200)

    @field_validator("email")
    @classmethod
    def _email_lower(cls, v: str) -> str:
        return v.lower()


class AdminUserUpdate(StrictModel):
    """Papel e estado — troca de senha tem endpoint próprio."""

    papel: PapelUsuario | None = None
    ativo: bool | None = None


class AdminPasswordReset(StrictModel):
    senha: str = Field(min_length=SENHA_MIN, max_length=200)


class ContactIn(StrictModel):
    tipo: Literal["whatsapp", "telefone", "email", "outro"]
    valor: str = Field(min_length=1, max_length=200)
    principal: bool = False


class PersonCreate(StrictModel):
    nome_completo: str = Field(min_length=2, max_length=300)
    data_nascimento: date | None = None
    observacao: str | None = Field(default=None, max_length=4000)
    contatos: list[ContactIn] = []
    consentimento_whatsapp: Literal["concedido", "revogado", "desconhecido"] | None = None


class PersonUpdate(StrictModel):
    nome_completo: str | None = Field(default=None, min_length=2, max_length=300)
    data_nascimento: date | None = None
    status: Literal["ativo", "inativo"] | None = None
    nao_contatar: bool | None = None
    observacao: str | None = Field(default=None, max_length=4000)


class ConsentIn(StrictModel):
    canal: Literal["whatsapp", "telefone", "email"]
    status: Literal["concedido", "revogado", "desconhecido"]
    origem: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=2000)


class LeadCreate(StrictModel):
    person_id: str
    origem: str | None = Field(default=None, max_length=120)
    canal_entrada: str | None = Field(default=None, max_length=60)
    servico_interesse: ServicoInteresse | None = None
    modalidade: Modalidade | None = None
    etapa: EtapaLead = "novo"
    data_primeiro_contato: str | None = Field(default=None, max_length=40)
    data_retomada_manual: date | None = None
    responsavel: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=4000)


class LeadUpdate(StrictModel):
    etapa: EtapaLead | None = None
    modalidade: Modalidade | None = None
    data_retomada_manual: date | None = None
    responsavel: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=4000)


class ExamCreate(StrictModel):
    person_id: str
    data_exame: str | None = Field(default=None, max_length=40)
    modalidade: Literal["residencial", "cowork", "clinica_parceira"] | None = None
    local_atendimento: str | None = Field(default=None, max_length=200)
    partner_id: str | None = None
    partner_unit_id: str | None = None
    status: StatusExame = "Aguardando"
    origem: str | None = Field(default=None, max_length=120)
    responsavel: str | None = Field(default=None, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=64)
    observacao: str | None = Field(default=None, max_length=4000)


class ExamUpdate(StrictModel):
    status: StatusExame | None = None
    data_exame: str | None = Field(default=None, max_length=40)
    responsavel: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=4000)


class ConsultationCreate(StrictModel):
    person_id: str
    data_consulta: str | None = Field(default=None, max_length=40)
    modalidade: ModalidadeConsulta | None = None
    profissional: str | None = Field(default=None, max_length=200)
    status: StatusConsulta = "Agendada"
    origem: str | None = Field(default=None, max_length=120)
    responsavel: str | None = Field(default=None, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=64)
    observacao: str | None = Field(default=None, max_length=4000)


class ConsultationUpdate(StrictModel):
    status: StatusConsulta | None = None
    data_consulta: str | None = Field(default=None, max_length=40)
    profissional: str | None = Field(default=None, max_length=200)
    observacao: str | None = Field(default=None, max_length=4000)


class PartnerCreate(StrictModel):
    nome: str = Field(min_length=2, max_length=200)
    tipo: Literal["clinica", "consultorio", "outro"] = "clinica"
    status: Literal["prospecto", "em_negociacao", "ativa", "pausada", "encerrada"] = "prospecto"
    cidade: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=4000)


class PartnerUpdate(StrictModel):
    nome: str | None = Field(default=None, min_length=2, max_length=200)
    tipo: Literal["clinica", "consultorio", "outro"] | None = None
    status: Literal["prospecto", "em_negociacao", "ativa", "pausada", "encerrada"] | None = None
    cidade: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=4000)


class PartnerUnitCreate(StrictModel):
    partner_id: str
    nome: str = Field(min_length=1, max_length=200)
    bairro: str | None = Field(default=None, max_length=120)
    cidade: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=2000)


class PartnerUnitUpdate(StrictModel):
    nome: str | None = Field(default=None, min_length=1, max_length=200)
    bairro: str | None = Field(default=None, max_length=120)
    cidade: str | None = Field(default=None, max_length=120)
    ativo: bool | None = None
    observacao: str | None = Field(default=None, max_length=2000)


class PartnerContactCreate(StrictModel):
    partner_id: str
    partner_unit_id: str | None = None
    nome: str = Field(min_length=2, max_length=200)
    cargo: str | None = Field(default=None, max_length=120)
    telefone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    principal: bool = False
    observacao: str | None = Field(default=None, max_length=2000)


class PartnerContactUpdate(StrictModel):
    partner_unit_id: str | None = None
    nome: str | None = Field(default=None, min_length=2, max_length=200)
    cargo: str | None = Field(default=None, max_length=120)
    telefone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    principal: bool | None = None
    ativo: bool | None = None
    observacao: str | None = Field(default=None, max_length=2000)


class PartnershipCreate(StrictModel):
    partner_id: str
    status: Literal["em_negociacao", "ativa", "pausada", "encerrada"] = "em_negociacao"
    data_inicio: str | None = Field(default=None, max_length=40)
    modelo_repasse: Literal["percentual", "fixo", "nenhum", "indefinido"] = "indefinido"
    percentual_repasse: Percent | None = None
    valor_repasse_fixo: MoneyOrZero | None = None
    responsavel_soprolife: str | None = Field(default=None, max_length=120)
    responsavel_followup: Literal["soprolife", "parceiro"] = "soprolife"
    observacao: str | None = Field(default=None, max_length=4000)


class PartnershipUpdate(StrictModel):
    """Atualização de parceria — papel gestor/admin (repasses são sensíveis)."""

    status: Literal["em_negociacao", "ativa", "pausada", "encerrada"] | None = None
    data_inicio: str | None = Field(default=None, max_length=40)
    modelo_repasse: Literal["percentual", "fixo", "nenhum", "indefinido"] | None = None
    percentual_repasse: Percent | None = None
    valor_repasse_fixo: MoneyOrZero | None = None
    responsavel_soprolife: str | None = Field(default=None, max_length=120)
    responsavel_followup: Literal["soprolife", "parceiro"] | None = None
    observacao: str | None = Field(default=None, max_length=4000)


class ReferralCreate(StrictModel):
    """Criação operacional do encaminhamento — SEM campos financeiros.

    Valores, repasses e recebimentos entram por ReferralFinanceUpdate,
    protegido pelo papel gestor.
    """

    person_id: str
    partner_id: str
    partner_unit_id: str | None = None
    partner_contact_id: str | None = None
    data_encaminhamento: str | None = Field(default=None, max_length=40)
    servico_solicitado: Literal["espirometria", "consulta", "ambos", "outro"] | None = None
    data_agendada: date | None = None
    status: StatusEncaminhamento = "Recebido da clínica"
    responsavel_soprolife: str | None = Field(default=None, max_length=120)
    observacao_operacional: str | None = Field(default=None, max_length=4000)
    autorizacao_contato_soprolife: bool | None = None
    responsavel_followup: Literal["soprolife", "parceiro"] = "soprolife"


class ReferralUpdate(StrictModel):
    """Atualização operacional — SEM campos financeiros."""

    status: StatusEncaminhamento | None = None
    partner_unit_id: str | None = None
    partner_contact_id: str | None = None
    data_agendada: date | None = None
    data_realizacao: date | None = None
    spirometry_exam_id: str | None = None
    consultation_id: str | None = None
    laudo_enviado: bool | None = None
    data_envio_laudo: date | None = None
    responsavel_soprolife: str | None = Field(default=None, max_length=120)
    observacao_operacional: str | None = Field(default=None, max_length=4000)
    autorizacao_contato_soprolife: bool | None = None
    responsavel_followup: Literal["soprolife", "parceiro"] | None = None
    proximo_followup: date | None = None


class ReferralFinanceUpdate(StrictModel):
    """Campos financeiros do encaminhamento — exclusivo de gestor/admin."""

    financial_entry_id: str | None = None
    valor_cobrado: MoneyOrZero | None = None
    valor_recebido: MoneyOrZero | None = None
    tipo_repasse: TipoRepasse | None = None
    valor_repasse: MoneyOrZero | None = None
    percentual_repasse: Percent | None = None
    status_repasse: Literal["previsto", "aguardando", "pago", "cancelado"] | None = None


class InteractionCreate(StrictModel):
    person_id: str
    canal: Literal["whatsapp", "telefone", "email", "presencial", "outro"]
    direcao: Literal["enviado", "recebido"] = "enviado"
    resumo: str | None = Field(default=None, max_length=4000)
    resultado: str | None = Field(default=None, max_length=60)
    followup_id: str | None = None


class FollowupCreate(StrictModel):
    person_id: str
    tipo: Literal["manual", "lead_sem_atendimento"] = "manual"
    due_date: date | None = None
    responsavel: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=2000)


class FollowupComplete(StrictModel):
    resultado: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=2000)


class FollowupRetry(StrictModel):
    nova_data: date
    observacao: str | None = Field(default=None, max_length=2000)


class WhatsAppConfirm(StrictModel):
    """Registro de interação APÓS confirmação humana do envio manual."""

    resumo: str | None = Field(default=None, max_length=2000)
    resultado: Literal["enviado", "sem_resposta", "respondeu", "numero_invalido"] = "enviado"


_FINANCE_PII_MSG = (
    "Payload financeiro não pode conter nome, telefone, CPF, e-mail ou dado clínico."
)


def _reject_finance_pii(v: str | None) -> str | None:
    if v and (contains_pii_like(v) or contains_clinical_info(v)):
        raise ValueError(_FINANCE_PII_MSG)
    return v


class FinancialEntryCreate(StrictModel):
    """Financeiro é separado dos dados pessoais: só IDs técnicos.

    extra='forbid' já rejeita chaves como nome/telefone/cpf; os validadores
    abaixo bloqueiam PII e informação clínica em texto livre (fail-closed,
    deliberadamente conservador: qualquer coisa parecida com identificação
    pessoal é recusada).
    """

    tipo: Literal["receita", "despesa", "repasse"]
    categoria: str | None = Field(default=None, max_length=60)
    descricao: str | None = Field(default=None, max_length=300)
    valor: Money
    moeda: Literal["BRL"] = "BRL"
    data_competencia: str | None = Field(default=None, max_length=40)
    data_recebimento: date | None = None
    status: StatusPagamento = "Pendente"
    forma_pagamento: FormaPagamento | None = None
    origem_preco: OrigemPreco | None = None
    spirometry_exam_id: str | None = None
    consultation_id: str | None = None
    partner_referral_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=64)

    _no_pii = field_validator("categoria", "descricao")(_reject_finance_pii)


class FinancialEntryUpdate(StrictModel):
    """Atualização do lançamento — gestor/admin.

    `valor` e `tipo` são imutáveis de propósito: correção de valor é um novo
    lançamento (trilha íntegra), nunca uma edição silenciosa.
    """

    categoria: str | None = Field(default=None, max_length=60)
    descricao: str | None = Field(default=None, max_length=300)
    status: StatusPagamento | None = None
    data_recebimento: date | None = None
    forma_pagamento: FormaPagamento | None = None
    origem_preco: OrigemPreco | None = None

    _no_pii = field_validator("categoria", "descricao")(_reject_finance_pii)


class TransferCreate(StrictModel):
    partner_id: str
    partnership_id: str | None = None
    partner_referral_id: str | None = None
    settlement_id: str | None = None
    financial_entry_id: str | None = None
    valor: Money
    status: Literal["previsto", "aguardando", "pago", "cancelado"] = "previsto"
    data_prevista: date | None = None
    data_pagamento: date | None = None
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=64)


class PersonSearch(StrictModel):
    """Busca de pessoas por corpo POST — nome nunca vai para query string/logs."""

    q: str | None = Field(default=None, max_length=200)
    status: Literal["ativo", "inativo"] | None = None
    nao_contatar: bool | None = None
    pagina: int = Field(default=1, ge=1)
    tamanho: int = Field(default=25, ge=1, le=100)


class MigrationApproval(StrictModel):
    """Aprovação humana de execução de snapshot — todos os identificadores
    exatos e digitados; nada é assumido nem aprovado automaticamente."""

    sha256: str = Field(min_length=64, max_length=64,
                        pattern=r"^[0-9a-f]{64}$")
    mapping_version: str = Field(min_length=1, max_length=40)
    dry_run_batch_id: str = Field(min_length=36, max_length=36)
    observacao: str | None = Field(default=None, max_length=2000)


class IdentityDecision(StrictModel):
    """Decisão humana sobre candidato de identidade — nunca funde registros."""

    decisao: Literal["pessoas_diferentes", "possivel_mesma_pessoa", "adiar"]
    observacao: str | None = Field(default=None, max_length=2000)
