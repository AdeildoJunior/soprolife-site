"""Patient funnel on existing leads/people/exams; never stores messages."""
from datetime import date, datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Lead, Person, Partner, PartnerUnit, SpirometryExam, FunnelEvent, User, utcnow
from ..schemas import StrictModel, PersonCreate
from ..security import require_role, ROLE_LEITURA, ROLE_OPERACIONAL, ROLE_GESTOR
from ..ids import allocate_public_code
from ..audit import audit
from ..services import funnel as f
from ..services.idempotency import idempotent_create
from ..services.person_registration import build_person
from .attendances import _candidatos_de_identidade
from .people import recusar_cpf_ja_cadastrado, registrar_identificacao_oficial

router=APIRouter(prefix='/funil',tags=['funil'])

class Dimensions(StrictModel):
    origem_aquisicao: str = 'nao_informado'
    canal_entrada: str = 'nao_informado'
    unidade_pretendida: str = 'nao_definido'
    servico_interesse: str = 'espirometria'
    partner_id: str | None = None
    partner_unit_id: str | None = None

    @model_validator(mode='after')
    def controlled(self):
        for name,values in [('origem_aquisicao',f.ORIGINS),('canal_entrada',f.CHANNELS),('unidade_pretendida',f.UNITS),('servico_interesse',f.SERVICES)]:
            if getattr(self,name) not in values: raise ValueError('Categoria inválida: '+name)
        return self

class ContactCreate(Dimensions):
    person_id: str | None = None
    pessoa: PersonCreate | None = None
    confirmar_duplicado: bool = False
    idempotency_key: str = Field(min_length=4,max_length=64)
    primeiro_contato_em: datetime | None = None

    @model_validator(mode='after')
    def identity(self):
        if bool(self.person_id)==bool(self.pessoa): raise ValueError('Escolha pessoa existente ou nova.')
        if self.primeiro_contato_em and self.primeiro_contato_em.tzinfo is None: raise ValueError('Timestamp exige fuso horário.')
        return self

class Transition(StrictModel):
    evento: Literal['PRIMEIRA_RESPOSTA','AGENDADO','REMARCADO','CANCELADO','NAO_COMPARECEU','AGUARDANDO_RETORNO','PERDIDO']
    data_prevista: date | None = None
    motivo: str | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.motivo and self.motivo not in f.REASONS: raise ValueError('Motivo inválido.')
        if self.evento in ('AGENDADO','REMARCADO') and not self.data_prevista: raise ValueError('Informe a data prevista.')
        return self

class Link(StrictModel):
    exam_id: str


def serialize(lead):
    return {k:(f.aware(v).isoformat() if isinstance(v,datetime) else (v.isoformat() if isinstance(v,date) else v))
        for k in ('id','public_code','person_id','origem_aquisicao','canal_entrada','unidade_pretendida','servico_interesse','status_funil','primeiro_contato_em','primeira_resposta_em','agendado_em','exame_previsto_em','realizado_em','partner_id','partner_unit_id','motivo_encerramento') if (v:=getattr(lead,k)) is not None}


def check_dimensions(db,payload):
    if payload.partner_id:
        p=db.get(Partner,payload.partner_id)
        if not p or p.arquivado: f.fail('Parceiro indisponível.')
    if payload.partner_unit_id:
        u=db.get(PartnerUnit,payload.partner_unit_id)
        if not u or not u.ativo or u.partner_id!=payload.partner_id: f.fail('Unidade precisa pertencer ao parceiro escolhido.')


@router.get('/config')
def config(db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_LEITURA))):
    units=db.scalars(select(PartnerUnit).where(PartnerUnit.ativo.is_(True))).all()
    return {'origens':f.ORIGINS,'canais':f.CHANNELS,'unidades':f.UNITS,'servicos':f.SERVICES,'estados':f.STATES,'motivos':f.REASONS,
        'unidades_parceiras':[{'id':u.id,'partner_id':u.partner_id,'nome':u.nome} for u in units]}


@router.post('/contatos',status_code=201)
def create(payload:ContactCreate,request:Request,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_OPERACIONAL))):
    check_dimensions(db,payload)
    now=utcnow(); first=payload.primeiro_contato_em or now
    if first>now: f.fail('Primeiro contato não pode estar no futuro.')
    if payload.primeiro_contato_em and first<now-timedelta(minutes=5):
        from ..security import user_effective_roles
        if ROLE_GESTOR not in user_effective_roles(user): f.fail('Correção de horário exige gestor.',403)
    def factory(key,fingerprint):
        if payload.person_id:
            person=db.get(Person,payload.person_id)
            if not person or person.arquivado_em: f.fail('Pessoa indisponível.',404)
        else:
            p=payload.pessoa
            recusar_cpf_ja_cadastrado(db,p.cpf)
            options=_candidatos_de_identidade(db,p)
            if options and not payload.confirmar_duplicado:
                raise HTTPException(status_code=409,detail={'codigo':'possivel_duplicado','mensagem':'Selecione o paciente existente ou confirme a criação.','candidatos':options})
            person=build_person(db,nome_completo=p.nome_completo,cpf=p.cpf,data_nascimento=p.data_nascimento,sexo=p.sexo,observacao=p.observacao,contatos=p.contatos,consentimento_whatsapp=p.consentimento_whatsapp,registrado_por=user.id)
            registrar_identificacao_oficial(db,request,user,person,p)
        lead=Lead(public_code=allocate_public_code(db,'leads'),person_id=person.id,
            **payload.model_dump(include=set(Dimensions.model_fields)),origem=f.ORIGINS[payload.origem_aquisicao],
            primeiro_contato_em=first,data_primeiro_contato=first.astimezone(f.TZ).date(),data_primeiro_contato_precisao='dia',data_primeiro_contato_dia_assumido=False,
            status_funil='NOVO_CONTATO',etapa='novo',idempotency_key=key,idempotency_fingerprint=fingerprint)
        db.add(lead); db.flush(); f.record(db,lead,'NOVO_CONTATO',user.id,at=first)
        audit(db,'funil.contato_criado','leads',lead.id,user.id,request.state.request_id,{'status':'NOVO_CONTATO'})
        return lead
    lead,replay=idempotent_create(db,Lead,payload.idempotency_key,payload.model_dump(mode='json'),factory)
    db.commit(); return {**serialize(lead),'idempotente':replay}


@router.patch('/contatos/{lead_id}')
def edit(lead_id:str,payload:Dimensions,request:Request,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_OPERACIONAL))):
    lead=f.lead_for_update(db,lead_id)
    merged=Dimensions(**{k:(getattr(payload,k) if k in payload.model_fields_set else getattr(lead,k)) for k in Dimensions.model_fields})
    check_dimensions(db,merged)
    for k in payload.model_fields_set: setattr(lead,k,getattr(payload,k))
    if 'origem_aquisicao' in payload.model_fields_set:
        lead.origem=f.ORIGINS[lead.origem_aquisicao]
    f.record(db,lead,'DIMENSOES_CORRIGIDAS',user.id)
    audit(db,'funil.dimensoes_corrigidas','leads',lead.id,user.id,request.state.request_id,{'campos':list(payload.model_fields_set)})
    db.commit(); return serialize(lead)


@router.get('/contatos')
def contacts(person_id:str|None=None,abertos:bool=False,pagina:int=Query(1,ge=1),db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_LEITURA))):
    stmt=select(Lead).where(Lead.primeiro_contato_em.is_not(None))
    if person_id: stmt=stmt.where(Lead.person_id==person_id)
    if abertos: stmt=stmt.where(Lead.status_funil.not_in(f.CLOSED))
    leads=db.scalars(stmt.order_by(Lead.primeiro_contato_em.desc()).offset((pagina-1)*50).limit(51)).all()
    return {'itens':[{**serialize(l),'nome_completo':db.get(Person,l.person_id).nome_completo,
        'exam_ids':list(db.scalars(select(SpirometryExam.id).where(SpirometryExam.lead_id==l.id)))} for l in leads[:50]],'tem_proxima':len(leads)>50,'pagina':pagina}


@router.get('/contatos/{lead_id}/eventos')
def events(lead_id:str,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_LEITURA))):
    f.lead_for_update(db,lead_id)
    rows=db.scalars(select(FunnelEvent).where(FunnelEvent.lead_id==lead_id).order_by(FunnelEvent.event_at,FunnelEvent.created_at)).all()
    return {'itens':[{'evento':e.event_type,'em':e.event_at.isoformat(),'data_anterior':e.previous_date,'data_prevista':e.scheduled_date,'motivo':e.reason_code,'dimensoes':e.dimensions} for e in rows]}


@router.post('/contatos/{lead_id}/eventos')
def transition(lead_id:str,payload:Transition,request:Request,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_OPERACIONAL))):
    lead=f.lead_for_update(db,lead_id); kind=payload.evento
    if kind=='PRIMEIRA_RESPOSTA':
        if lead.primeira_resposta_em: return serialize(lead)
        if lead.status_funil in f.CLOSED: f.fail('Contato encerrado.',409)
        lead.primeira_resposta_em=utcnow()
        if lead.status_funil=='NOVO_CONTATO': lead.status_funil=kind; lead.etapa='em_contato'
        f.record(db,lead,kind,user.id)
    elif kind in ('AGENDADO','REMARCADO'):
        if lead.servico_interesse not in ('espirometria','ambos'): f.fail('Agenda integrada disponível para espirometria. Use o atendimento de consulta existente.')
        if lead.realizado_em: f.fail('Contato já realizado; reteste não cria novo contato.',409)
        if payload.data_prevista < f.aware(lead.primeiro_contato_em).astimezone(f.TZ).date(): f.fail('Data prevista anterior ao contato.')
        exam=db.scalar(select(SpirometryExam).where(SpirometryExam.lead_id==lead.id).order_by(SpirometryExam.created_at).with_for_update())
        if not exam:
            if kind=='REMARCADO': f.fail('Não há agendamento para remarcar.')
            exam=SpirometryExam(public_code=allocate_public_code(db,'spirometry_exams'),person_id=lead.person_id,lead_id=lead.id,status='Aguardando',partner_id=lead.partner_id,partner_unit_id=lead.partner_unit_id,
                modalidade='clinica_parceira' if lead.partner_unit_id else ('residencial' if lead.unidade_pretendida=='domiciliar' else 'cowork'),local_atendimento=f.UNITS[lead.unidade_pretendida])
            db.add(exam); db.flush(); f.record(db,lead,'EXAME_VINCULADO',user.id,exam)
        previous=exam.data_exame; old_status=exam.status
        if previous==payload.data_prevista and old_status=='Aguardando' and lead.status_funil in ('AGENDADO','REMARCADO'): return serialize(lead)
        exam.data_exame=payload.data_prevista; exam.data_exame_original=payload.data_prevista.isoformat()
        exam.data_exame_precisao='dia'; exam.data_exame_dia_assumido=False; exam.status='Aguardando'
        f.sync_exam(db,exam,user.id,previous,old_status)
        if old_status in ('Cancelado','Não compareceu') or lead.status_funil in ('PERDIDO','AGUARDANDO_RETORNO'):
            lead.status_funil='AGENDADO'; lead.etapa='agendado'
            f.record(db,lead,'AGENDADO',user.id,exam)
    else:
        if lead.realizado_em: f.fail('Exame já realizado; desfecho não pode ser apagado.',409)
        if kind=='NAO_COMPARECEU' and not lead.agendado_em: f.fail('Falta exige agendamento registrado.')
        if lead.status_funil==kind: return serialize(lead)
        lead.status_funil=kind; lead.motivo_encerramento=payload.motivo
        lead.etapa='perdido' if kind=='PERDIDO' else ('aguardando_retomada' if kind=='AGUARDANDO_RETORNO' else lead.etapa)
        f.record(db,lead,kind,user.id,reason=payload.motivo)
        if kind in ('CANCELADO','NAO_COMPARECEU'):
            for exam in db.scalars(select(SpirometryExam).where(SpirometryExam.lead_id==lead.id)):
                if exam.status not in f.PERFORMED: exam.status='Cancelado' if kind=='CANCELADO' else 'Não compareceu'
    audit(db,'funil.transicao','leads',lead.id,user.id,request.state.request_id,{'status':kind})
    db.commit(); return serialize(lead)


@router.post('/contatos/{lead_id}/vincular-exame')
def link(lead_id:str,payload:Link,request:Request,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_OPERACIONAL))):
    exam=db.scalar(select(SpirometryExam).where(SpirometryExam.id==payload.exam_id).with_for_update())
    if not exam: f.fail('Exame não encontrado.',404)
    f.attach_exam(db,exam,lead_id,user_id=user.id); f.sync_exam(db,exam,user.id)
    db.commit(); return {'vinculado':True}


@router.get('/metricas')
def metrics(inicio:date,fim:date,unidade:str|None=None,origem:str|None=None,canal:str|None=None,servico:str|None=None,db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_LEITURA))):
    for value,choices in [(unidade,f.UNITS),(origem,f.ORIGINS),(canal,f.CHANNELS),(servico,f.SERVICES)]:
        if value and value not in choices: f.fail('Filtro inválido.')
    return f.metrics(db,inicio,fim,unidade,origem,canal,servico)
