"""Read-only command projections. No notification facts, finance or clinical writes.

The sources remain the existing queues, profit projection, funnel and ledger.
Only compact presentation records cross this boundary; no clinical text or CPF.
"""
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import exists, func, select

from ..config import get_settings
from ..models import (EegExam, ExamPartnerShare,
    FinancialEntry, FiscalDocument, Followup, Lead, Partner,
    PartnerSettlement, Person, ReportDocument, SpirometryExam)
from ..security import (ADMINISTRATIVE_ROLES, ROLE_ADMIN, ROLE_GESTOR, ROLE_LEITURA,
    ROLE_MEDICO, ROLE_OPERACIONAL, can_access_eeg, user_effective_roles)
from . import funnel, followup, partner_shares, profit_projection

CATEGORIES = {
    'sem_laudo': 'Exame realizado sem laudo', 'laudo': 'Laudo pendente',
    'fiscal': 'NFS-e pendente', 'contato': 'Contato aguardando ação',
    'followup': 'Follow-up vencido ou para hoje', 'agenda': 'Agendamento próximo',
    'acerto': 'Acerto de parceiro pendente', 'inconsistencia': 'Informação operacional ausente',
}
SCHEDULED = ('Aguardando', 'Agendado', 'Remarcado')


def capabilities(user, brand):
    roles = user_effective_roles(user)
    if brand == 'eeglife':
        if not can_access_eeg(user):
            raise HTTPException(403, 'Contexto não autorizado.')
        return {'operacao': bool(roles & {ROLE_ADMIN, 'eeg_operador'}),
                'financeiro': bool(roles & {ROLE_ADMIN, 'eeg_financeiro'}),
                'fiscal': ROLE_ADMIN in roles, 'clinico': 'medical_eeg' in roles}
    if brand != 'soprolife' or not roles.intersection({*ADMINISTRATIVE_ROLES, ROLE_MEDICO}):
        raise HTTPException(403, 'Contexto não autorizado.')
    return {'operacao': ROLE_OPERACIONAL in roles, 'leitura': ROLE_LEITURA in roles,
            'financeiro': ROLE_GESTOR in roles, 'fiscal': ROLE_GESTOR in roles,
            'clinico': ROLE_MEDICO in roles}


def today():
    return followup.today_local()


def _target(kind, record_id=None, code=None, **extra):
    return {'tipo': kind, 'id': record_id, 'codigo': code, **extra}


def _item(category, kind, record_id, code, title, when=None, **extra):
    return {'categoria': category, 'codigo': code, 'titulo': title,
            'data': when.isoformat() if when else None,
            'destino': _target(kind, record_id, code, **extra)}


def _exam_base():
    return select(SpirometryExam, Person).join(Person, Person.id == SpirometryExam.person_id).where(
        Person.arquivado.is_not(True))


def _exact_date():
    return (SpirometryExam.data_exame_precisao == 'dia',
            SpirometryExam.data_exame_dia_assumido.is_(False))


def _today_exams(when):
    return _exam_base().where(SpirometryExam.data_exame == when, *_exact_date(),
        SpirometryExam.status.not_in(('Cancelado', 'Não Compareceu')))


def _day_bounds(when):
    tz = ZoneInfo(get_settings().display_timezone)
    return datetime.combine(when, time.min, tz), datetime.combine(when + timedelta(days=1), time.min, tz)


def _fiscal_items(db, brand):
    # Queue snapshots only. production_queue() also expires requests and MUST
    # NOT be used here: opening the home is never a fiscal state transition.
    stmt = select(FiscalDocument).where(FiscalDocument.environment == 'production',
        FiscalDocument.state.not_in(('issued', 'cancelled', 'simulated')))
    stmt = stmt.where(FiscalDocument.kind == 'eeg' if brand == 'eeglife' else FiscalDocument.kind != 'eeg')
    return [_item('fiscal', 'fiscal', d.id, 'NFS-e',
                  'Documento fiscal · ' + d.state, d.created_at)
            for d in db.scalars(stmt)]


def _report_items(db):
    from ..routers import reports
    # Shared selection and state resolver: corrections, closures and signed
    # documents have exactly the same semantics as the existing delivery queue.
    rows = db.execute(reports.delivery_queue_statement()).all()
    document_ids = [d.id for d, *_ in rows]
    signed = {}
    if document_ids:
        for s in db.scalars(reports.current_signed_query(document_ids)):
            signed.setdefault(s.report_document_id, s)
    items = []
    for doc, exam, person, assignment, _successor in rows:
        state = reports._estado_de_entrega(doc, signed.get(doc.id))
        if state == reports.FILA_ENTREGUE:
            continue
        items.append(_item('laudo', 'laudo', doc.id, doc.public_code,
            reports.FILA_ROTULOS[state], exam.data_exame, exame=exam.public_code))
    return items


def _sopro_items(db, cap, when, include_upcoming=False):
    items, unavailable = [], []
    # Operational report queues require operacional in the existing API.
    if cap['operacao']:
        performed = _exam_base().where(SpirometryExam.status.in_(funnel.PERFORMED),
            SpirometryExam.encerramento_motivo.is_(None),
            ~exists().where(ReportDocument.spirometry_exam_id == SpirometryExam.id))
        for exam, person in db.execute(performed):
            items.append(_item('sem_laudo', 'exame', exam.id, exam.public_code,
                'Anexar exame e encaminhar para laudo', exam.data_exame, pessoa=person.public_code))
        if get_settings().reports_enabled and get_settings().reports_mode == 'pilot':
            items.extend(_report_items(db))
        else:
            unavailable.append('laudo')
    if cap.get('leitura'):
        upcoming = _exam_base().where(SpirometryExam.status.in_(SCHEDULED), *_exact_date(),
            SpirometryExam.data_exame.between(when, when + timedelta(days=7)))
        for exam, person in db.execute(upcoming):
            items.append(_item('agenda', 'agenda', exam.id, exam.public_code,
                person.nome_completo, exam.data_exame, pessoa=person.public_code))
        # Only explicit open funnel states, never inferred elapsed response time.
        leads = select(Lead, Person).join(Person, Person.id == Lead.person_id).where(
            Person.arquivado.is_not(True), Person.nao_contatar.is_(False),
            Lead.primeiro_contato_em.is_not(None),
            Lead.status_funil.in_(('NOVO_CONTATO', 'PRIMEIRA_RESPOSTA', 'AGUARDANDO_RETORNO')))
        for lead, person in db.execute(leads):
            items.append(_item('contato', 'contato', lead.id, lead.public_code,
                person.nome_completo, lead.data_retomada_manual, pessoa=person.public_code))
        for fup, person in db.execute(select(Followup, Person).join(Person, Person.id == Followup.person_id).where(
                Person.arquivado.is_not(True), Followup.status == 'pendente',
                Followup.due_date <= when + timedelta(days=7 if include_upcoming else 0),
                Followup.controlado_por_parceiro.is_(False))):
            from ..routers.followups import _contact_person
            try:
                contact = _contact_person(db, fup, person)
            except HTTPException:
                if fup.due_date > when:
                    continue
                items.append(_item('inconsistencia', 'pessoa', person.id, person.public_code,
                    'Vínculo de contato precisa de conferência', pessoa=person.public_code))
                continue
            if contact.nao_contatar or person.nao_contatar or (fup.due_date <= when and
                    followup.classify_queue(fup, person, when) not in (followup.FILA_ATRASADO, followup.FILA_HOJE)):
                continue
            items.append(_item('followup', 'pessoa', person.id, fup.public_code,
                person.nome_completo, fup.due_date, pessoa=person.public_code, followup_id=fup.id))
        for exam, person in db.execute(_exam_base().where(
                SpirometryExam.status.in_(funnel.PERFORMED), SpirometryExam.data_exame.is_(None),
                SpirometryExam.encerramento_motivo.is_(None))):
            items.append(_item('inconsistencia', 'exame', exam.id, exam.public_code,
                'Exame realizado sem data registrada', pessoa=person.public_code))
    if cap['fiscal']:
        if get_settings().nfse_enabled:
            items.extend(_fiscal_items(db, 'soprolife'))
        else:
            unavailable.append('fiscal')
    if cap['financeiro']:
        for share in db.scalars(select(ExamPartnerShare)):
            pos = partner_shares.position(db, share)
            if pos['status_acerto'] in ('pendente', 'parcial'):
                items.append(_item('acerto', 'acerto', share.id, pos['exame'], pos['parceiro']))
        for s, partner in db.execute(select(PartnerSettlement, Partner).join(Partner,
                Partner.id == PartnerSettlement.partner_id).where(
                PartnerSettlement.status.in_(('incluido','enviado','a_receber')))):
            items.append(_item('acerto', 'pastore', s.id, 'Fechamento', partner.nome,
                s.competencia))
    return items, unavailable


def _eeg_scope(user):
    from ..routers.eeg import visible_exam_query
    return visible_exam_query(user)


def _eeg_items(db, cap, when, user):
    roles = user_effective_roles(user)
    items = []
    if roles.intersection({ROLE_ADMIN, 'eeg_operador', 'eeg_tecnico', 'medical_eeg'}):
        for exam in db.scalars(_eeg_scope(user)):
            if exam.status in ('agendado', 'confirmado') and exam.agendado_para:
                scheduled = funnel.aware(exam.agendado_para).astimezone(funnel.TZ)
                if when <= scheduled.date() <= when + timedelta(days=7):
                    items.append(_item('agenda', 'eeg_exame', exam.id, exam.public_code,
                        'Atendimento EEG agendado', scheduled))
            if exam.status in ('realizado', 'aguardando_envio_laudo', 'enviado_para_laudo', 'em_laudo'):
                items.append(_item('laudo', 'eeg_exame', exam.id, exam.public_code,
                    'Fluxo de laudo EEG · ' + exam.status.replace('_', ' '), exam.realizado_em))
    if cap['fiscal'] and get_settings().nfse_enabled:
        items.extend(_fiscal_items(db, 'eeglife'))
    return items, []


def _window(item, when):
    # Dates on reports/fiscal/acertos describe the record, not a due date.
    # Only explicit agenda / follow-up / manual contact dates schedule action.
    category = item['categoria']
    due = item['data'][:10] if item['data'] else None
    if category in ('agenda', 'followup', 'contato') and due:
        if due == when.isoformat():
            return 'hoje'
        if when.isoformat() < due <= (when + timedelta(days=7)).isoformat():
            return 'proximos'
        if due > when.isoformat():
            return None
    return 'atencao' if category != 'agenda' else None


def pending(db, user, brand, category=None, page=1, limit=20, window=None):
    cap, when = capabilities(user, brand), today()
    if brand == 'soprolife' and not cap.get('leitura'):
        raise HTTPException(403, 'Use a fila clínica atribuída.')
    items, unavailable = (_sopro_items(db, cap, when, include_upcoming=True) if brand == 'soprolife'
                          else _eeg_items(db, cap, when, user))
    groups = {key: dict(Counter(i['categoria'] for i in items if _window(i, when) == key))
              for key in ('hoje', 'atencao', 'proximos')}
    # Upcoming follow-ups are navigation context, never added to global Pendências.
    canonical = [i for i in items if not (i['categoria'] == 'followup' and i['data'][:10] > when.isoformat())]
    counts = Counter(i['categoria'] for i in canonical)
    if window:
        if window not in groups:
            raise HTTPException(422, 'Período inválido.')
        items = [i for i in items if _window(i, when) == window]
    else:
        items = canonical
    if category == 'hoje':
        if brand == 'soprolife':
            items = [_item('hoje', 'exame', e.id, e.public_code, p.nome_completo, e.data_exame)
                for e, p in db.execute(_today_exams(when))]
        else:
            lo, hi = _day_bounds(when)
            items = [_item('hoje', 'eeg_exame', e.id, e.public_code, 'Atendimento EEG', e.agendado_para)
                for e in db.scalars(_eeg_scope(user).where(EegExam.agendado_para >= lo, EegExam.agendado_para < hi, EegExam.status != 'cancelado'))]
    elif category:
        if category not in CATEGORIES:
            raise HTTPException(422, 'Categoria inválida.')
        items = [i for i in items if i['categoria'] == category]
    items.sort(key=lambda i: (i['data'] or '9999', i['categoria'], i['destino']['id']))
    start = (page - 1) * limit
    return {'total': len(items), 'categorias': dict(counts), 'indisponiveis': unavailable,
            'itens': items[start:start + limit], 'pagina': page,
            'tem_proxima': start + limit < len(items), 'completo': not unavailable,
            'grupos': groups}


def _sum_known(rows, field):
    values = [r.get(field) for r in rows]
    known = [v for v in values if v is not None]
    if values and not known:
        return None
    return f"{sum((Decimal(v) for v in known), Decimal(0)):.2f}"


def _finance(db, brand, start, end):
    stmt = select(func.coalesce(func.sum(FinancialEntry.valor), 0)).where(
        FinancialEntry.tipo == 'despesa', FinancialEntry.status != 'Cancelado',
        FinancialEntry.data_competencia.between(start, end))
    stmt = stmt.where(FinancialEntry.eeg_exam_id.is_not(None) if brand == 'eeglife'
                      else FinancialEntry.eeg_exam_id.is_(None))
    expenses = f'{Decimal(db.scalar(stmt)):.2f}'
    if brand == 'eeglife':
        income = db.scalar(select(func.coalesce(func.sum(FinancialEntry.valor), 0)).where(
            FinancialEntry.eeg_exam_id.is_not(None), FinancialEntry.tipo == 'receita',
            FinancialEntry.status != 'Cancelado', FinancialEntry.data_competencia.between(start, end)))
        return {'bruto': f'{Decimal(income):.2f}', 'soprolife': None, 'parceiros': None,
                'despesas': None, 'resultado': None, 'cobertura': None,
                'nota': 'Receitas do razão EEG. Despesas por marca e resultado analítico não consolidados nesta fonte; consulte Custos EEGLife.'}
    projection = profit_projection.build_projection(db, start, end)
    rows = projection['exames']
    # Consume the canonical recognized revenue AND its existing partner cost.
    # Net historical bases have zero additional partner cost; never deduct twice.
    economic = []
    for row in rows:
        revenue, partner_cost = row.get('receita'), row.get('custos', {}).get('parceiro')
        own = (Decimal(revenue) - Decimal(partner_cost)) if revenue is not None and partner_cost is not None else None
        economic.append({'bruto': row.get('bruto'), 'soprolife': str(own) if own is not None else None,
            'parceiros': str(Decimal(row['bruto']) - own) if row.get('bruto') is not None and own is not None else None})
    kpis = projection['kpis']
    return {**{k: _sum_known(economic, k) for k in ('bruto', 'soprolife', 'parceiros')},
            'despesas': expenses,
            'resultado': kpis['lucro'],
            'parcial': {**{k: any(r[k] is None for r in economic) for k in ('bruto','soprolife','parceiros')}, 'resultado': bool(kpis['incompletos'])},
            'cobertura_valores': {k: {'com_dado': sum(r[k] is not None for r in economic), 'total': len(economic)} for k in ('bruto','soprolife','parceiros')},
            'cobertura': {'completos': kpis['exames'], 'total': kpis['total_exames']},
            'nota': 'Competência dos exames remuneráveis; resultado usa somente custos confirmados na projeção existente. Despesas vêm do razão.'}


def summary(db, user, brand, operational=False):
    cap, when = capabilities(user, brand), today()
    if brand == 'soprolife' and not cap.get('leitura'):
        raise HTTPException(403, 'Use a fila clínica atribuída.')
    start = when.replace(day=1)
    p = pending(db, user, brand, limit=6)
    if operational:
        if brand == 'soprolife':
            daily = db.scalar(select(func.count()).select_from(_today_exams(when).subquery()))
        elif user_effective_roles(user) & {ROLE_ADMIN, 'eeg_operador', 'eeg_tecnico', 'medical_eeg'}:
            lo, hi = _day_bounds(when)
            daily = db.scalar(select(func.count()).select_from(_eeg_scope(user).where(
                EegExam.agendado_para >= lo, EegExam.agendado_para < hi,
                EegExam.status != 'cancelado').subquery()))
        else:
            daily = None
        month = None
    elif brand == 'soprolife':
        base = _exam_base()
        daily = db.scalar(select(func.count()).select_from(_today_exams(when).subquery()))
        monthly = db.scalar(select(func.count()).select_from(base.where(
            SpirometryExam.data_exame.between(start, when),
            SpirometryExam.data_exame_precisao.in_(('dia', 'mes')),
            SpirometryExam.status.in_(funnel.PERFORMED)).subquery()))
        cohort = funnel.metrics(db, start, when)
        month = {'exames': monthly, 'contatos': cohort['contatos'],
                 'agendados': cohort['agendados'], 'realizados': cohort['realizados'],
                 'coorte': cohort['coorte']}
    else:
        roles = user_effective_roles(user)
        daily = monthly = None
        if roles & {ROLE_ADMIN, 'eeg_operador', 'eeg_tecnico', 'medical_eeg'}:
            scope = _eeg_scope(user)
            tz = ZoneInfo(get_settings().display_timezone)
            lo, hi = datetime.combine(when, time.min, tz), datetime.combine(when + timedelta(days=1), time.min, tz)
            daily = db.scalar(select(func.count()).select_from(scope.where(
                EegExam.agendado_para >= lo, EegExam.agendado_para < hi,
                EegExam.status != 'cancelado').subquery()))
            monthly = db.scalar(select(func.count()).select_from(scope.where(
                EegExam.realizado_em >= datetime.combine(start, time.min, tz),
                EegExam.realizado_em < hi, EegExam.status != 'cancelado').subquery()))
        month = {'exames': monthly}
    result = {'marca': brand, 'estado': 'ready', 'capacidades': cap,
              'observado_em': datetime.now(timezone.utc).isoformat(),
              'periodo': {'hoje': when.isoformat(), 'inicio': start.isoformat()},
              'hoje': {'exames': daily}, 'mes': month, 'pendencias': p,
              'categorias': CATEGORIES,
              'escopo_fiscal': 'Documentos de produção já preparados; elegibilidade de novos exames permanece no módulo Fiscal.'}
    if cap['financeiro'] and not operational:
        result['financeiro'] = _finance(db, brand, start, when)
    return result
