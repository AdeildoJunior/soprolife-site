"""Schemas Pydantic de entrada — validação fail-closed (extra='forbid').

PCMSO saiu da operação ativa: nenhuma modalidade, funil ou origem PCMSO
existe na M15. Dados históricos permanecem apenas nas fontes legadas.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .finance_categories import CATEGORIA_CONSULTA as _CATEGORIA_CONSULTA
from .finance_categories import CATEGORIA_ESPIROMETRIA as _CATEGORIA_ESPIROMETRIA
from .finance_categories import CATEGORIA_REPASSE_MEDICO as _CATEGORIA_REPASSE_MEDICO
from .finance_categories import canonizar_categoria
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
StatusExame = Literal["Aguardando", "Realizado", "Laudo Liberado", "Cancelado", "Remarcado"]
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
    # M21 — "Manter conectado neste dispositivo". Ausente = sessão que morre
    # com o navegador (padrão conservador).
    manter_conectado: bool = False


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


RelationshipType = Literal[
    "mother", "father", "legal_guardian", "grandparent", "other"
]


class PersonRelationshipCreate(StrictModel):
    guardian_person_id: str = Field(min_length=36, max_length=36)
    relationship_type: RelationshipType
    is_legal_guardian: bool = False
    active: bool = True


class PersonRelationshipDeactivate(StrictModel):
    reason_code: Literal[
        "relationship_ended", "superseded", "administrative_correction"
    ]


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
    broncodilatador: bool | None = None
    origem: str | None = Field(default=None, max_length=120)
    responsavel: str | None = Field(default=None, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=64)
    observacao: str | None = Field(default=None, max_length=4000)


class ExamUpdate(StrictModel):
    status: StatusExame | None = None
    data_exame: str | None = Field(default=None, max_length=40)
    broncodilatador: bool | None = None
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
    # ``person_id`` é o alias legado do paciente. Clientes novos devem enviar
    # ``patient_person_id`` e podem apontar o contato para o responsável.
    person_id: str | None = None
    patient_person_id: str | None = None
    contact_person_id: str | None = None
    tipo: Literal["manual", "lead_sem_atendimento"] = "manual"
    due_date: date | None = None
    responsavel: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_patient(self):
        if self.person_id is None and self.patient_person_id is None:
            raise ValueError("patient_person_id é obrigatório")
        if (
            self.person_id is not None
            and self.patient_person_id is not None
            and self.person_id != self.patient_person_id
        ):
            raise ValueError("person_id e patient_person_id divergem")
        return self

    @property
    def resolved_patient_person_id(self) -> str:
        return self.patient_person_id or self.person_id  # type: ignore[return-value]


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


def _normalize_finance_category(v):
    """Normaliza ANTES dos limites e do detector de PII.

    Assim caracteres invisíveis/NFKC não escondem telefone, CPF ou outro dado
    que o validador precisa enxergar, e o max_length é aplicado à forma que
    realmente seria persistida.
    """
    if v is not None and not isinstance(v, str):
        return v  # o validador de tipo do Pydantic produz o 422 apropriado
    return canonizar_categoria(v)


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

    _categoria_canonica = field_validator("categoria", mode="before")(
        _normalize_finance_category
    )
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

    _categoria_canonica = field_validator("categoria", mode="before")(
        _normalize_finance_category
    )
    _no_pii = field_validator("categoria", "descricao")(_reject_finance_pii)


class ConciliacaoItemCreate(StrictModel):
    """Um item da conciliação de exames extra-Pastore — R$ evidenciado."""

    spirometry_exam_id: str
    valor: Money
    status: StatusPagamento = "Recebido"
    data_recebimento: date | None = None
    forma_pagamento: FormaPagamento | None = None
    origem_preco: OrigemPreco | None = None
    descricao: str | None = Field(default=None, max_length=300)

    _no_pii = field_validator("descricao")(_reject_finance_pii)


# ------------------------------------------------- atendimento único (M20)

TipoAtendimento = Literal[
    "espirometria_soprolife",
    "espirometria_pastore",
    "consulta_soprolife",
    "espirometria_consulta_soprolife",
]

TIPOS_COM_ESPIROMETRIA = (
    "espirometria_soprolife",
    "espirometria_pastore",
    "espirometria_consulta_soprolife",
)
TIPOS_COM_CONSULTA = ("consulta_soprolife", "espirometria_consulta_soprolife")
# Pastore só se aplica quando o tipo é explicitamente Espirometria Pastore.
TIPO_PASTORE = "espirometria_pastore"

# Categorias da receita PRÓPRIA de um exame/consulta (o lançamento que o
# atendimento cria sozinho). Compartilhadas entre attendances.py (quem cria)
# e finance.py (quem bloqueia duplicata manual da mesma receita — M23.1)
# para as duas nunca divergirem por um literal digitado diferente.
#
# M23.1 (correção da revisão crítica): a definição mora em
# app/finance_categories.py junto do contrato de normalização; aqui só
# reexportamos para não quebrar quem já importava de schemas.
CATEGORIA_ESPIROMETRIA = _CATEGORIA_ESPIROMETRIA
CATEGORIA_CONSULTA = _CATEGORIA_CONSULTA
CATEGORIA_REPASSE_MEDICO = _CATEGORIA_REPASSE_MEDICO


class AtendimentoEspirometria(StrictModel):
    data_exame: str = Field(min_length=4, max_length=40)
    status: StatusExame = "Realizado"
    broncodilatador: bool | None = None
    modalidade: Literal["residencial", "cowork", "clinica_parceira"] | None = None
    local_atendimento: str | None = Field(default=None, max_length=200)
    partner_id: str | None = None
    partner_unit_id: str | None = None
    origem: str | None = Field(default=None, max_length=120)
    responsavel: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=4000)
    # Acompanhamento explícito; sem isto vale a regra vigente do exame.
    proximo_followup: date | None = None


class AtendimentoConsulta(StrictModel):
    """Consulta SoproLife. Retorno NUNCA é assumido — precisa ser escolhido."""

    data_consulta: str = Field(min_length=4, max_length=40)
    status: StatusConsulta = "Realizada"
    modalidade: ModalidadeConsulta | None = None
    profissional: str | None = Field(default=None, max_length=200)
    origem: str | None = Field(default=None, max_length=120)
    responsavel: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=4000)
    retorno: Literal["sem_retorno", "data", "intervalo_meses"] = "sem_retorno"
    retorno_data: date | None = None
    retorno_intervalo_meses: int | None = Field(default=None, ge=1, le=60)

    @model_validator(mode="after")
    def _retorno_coerente(self):
        if self.retorno == "data" and self.retorno_data is None:
            raise ValueError("retorno='data' exige retorno_data.")
        if self.retorno == "intervalo_meses" and self.retorno_intervalo_meses is None:
            raise ValueError("retorno='intervalo_meses' exige retorno_intervalo_meses.")
        if self.retorno == "sem_retorno" and (
            self.retorno_data is not None or self.retorno_intervalo_meses is not None
        ):
            raise ValueError("retorno='sem_retorno' não aceita data nem intervalo.")
        return self


class AtendimentoFinanceiroExame(StrictModel):
    """Componente financeiro da espirometria — valor sempre explícito."""

    valor: Money
    status: StatusPagamento = "Recebido"
    data_competencia: str | None = Field(default=None, max_length=40)
    data_recebimento: date | None = None
    forma_pagamento: FormaPagamento | None = None
    origem_preco: OrigemPreco | None = None


class AtendimentoFinanceiroConsulta(StrictModel):
    """Componente financeiro da consulta — receita BRUTA da SoproLife.

    O repasse ao médico é obrigação separada e auditável à parte: nunca
    reduz nem substitui a receita bruta. Um repasse de 100% continua sendo
    repasse — a receita bruta permanece dentro do controle da SoproLife.
    """

    valor_bruto: Money
    status: StatusPagamento = "Recebido"
    data_competencia: str | None = Field(default=None, max_length=40)
    data_recebimento: date | None = None
    forma_pagamento: FormaPagamento | None = None
    origem_preco: OrigemPreco | None = None
    # Regra de repasse: percentual determinístico OU valor explícito.
    repasse_medico_percentual: Percent | None = None
    repasse_medico_valor: Money | None = None
    repasse_medico_status: StatusPagamento = "Pendente"

    @model_validator(mode="after")
    def _repasse_coerente(self):
        if (
            self.repasse_medico_percentual is not None
            and self.repasse_medico_valor is not None
        ):
            raise ValueError(
                "Informe o repasse por percentual OU por valor, nunca os dois."
            )
        if (
            self.repasse_medico_valor is not None
            and self.repasse_medico_valor > self.valor_bruto
        ):
            raise ValueError("Repasse ao médico não pode exceder a receita bruta.")
        return self


class AtendimentoFinanceiro(StrictModel):
    espirometria: AtendimentoFinanceiroExame | None = None
    consulta: AtendimentoFinanceiroConsulta | None = None


class AtendimentoCreate(StrictModel):
    """Fluxo único de novo atendimento (M20).

    Um paciente já selecionado (person_id) + um tipo + os blocos que o tipo
    exige. Nenhum valor monetário é inferido: o financeiro só existe quando
    vem explícito no payload.
    """

    person_id: str
    tipo: TipoAtendimento
    espirometria: AtendimentoEspirometria | None = None
    consulta: AtendimentoConsulta | None = None
    financeiro: AtendimentoFinanceiro | None = None
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=64)

    @model_validator(mode="after")
    def _blocos_coerentes(self):
        precisa_exame = self.tipo in TIPOS_COM_ESPIROMETRIA
        precisa_consulta = self.tipo in TIPOS_COM_CONSULTA
        if precisa_exame and self.espirometria is None:
            raise ValueError(f"O tipo '{self.tipo}' exige o bloco 'espirometria'.")
        if not precisa_exame and self.espirometria is not None:
            raise ValueError(f"O tipo '{self.tipo}' não aceita o bloco 'espirometria'.")
        if precisa_consulta and self.consulta is None:
            raise ValueError(f"O tipo '{self.tipo}' exige o bloco 'consulta'.")
        if not precisa_consulta and self.consulta is not None:
            raise ValueError(f"O tipo '{self.tipo}' não aceita o bloco 'consulta'.")

        exame = self.espirometria
        if self.tipo == TIPO_PASTORE:
            if not (exame and exame.partner_id):
                raise ValueError("Espirometria Pastore exige o parceiro.")
            if not exame.partner_unit_id:
                raise ValueError("Espirometria Pastore exige a unidade operacional.")
            if self.financeiro is not None:
                raise ValueError(
                    "Espirometria Pastore não aceita pagamento direto do paciente."
                )
        elif exame is not None and (exame.partner_id or exame.partner_unit_id):
            # Atendimento SoproLife (inclusive o combinado) NUNCA é Pastore.
            raise ValueError(
                "Atendimento SoproLife não aceita parceiro/unidade — "
                "use o tipo 'espirometria_pastore'."
            )

        fin = self.financeiro
        if fin is not None:
            if fin.espirometria is not None and not precisa_exame:
                raise ValueError("Financeiro de espirometria sem espirometria no tipo.")
            if fin.consulta is not None and not precisa_consulta:
                raise ValueError("Financeiro de consulta sem consulta no tipo.")
        return self


StatusFechamentoPastore = Literal[
    "incluido", "enviado", "a_receber", "recebido", "cancelado"
]


class PastoreSettlementCreate(StrictModel):
    partner_unit_id: str
    competencia: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    observacao: str | None = Field(default=None, max_length=4000)


class PastoreSettlementUpdate(StrictModel):
    # "recebido" só nasce pelo endpoint de recebimento, que cria o recibo.
    status: Literal["incluido", "enviado", "a_receber", "cancelado"] | None = None
    valor_total: Money | None = None
    data_envio: date | None = None
    observacao: str | None = Field(default=None, max_length=4000)


class PastoreSettlementReceive(StrictModel):
    valor_confirmado: Money
    data_recebimento: date
    forma_pagamento: FormaPagamento
    idempotency_key: str = Field(min_length=4, max_length=64)


class ConciliacaoLoteRequest(StrictModel):
    """Envio em lote — só commita se a soma bater com o pendente atual."""

    itens: list[ConciliacaoItemCreate] = Field(min_length=1, max_length=20)
    total_esperado: Money


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


class FinanceSearch(StrictModel):
    """Busca de lançamentos por corpo POST — código ou nome nunca em URL."""

    q: str | None = Field(default=None, max_length=200)
    pagina: int = Field(default=1, ge=1)
    tamanho: int = Field(default=25, ge=1, le=100)


class DuplicateCheck(StrictModel):
    """Pré-checagem de duplicados ANTES de criar a pessoa — somente leitura.

    Recebe nome/telefones via corpo POST (nunca URL) e devolve candidatos;
    a decisão de prosseguir é sempre humana, nunca fusão automática.
    """

    nome_completo: str | None = Field(default=None, min_length=2, max_length=300)
    telefones: list[str] = Field(default_factory=list, max_length=5)


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


class MultiSheetDryRunRequest(StrictModel):
    """Nome simples do envelope já presente no diretório privado aprovado."""

    envelope: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[^/\\\x00]+$",
    )


class MultiSheetReviewDecision(StrictModel):
    """Decisão sanitizada; nunca recebe nem devolve o conteúdo da linha."""

    decisao: Literal[
        "resolvido",
        "excluido",
        "adiado",
        "vincular_candidato",
        "criar_pessoa",
        "create_minor_patient_with_guardian",
        "manter_primeira",
        "manter_segunda",
        "manter_ambas",
    ]
    mapping_version: str = Field(min_length=1, max_length=40)


class IdentityDecision(StrictModel):
    """Decisão humana sobre candidato de identidade — nunca funde registros."""

    decisao: Literal["pessoas_diferentes", "possivel_mesma_pessoa", "adiar"]
    observacao: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------ CRM (M19)

class CrmPacienteBusca(StrictModel):
    """Busca da lista canônica de pacientes — nome sempre por corpo POST."""

    q: str | None = Field(default=None, max_length=200)
    fila: str | None = Field(default=None, max_length=40)
    origem: str | None = Field(default=None, max_length=120)
    status_acompanhamento: str | None = Field(default=None, max_length=40)
    somente_sem_telefone: bool = False
    pagina: int = Field(default=1, ge=1)
    tamanho: int = Field(default=25, ge=1, le=100)


class CrmContatoRegistro(StrictModel):
    """Resultado explícito de uma tentativa de contato (M19 §9).

    Cada envio cria exatamente UM registro auditável de tentativa; o efeito
    no follow-up é derivado do resultado escolhido pelo operador.
    """

    followup_id: str | None = None
    person_id: str | None = None
    resultado: Literal[
        "contato_realizado",
        "nao_respondeu",
        "reagendar",
        "nao_deseja_contato",
        "telefone_invalido",
    ]
    canal: Literal["whatsapp", "telefone", "email", "presencial", "outro"] = "whatsapp"
    nova_data: date | None = None
    observacao: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _coerencia(self):
        if not self.followup_id and not self.person_id:
            raise ValueError("Informe followup_id ou person_id.")
        if self.resultado == "reagendar" and self.nova_data is None:
            raise ValueError("Reagendar exige nova_data.")
        if self.observacao and contains_pii_like(self.observacao):
            raise ValueError(
                "Observação operacional não pode conter telefone, CPF ou e-mail."
            )
        return self
