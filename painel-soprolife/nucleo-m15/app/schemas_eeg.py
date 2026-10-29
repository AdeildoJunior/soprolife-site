"""Contratos EEG independentes do domínio respiratório."""
from datetime import date, datetime
from typing import Literal
from pydantic import Field, model_validator
from .schemas import StrictModel, PatientRegistrationUpdate, BrazilUF

LEAD_STATES = ('lead','interessado','aguardando_contato','solicitado','pre_agendado','confirmado','realizado','faltou','cancelado','reagendado','convertido')
TASK_CATEGORIES = ('confirmar_agenda','enviar_preparo','cobrar_tracado','atribuir_laudador','acompanhar_assinatura','liberar_resultado','cobranca','retorno_comercial')
DOC_CATEGORIES = ('contratos','equipamento','anvisa','calibracao','treinamento','neurologistas','parceiros','protocolos','fiscal','institucional')
PARTNER_CATEGORIES = ('clinica','hospital','neurologista','telelaudo','pediatra','psiquiatra','home_care','unidade')


class EegRegistration(PatientRegistrationUpdate):
    consentimento_whatsapp: Literal['concedido','revogado','desconhecido'] | None = None


class RelationIn(StrictModel):
    proximo_contato: date | None = None
    situacao: Literal['ativo','aguardando_contato','acompanhamento','encerrado'] = 'ativo'


class ActivityIn(StrictModel):
    tipo: Literal['lead','tarefa']
    titulo: str = Field(min_length=2, max_length=200)
    categoria: str = Field(min_length=2, max_length=40)
    status: str = Field(min_length=2, max_length=30)
    origem: Literal['direto','site','whatsapp','indicacao','parceiro','outro'] | None = None
    contato: str | None = Field(default=None, max_length=80)
    consentimento: Literal['concedido','revogado','desconhecido'] = 'desconhecido'
    enrollment_id: str | None = Field(default=None, max_length=36)
    exam_id: str | None = Field(default=None, max_length=36)
    responsavel_id: str | None = Field(default=None, max_length=36)
    prazo: datetime | None = None

    @model_validator(mode='after')
    def categories(self):
        if self.tipo == 'lead':
            if self.status not in LEAD_STATES or self.categoria != 'oportunidade': raise ValueError('Etapa de lead inválida.')
        elif self.status not in ('pendente','em_andamento','concluida','cancelada') or self.categoria not in TASK_CATEGORIES:
            raise ValueError('Tarefa inválida.')
        if self.prazo and not self.prazo.tzinfo: raise ValueError('Prazo exige fuso horário.')
        return self


class ConvertIn(StrictModel):
    enrollment_id: str = Field(min_length=36, max_length=36)
    exam_id: str | None = Field(default=None, max_length=36)


class ProfessionalIn(StrictModel):
    user_id: str | None = Field(default=None, max_length=36)
    nome: str = Field(min_length=2, max_length=220)
    crm: str = Field(pattern=r'^\d{1,12}$')
    uf: BrazilUF
    rqe: str | None = Field(default=None, max_length=30)
    servico: str | None = Field(default=None, max_length=200)
    tipo: Literal['interno','parceiro','telelaudo']
    referencia_verificacao: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{3,63}$')


class AssignIn(StrictModel):
    professional_id: str = Field(min_length=36, max_length=36)


class ReportContent(StrictModel):
    indicacao: str | None = Field(default=None, max_length=4000)
    tecnica: str = Field(min_length=2, max_length=8000)
    descricao: str = Field(min_length=2, max_length=20000)
    conclusao: str = Field(min_length=2, max_length=8000)
    observacoes: str | None = Field(default=None, max_length=4000)


class CaseAction(StrictModel):
    acao: Literal['conferir','concluir','publicar','entregar']
    confirmacao_codigo: str = Field(min_length=1, max_length=12)


class PartnerIn(StrictModel):
    partner_id: str | None = Field(default=None, max_length=36)
    nome: str | None = Field(default=None, min_length=2, max_length=200)
    categoria: Literal['clinica','hospital','neurologista','telelaudo','pediatra','psiquiatra','home_care','unidade']
    unidade: str | None = Field(default=None, max_length=200)


class PreparationIn(StrictModel):
    modalidade_codigo: str = Field(max_length=40)
    texto: str = Field(min_length=2, max_length=8000)
    referencia_aprovacao: str = Field(min_length=4, max_length=120)


class CrmSearch(StrictModel):
    q: str | None = Field(default=None, max_length=200)
    situacao: Literal['ativo','aguardando_contato','acompanhamento','encerrado'] | None = None
    pagina: int = Field(default=1,ge=1)
    tamanho: int = Field(default=25,ge=1,le=100)
