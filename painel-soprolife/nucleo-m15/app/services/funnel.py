"""Prospective patient funnel. IDs only, explicit transitions, no legacy inference."""
from datetime import datetime, timezone, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, event
from sqlalchemy.orm import Session
from ..models import Lead, SpirometryExam, FunnelEvent, FunnelDeployment, utcnow

TZ = ZoneInfo('America/Sao_Paulo')
ORIGINS = {
 'google_pesquisa':'Google Pesquisa', 'google_maps':'Google Maps / Perfil da Empresa',
 'site':'Site SoproLife', 'instagram':'Instagram', 'facebook':'Facebook',
 'indicacao_medica':'Indicação médica', 'indicacao_paciente':'Indicação de paciente/amigo',
 'parceiro':'Clínica/parceiro', 'pastore':'Pastore', 'empresa':'Empresa/PCMSO',
 'direto':'Já conhecia / busca direta', 'outro':'Outro', 'nao_informado':'Não informado'}
CHANNELS = {'whatsapp':'WhatsApp','telefone':'Telefone','formulario_site':'Formulário do site',
 'presencial':'Presencial','clinica_parceira':'Clínica parceira','instagram_direct':'Instagram/Direct',
 'outro':'Outro','nao_informado':'Não informado'}
# PartnerUnit is only for partner institutions; no SoproLife location entity exists.
UNITS = {'barra':'Barra/Downtown','ipanema':'Ipanema/Pastore','domiciliar':'Domiciliar',
 'zona_norte':'Zona Norte','outra':'Outra','nao_definido':'Não definido'}
SERVICES = {'espirometria':'Espirometria','consulta':'Consulta','ambos':'Ambos','outro':'Outro'}
STATES = ('NOVO_CONTATO','PRIMEIRA_RESPOSTA','AGENDADO','REMARCADO','CANCELADO',
 'NAO_COMPARECEU','EXAME_REALIZADO','AGUARDANDO_RETORNO','PERDIDO')
REASONS = {'desistencia':'Paciente desistiu','horario':'Conflito de horário','preco':'Preço',
 'distancia':'Distância/local','orientacao_medica':'Orientação médica','outro_local':'Conseguiu em outro local',
 'sem_retorno':'Sem retorno','outro':'Outro','nao_informado':'Não informado'}
CLOSED = ('CANCELADO','NAO_COMPARECEU','EXAME_REALIZADO','PERDIDO')
PERFORMED = ('Realizado','Exame realizado','Laudo Liberado','Liberado')


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def fail(message, status=422):
    raise HTTPException(status_code=status, detail=message)


@event.listens_for(Session, 'before_flush')
def immutable_events(db, *_):
    if any(isinstance(x, FunnelEvent) for x in (*db.dirty, *db.deleted)):
        raise ValueError('funnel_events are append-only')


def record(db, lead, kind, user_id=None, exam=None, previous=None, reason=None, at=None):
    db.add(FunnelEvent(lead_id=lead.id, event_type=kind, user_id=user_id,
        related_exam_id=exam.id if exam else None, previous_date=previous,
        scheduled_date=exam.data_exame if exam else lead.exame_previsto_em,
        reason_code=reason, event_at=at or utcnow(), dimensions={k:getattr(lead,k) for k in (
            "origem_aquisicao","canal_entrada","unidade_pretendida","servico_interesse","partner_id","partner_unit_id")}))


def lead_for_update(db, lead_id):
    lead=db.scalar(select(Lead).where(Lead.id==lead_id).with_for_update())
    if not lead or not lead.primeiro_contato_em:
        fail('Contato rastreado não encontrado.',404)
    return lead


def candidates(db, person_id):
    return db.scalars(select(Lead).where(Lead.person_id==person_id,
        Lead.primeiro_contato_em.is_not(None), Lead.status_funil.not_in(CLOSED))
        .order_by(Lead.primeiro_contato_em.desc()).with_for_update()).all()


def attach_exam(db, exam, lead_id=None, skip=False, user_id=None):
    if exam.lead_id:
        if lead_id and lead_id != exam.lead_id:
            fail('Exame já vinculado a outro contato.',409)
        return lead_for_update(db, exam.lead_id)
    if skip:
        return None
    if lead_id:
        lead=lead_for_update(db, lead_id)
    else:
        options=candidates(db, exam.person_id)
        if len(options)>1:
            raise HTTPException(status_code=409, detail={'codigo':'escolher_contato',
                'mensagem':'Há mais de um contato aberto. Escolha o contato ou continue sem rastreamento.',
                'contatos':[{'id':l.id,'public_code':l.public_code} for l in options]})
        lead=options[0] if options else None
    if lead:
        if lead.person_id != exam.person_id:
            fail('Contato e exame devem pertencer à mesma pessoa.')
        if exam.data_exame and exam.data_exame < aware(lead.primeiro_contato_em).astimezone(TZ).date():
            fail('Exame anterior ao contato não pode receber atribuição prospectiva.')
        exam.lead_id=lead.id
        record(db,lead,'EXAME_VINCULADO',user_id,exam)
    return lead


def sync_exam(db, exam, user_id=None, previous_date=None, previous_status=None):
    if not exam.lead_id:
        return
    # Lock opportunity before flushing an exam update (same lock order as scheduling).
    with db.no_autoflush:
        lead=lead_for_update(db,exam.lead_id)
    if exam.person_id != lead.person_id:
        fail('Contato e exame devem pertencer à mesma pessoa.')
    if exam.data_exame and exam.data_exame < aware(lead.primeiro_contato_em).astimezone(TZ).date():
        fail('Exame anterior ao contato não pode receber atribuição prospectiva.')
    previous_date=previous_date or getattr(exam,'_funnel_previous_date',None)
    previous_status=previous_status or getattr(exam,'_funnel_previous_status',None)
    now=utcnow()
    if previous_date and previous_date != exam.data_exame and lead.agendado_em:
        record(db,lead,'REMARCADO',user_id,exam,previous=previous_date)
        lead.exame_previsto_em=exam.data_exame
        if lead.status_funil != 'EXAME_REALIZADO':
            lead.status_funil='REMARCADO'
    if exam.status in ('Agendado','Aguardando','Remarcado'):
        if not lead.agendado_em and exam.data_exame:
            lead.agendado_em=now
            lead.exame_previsto_em=exam.data_exame
            lead.status_funil='AGENDADO'
            lead.etapa='agendado'
            record(db,lead,'AGENDADO',user_id,exam)
    elif exam.status in PERFORMED:
        if not lead.realizado_em:
            lead.realizado_em=now
            lead.status_funil='EXAME_REALIZADO'
            lead.etapa='convertido'
            record(db,lead,'EXAME_REALIZADO',user_id,exam)
    elif exam.status in ('Cancelado','Não compareceu') and exam.status != previous_status:
        state='CANCELADO' if exam.status=='Cancelado' else 'NAO_COMPARECEU'
        lead.status_funil=state
        record(db,lead,state,user_id,exam)


def percentage(n,d):
    return round(100*n/d,2) if d else None


def summarize(rows):
    n=len(rows)
    responded=sum(bool(r['primeira_resposta_em']) for r in rows)
    scheduled=sum(bool(r['agendado_em']) for r in rows)
    done=sum(bool(r['realizado_em']) for r in rows)
    scheduled_done=sum(bool(r['agendado_em'] and r['realizado_em']) for r in rows)
    out={'contatos':n,'respondidos':responded,'agendados':scheduled,'realizados':done,
        'cancelados':sum(r['status_funil']=='CANCELADO' for r in rows),
        'nao_compareceu':sum(r['status_funil']=='NAO_COMPARECEU' for r in rows),
        'perdidos':sum(r['status_funil']=='PERDIDO' for r in rows),
        'aguardando_retorno':sum(r['status_funil']=='AGUARDANDO_RETORNO' for r in rows),
        'conversoes':{'contato_resposta':percentage(responded,n),'contato_agendamento':percentage(scheduled,n),
        'agendamento_exame':percentage(scheduled_done,scheduled),'contato_exame':percentage(done,n)}}
    out['tempos']={}
    for key,a,b in [('contato_resposta','primeiro_contato_em','primeira_resposta_em'),
        ('contato_agendamento','primeiro_contato_em','agendado_em'),
        ('agendamento_exame','agendado_em','realizado_em')]:
        values=[(aware(r[b])-aware(r[a])).total_seconds()/60 for r in rows
            if r[a] and r[b] and aware(r[b])>=aware(r[a])]
        out['tempos'][key]={'mediana_minutos':round(median(values),2) if values else None,
            'disponiveis':len(values),'total':scheduled if key=='agendamento_exame' else n}
    return out


def metrics(db, start, end, unit=None, origin=None, channel=None, service=None, now=None):
    now=aware(now or utcnow())
    if start>end or (end-start).days>730:
        fail('Período inválido (máximo 731 dias).')
    fields=('primeiro_contato_em','primeira_resposta_em','agendado_em','realizado_em',
        'status_funil','origem_aquisicao','canal_entrada','unidade_pretendida','servico_interesse')
    stmt=select(*(getattr(Lead,k) for k in fields)).where(Lead.primeiro_contato_em.is_not(None))
    for name,v in [('unidade_pretendida',unit),('origem_aquisicao',origin),('canal_entrada',channel),('servico_interesse',service)]:
        if v: stmt=stmt.where(getattr(Lead,name)==v)
    all_rows=[dict(r) for r in db.execute(stmt).mappings()
        if start<=aware(r['primeiro_contato_em']).astimezone(TZ).date()<=end
        and aware(r['primeiro_contato_em'])<=now]
    result=summarize(all_rows)
    contact_ids=select(Lead.id).where(Lead.primeiro_contato_em.is_not(None))
    for name,v in [('unidade_pretendida',unit),('origem_aquisicao',origin),('canal_entrada',channel),('servico_interesse',service)]:
        if v: contact_ids=contact_ids.where(getattr(Lead,name)==v)
    start_at=datetime.combine(start,datetime.min.time(),TZ)
    end_at=datetime.combine(end+timedelta(days=1),datetime.min.time(),TZ)
    contact_ids=contact_ids.where(Lead.primeiro_contato_em>=start_at,Lead.primeiro_contato_em<end_at)
    from sqlalchemy import func
    result['remarcacoes']=db.scalar(select(func.count()).select_from(FunnelEvent).where(
        FunnelEvent.event_type=='REMARCADO',FunnelEvent.lead_id.in_(contact_ids)))
    def groups(field,labels):
        return [{'chave':k,'rotulo':label,**summarize([r for r in all_rows if (r[field] or 'nao_informado')==k])}
            for k,label in labels.items()]
    result['por_origem']=groups('origem_aquisicao',ORIGINS)
    result['por_unidade']=groups('unidade_pretendida',UNITS)
    weekly=[]
    day=start-timedelta(days=start.weekday())
    today=now.astimezone(TZ).date()
    while day<=min(end,today):
        last=day+timedelta(days=6)
        week_rows=[r for r in all_rows if day<=aware(r['primeiro_contato_em']).astimezone(TZ).date()<=last]
        weekly.append({'inicio':day.isoformat(),'fim':last.isoformat(),
            'parcial':last>=today or start>day or end<last,
            **summarize(week_rows),
            'origens':{k:sum(r['origem_aquisicao']==k for r in week_rows) for k in ORIGINS},
            'unidades':{k:sum(r['unidade_pretendida']==k for r in week_rows) for k in UNITS}})
        day+=timedelta(days=7)
    result['semanas']=weekly
    # Coverage is a separate production-date cohort, never a conversion denominator.
    ex_stmt=select(SpirometryExam.lead_id,SpirometryExam.status).where(
        SpirometryExam.data_exame.between(start,min(end,today)))
    if unit or origin or channel or service:
        ex_stmt=ex_stmt.join(Lead,SpirometryExam.lead_id==Lead.id)
        for name,v in [('unidade_pretendida',unit),('origem_aquisicao',origin),('canal_entrada',channel),('servico_interesse',service)]:
            if v: ex_stmt=ex_stmt.where(getattr(Lead,name)==v)
    exams=db.execute(ex_stmt).all()
    performed=[r for r in exams if r.status in PERFORMED]
    reserved=[r for r in exams if r.status in ('Aguardando','Agendado','Remarcado')]
    origin_count=sum(bool(r['origem_aquisicao'] and r['origem_aquisicao']!='nao_informado') for r in all_rows)
    result['cobertura']={k:{'com_dado':a,'total':b,'percentual':percentage(a,b)} for k,a,b in [
        ('exames_rastreados',sum(bool(e.lead_id) for e in performed),len(performed)),
        ('origem_registrada',origin_count,len(all_rows)),
        ('agendamentos_vinculados',sum(bool(e.lead_id) for e in reserved),len(reserved))]}
    deployment=db.get(FunnelDeployment,'patient_funnel')
    result.update({'implantado_em':deployment.deployed_at.isoformat() if deployment else None,
        'observado_em':now.isoformat(),'coorte':'Primeiro contato no período; etapas acumuladas até agora. Sem janela fixa de maturação.',
        'cobertura_escopo':'Produção por data do exame. Filtros de aquisição/unidade selecionam apenas exames com contato; sem filtro inclui histórico não rastreado.',
        'tempos_escopo':'Minutos até registro da etapa; realização usa confirmação operacional, não horário clínico.'})
    return result


def reuse_scheduled(db, new_exam, lead_id=None, skip=False):
    """An attendance completes its existing reservation instead of duplicating it."""
    if skip:
        return new_exam
    if lead_id:
        lead=lead_for_update(db,lead_id)
        options=[lead]
    else:
        options=candidates(db,new_exam.person_id)
    if len(options)!=1 or options[0].person_id!=new_exam.person_id:
        return new_exam  # attach_exam validates ambiguity/identity before commit
    pending=db.scalars(select(SpirometryExam).where(SpirometryExam.lead_id==options[0].id,
        SpirometryExam.status.in_(('Aguardando','Agendado','Remarcado'))).with_for_update()).all()
    if len(pending)>1:
        fail('Há mais de um exame agendado; escolha o exame existente na operação.',409)
    if not pending:
        return new_exam
    exam=pending[0]
    exam._funnel_previous_date=exam.data_exame
    exam._funnel_previous_status=exam.status
    for name in ('data_exame','data_exame_original','data_exame_precisao','data_exame_dia_assumido',
        'modalidade','local_atendimento','municipio_atendimento_ibge','partner_id','partner_unit_id',
        'status','broncodilatador','origem','responsavel','observacao','equipamento_fonte',
        'idempotency_key','idempotency_fingerprint'):
        setattr(exam,name,getattr(new_exam,name))
    return exam
