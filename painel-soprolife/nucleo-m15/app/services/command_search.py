"""Bounded, indexed, per-user search. Terms stay in POST bodies, never logs/audit."""
import re
import threading
import time
from collections import deque

from fastapi import HTTPException
from sqlalchemy import false, func, literal_column, or_, select

from ..models import (EegEnrollment, EegExam, EegReportDocument, FiscalAttempt,
    FiscalDocument, FiscalDocumentItem, Partner, PartnerUnit, Person, PersonContact,
    ReportAssignment, ReportDocument, SpirometryExam)
from ..normalize import normalize_name, normalize_phone
from ..security import ROLE_LEITURA, can_edit_patient_registration, user_effective_roles
from .command_center import _target, capabilities, _eeg_scope


class SearchLimiter:
    def __init__(self, maximum=30, seconds=60):
        self.maximum, self.seconds = maximum, seconds
        self.calls, self.lock = {}, threading.Lock()

    def consume(self, user_id):
        now = time.monotonic()
        with self.lock:
            for key in list(self.calls):
                if not self.calls[key] or self.calls[key][-1] <= now - self.seconds:
                    del self.calls[key]
            calls = self.calls.setdefault(user_id, deque())
            while calls and calls[0] <= now - self.seconds:
                calls.popleft()
            if len(calls) >= self.maximum or len(self.calls) > 10000:
                raise HTTPException(429, 'Aguarde um momento antes de pesquisar novamente.', headers={'Retry-After': '60'})
            calls.append(now)


limiter = SearchLimiter()


def _prefix(column, value):
    # Escape wildcard input even in the SQLite development fallback.
    return column.like(value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%', escape='\\')


def _name_match(db, value):
    normalized = normalize_name(value)
    words = re.findall(r'[a-z0-9]+', normalized)
    if not words or len(normalized) < 3:
        return false()
    if db.bind.dialect.name == 'postgresql':
        # Same expression as the GIN index; no extension or clinical content.
        return func.to_tsvector(literal_column("'simple'"), Person.nome_normalizado).op('@@')(
            func.to_tsquery(literal_column("'simple'"), ' & '.join(w + ':*' for w in words)))
    conditions = []
    for word in words:
        conditions.append(or_(_prefix(Person.nome_normalizado, word),
                              Person.nome_normalizado.like('% ' + word + '%')))
    from sqlalchemy import and_
    return and_(*conditions)


def _sopro_visibility(db, user):
    roles = user_effective_roles(user)
    if ROLE_LEITURA in roles:
        # Existing corporate administrative people access is not limited to exams.
        return None, None, None
    from ..routers import reports
    reports._require_reports_enabled()
    profile = reports._require_active_physician(db, user)
    docs = select(ReportDocument.id).join(ReportAssignment,
        ReportAssignment.report_document_id == ReportDocument.id).where(
        ReportAssignment.active.is_(True), ReportAssignment.physician_profile_id == profile.id,
        ReportDocument.devolucao_cancelada_em.is_(None))
    exams = select(ReportDocument.spirometry_exam_id).where(ReportDocument.id.in_(docs))
    people = select(SpirometryExam.person_id).where(SpirometryExam.id.in_(exams))
    return people, exams, docs


def search(db, user, brand, term, limit=12):
    cap = capabilities(user, brand)
    limiter.consume(user.id)
    q = term.strip()
    if len(q) < 3 or len(q) > 80 or any(ord(c) < 32 for c in q):
        raise HTTPException(422, 'Informe ao menos 3 caracteres do nome ou um código completo.')
    code = q.upper()
    match = re.fullmatch(r'([A-Z]{2,6})-(\d{1,8})', code)
    if match:
        code = match[1] + '-' + match[2].zfill(6)
    numeric = re.sub(r'\D', '', q)
    is_number = bool(re.fullmatch(r'[\d\s+().-]+', q))
    if is_number and len(numeric) not in (10, 11, 12, 13, 50):
        raise HTTPException(422, 'Informe o telefone completo ou o identificador completo.')
    people_scope = exam_scope = document_scope = None
    eeg_scope = None
    administrative = ROLE_LEITURA in user_effective_roles(user)
    if brand == 'soprolife':
        people_scope, exam_scope, document_scope = _sopro_visibility(db, user)
    else:
        try:
            eeg_scope = _eeg_scope(user)
            people_scope = (select(EegEnrollment.person_id).where(EegEnrollment.status == 'ativo')
                if cap['operacao'] else select(EegEnrollment.person_id).where(EegEnrollment.id.in_(
                    eeg_scope.with_only_columns(EegExam.enrollment_id))))
        except HTTPException:
            people_scope = select(Person.id).where(false())
    groups = []
    remaining = limit

    def add(kind, label, rows):
        nonlocal remaining
        if rows and remaining:
            chosen = rows[:min(4, remaining)]
            groups.append({'tipo': kind, 'rotulo': label, 'itens': chosen})
            remaining -= len(chosen)

    def result(label, code_value, dest, **extra):
        return {'rotulo': label, 'codigo': code_value, 'destino': dest, **extra}

    if is_number:
        # Exact identity only; partial telephone/CPF enumeration is refused.
        person_filter = Person.id.in_(select(PersonContact.person_id).where(
            PersonContact.ativo.is_(True), PersonContact.tipo.in_(('telefone','whatsapp')),
            PersonContact.valor_normalizado == normalize_phone(q)))
        if len(numeric) == 11 and can_edit_patient_registration(user):
            person_filter = or_(person_filter, Person.cpf == numeric)
    elif match and match[1] in ('PES', 'PAC'):
        person_filter = Person.public_code == code
    else:
        person_filter = _name_match(db, q) if not match else false()
    stmt = select(Person).where(Person.arquivado.is_not(True), person_filter)
    if people_scope is not None:
        stmt = stmt.where(Person.id.in_(people_scope))
    people = db.scalars(stmt.order_by(Person.nome_normalizado, Person.id).limit(4)).all()
    people_results = []
    for person in people:
        if brand == 'soprolife' and not administrative:
            doc = db.scalar(select(ReportDocument).where(ReportDocument.id.in_(document_scope),
                ReportDocument.spirometry_exam_id.in_(select(SpirometryExam.id).where(
                    SpirometryExam.person_id == person.id))).order_by(ReportDocument.created_at.desc()).limit(1))
            dest = _target('laudo', doc.id, doc.public_code)
        elif brand == 'eeglife':
            exam = db.scalar(eeg_scope.where(EegExam.enrollment_id.in_(select(EegEnrollment.id).where(
                EegEnrollment.person_id == person.id))).order_by(EegExam.created_at.desc()).limit(1))
            if not exam:
                if not cap['operacao']:
                    continue
                enrollment = db.scalar(select(EegEnrollment).where(EegEnrollment.person_id == person.id, EegEnrollment.status == 'ativo'))
                if not enrollment:
                    continue
                dest = _target('eeg_pessoa', enrollment.id, person.public_code)
            else:
                dest = _target('eeg_exame', exam.id, exam.public_code)
        else:
            dest = _target('pessoa', person.id, person.public_code)
        # A search never returns contact/CPF originals, even to an editor.
        people_results.append(result(person.nome_completo, person.public_code, dest,
            contato_mascarado=('•••• ' + numeric[-4:]) if is_number else None))
    add('pessoas', 'Pacientes / pessoas', people_results)

    if brand == 'soprolife':
        ex = select(SpirometryExam, Person).join(Person, Person.id == SpirometryExam.person_id).where(Person.arquivado.is_not(True))
        ex = ex.where(SpirometryExam.public_code == code) if match else ex.where(_name_match(db,q) if not is_number else false())
        if exam_scope is not None:
            ex = ex.where(SpirometryExam.id.in_(exam_scope))
        out = []
        for exam, person in db.execute(ex.order_by(SpirometryExam.created_at.desc()).limit(4)):
            if not administrative:
                doc = db.scalar(select(ReportDocument).where(ReportDocument.id.in_(document_scope),
                    ReportDocument.spirometry_exam_id == exam.id).order_by(ReportDocument.created_at.desc()).limit(1))
                dest = _target('laudo', doc.id, doc.public_code)
            else:
                dest = _target('exame', exam.id, exam.public_code, pessoa=person.public_code)
            out.append(result(person.nome_completo, exam.public_code, dest, data=exam.data_exame))
        add('exames', 'Exames', out)
        docs = select(ReportDocument, Person).join(SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id).join(Person,
            Person.id == SpirometryExam.person_id).where(Person.arquivado.is_not(True))
        docs = docs.where(ReportDocument.public_code == code) if match else docs.where(_name_match(db,q) if not is_number else false())
        if document_scope is not None:
            docs = docs.where(ReportDocument.id.in_(document_scope))
        # A leitura-only account has no report operation permission.
        if cap['operacao'] or cap['clinico']:
            add('laudos','Laudos',[result(p.nome_completo,d.public_code,_target('laudo',d.id,d.public_code))
                for d,p in db.execute(docs.order_by(ReportDocument.created_at.desc()).limit(4))])
    elif eeg_scope is not None:
        ex = eeg_scope.join(EegEnrollment,EegEnrollment.id==EegExam.enrollment_id).join(Person,Person.id==EegEnrollment.person_id).where(Person.arquivado.is_not(True))
        ex = ex.where(EegExam.public_code == code) if match else ex.where(_name_match(db,q) if not is_number else false())
        add('exames','Exames EEG',[result(e.public_code,e.public_code,_target('eeg_exame',e.id,e.public_code)) for e in db.scalars(ex.limit(4))])
        docs=select(EegReportDocument).where(EegReportDocument.exam_id.in_(eeg_scope.with_only_columns(EegExam.id)),EegReportDocument.public_code==code)
        add('laudos','Laudos EEG',[result(d.public_code,d.public_code,_target('eeg_exame',d.exam_id,d.public_code)) for d in db.scalars(docs.limit(4))])

    from .partnership_center import catalog as partnership_catalog
    partnership_codes = {p['partner_id']:p['codigo'] for p in partnership_catalog(db)} if brand=='soprolife' and administrative else {}
    if (brand=='soprolife' and administrative) or (brand=='eeglife' and cap['operacao']):
        partner_filter = Partner.public_code == code if match else _prefix(func.lower(Partner.nome),q.lower()) if not is_number else false()
        add('parceiros','Parceiros',[result(p.nome,p.public_code,_target('parceiro',p.id,p.public_code),
                **({'ver_parceria':_target('parceria',p.id,p.public_code)} if partnership_codes.get(p.id) in ('maclin','alergocare') else {}))
            for p in db.scalars(select(Partner).where(Partner.arquivado.is_(False),partner_filter).order_by(Partner.nome).limit(4))])
        unit_filter = PartnerUnit.public_code == code if match else _prefix(func.lower(PartnerUnit.nome),q.lower()) if not is_number else false()
        add('unidades','Unidades',[result(u.nome,u.public_code,_target('unidade',u.id,u.public_code,parceiro=u.partner_id))
            for u in db.scalars(select(PartnerUnit).join(Partner).where(Partner.arquivado.is_(False),PartnerUnit.ativo.is_(True),unit_filter).order_by(PartnerUnit.nome).limit(4))])
    if cap['fiscal']:
        doc_filter = FiscalDocument.id == q
        if match and match[1]=='ESP' and brand=='soprolife':
            exam_ids=select(SpirometryExam.id).where(SpirometryExam.public_code==code)
            doc_filter=or_(FiscalDocument.spirometry_exam_id.in_(exam_ids),FiscalDocument.id.in_(
                select(FiscalDocumentItem.document_id).where(FiscalDocumentItem.spirometry_exam_id.in_(exam_ids))))
        elif len(q)>=10:
            doc_filter=or_(doc_filter,FiscalDocument.id.in_(select(FiscalAttempt.document_id).where(FiscalAttempt.external_id==q)))
        docs=select(FiscalDocument).where(doc_filter,FiscalDocument.environment=='production',
            FiscalDocument.kind=='eeg' if brand=='eeglife' else FiscalDocument.kind!='eeg')
        add('fiscal','NFS-e / documentos fiscais',[result('NFS-e · '+d.state,'Documento fiscal',_target('fiscal',d.id,'NFS-e'))
            for d in db.scalars(docs.order_by(FiscalDocument.created_at.desc()).limit(4))])
    return {'marca':brand,'grupos':groups,'total':sum(len(g['itens']) for g in groups),'limite':limit}


def lookup(db, user, brand, kind, record_id):
    """Resolve a bookmark/selection by its ID under the SAME search scope.

    Only compact metadata; the existing destination module owns all actions.
    A direct URL never widens a physician's assigned set or the chosen brand.
    """
    from ..models import ExamPartnerShare, Lead, PartnerSettlement
    cap = capabilities(user, brand)
    admin_read = ROLE_LEITURA in user_effective_roles(user)
    people_scope = exam_scope = doc_scope = None
    if kind == 'parceria' and not admin_read:
        raise HTTPException(403,'Área de parcerias não autorizada.')
    if brand == 'soprolife':
        people_scope, exam_scope, doc_scope = _sopro_visibility(db, user)
    data, target = {}, None
    if kind == 'parceria' and brand == 'soprolife' and admin_read:
        from .partnership_center import code_for_partner
        code=code_for_partner(db,record_id)
        if code:data={'codigo':code};target=_target(kind,record_id)
    elif kind == 'pessoa' and brand == 'soprolife' and admin_read:
        p = db.scalar(select(Person).where(Person.id == record_id, Person.arquivado.is_not(True)))
        if p:
            data={'nome_completo':p.nome_completo,'public_code':p.public_code};target=_target(kind,p.id,p.public_code)
    elif kind in ('exame','agenda') and brand == 'soprolife':
        stmt=select(SpirometryExam,Person).join(Person,Person.id==SpirometryExam.person_id).where(SpirometryExam.id==record_id,Person.arquivado.is_not(True))
        if exam_scope is not None:stmt=stmt.where(SpirometryExam.id.in_(exam_scope))
        row=db.execute(stmt).first()
        if row:
            e,p=row
            data={'public_code':e.public_code,'nome_completo':p.nome_completo,'status':e.status,'data':e.data_exame}
            if not admin_read:
                d=db.scalar(select(ReportDocument).where(ReportDocument.id.in_(doc_scope),ReportDocument.spirometry_exam_id==e.id).limit(1))
                target=_target('laudo',d.id,d.public_code)
            else:target=_target('exame',e.id,e.public_code,pessoa=p.public_code,pessoa_id=p.id)
    elif kind=='laudo' and brand=='soprolife' and (cap['operacao'] or cap['clinico']):
        stmt=select(ReportDocument).join(SpirometryExam).join(Person).where(ReportDocument.id==record_id,Person.arquivado.is_not(True))
        if doc_scope is not None:stmt=stmt.where(ReportDocument.id.in_(doc_scope))
        d=db.scalar(stmt)
        if d:data={'public_code':d.public_code,'status':d.status};target=_target(kind,d.id,d.public_code)
    elif kind=='eeg_pessoa' and brand=='eeglife' and cap['operacao']:
        row=db.execute(select(EegEnrollment,Person).join(Person).where(EegEnrollment.id==record_id,EegEnrollment.status=='ativo',Person.arquivado.is_not(True))).first()
        if row:
            enrollment,p=row;data={'public_code':p.public_code,'nome_completo':p.nome_completo,'status':enrollment.status};target=_target(kind,enrollment.id,p.public_code)
    elif kind=='eeg_exame' and brand=='eeglife':
        e=db.scalar(_eeg_scope(user).where(EegExam.id==record_id))
        if e:
            from ..routers.eeg import _ser_exame
            data=_ser_exame(db,e);target=_target(kind,e.id,e.public_code)
    elif kind in ('parceiro','unidade') and (brand=='soprolife' and admin_read or brand=='eeglife' and cap['operacao']):
        if kind=='parceiro':
            p=db.scalar(select(Partner).where(Partner.id==record_id,Partner.arquivado.is_(False)))
            if p:data={'public_code':p.public_code,'nome':p.nome,'status':p.status};target=_target(kind,p.id,p.public_code)
        else:
            u=db.scalar(select(PartnerUnit).join(Partner).where(PartnerUnit.id==record_id,PartnerUnit.ativo.is_(True),Partner.arquivado.is_(False)))
            if u:data={'public_code':u.public_code,'nome':u.nome,'cidade':u.cidade};target=_target(kind,u.id,u.public_code,parceiro=u.partner_id)
    elif kind=='fiscal' and cap['fiscal']:
        d=db.scalar(select(FiscalDocument).where(FiscalDocument.id==record_id,FiscalDocument.environment=='production',FiscalDocument.kind=='eeg' if brand=='eeglife' else FiscalDocument.kind!='eeg'))
        if d:
            from .nfse import serialize_document
            data=serialize_document(db,d)
            target=_target(kind,d.id,'NFS-e · '+d.id[:8])
    elif kind=='contato' and brand=='soprolife' and admin_read:
        row=db.execute(select(Lead,Person).join(Person).where(Lead.id==record_id,Person.arquivado.is_not(True),Lead.primeiro_contato_em.is_not(None))).first()
        if row:
            l,p=row;data={'public_code':l.public_code,'nome_completo':p.nome_completo,'status':l.status_funil};target=_target(kind,l.id,l.public_code)
    elif kind=='acerto' and brand=='soprolife' and cap['financeiro']:
        share=db.get(ExamPartnerShare,record_id)
        if share:
            e=db.get(SpirometryExam,share.spirometry_exam_id);data={'public_code':e.public_code};target=_target(kind,share.id,e.public_code)
    elif kind=='pastore' and brand=='soprolife' and cap['financeiro']:
        settlement=db.get(PartnerSettlement,record_id)
        if settlement:data={'public_code':'Fechamento','status':settlement.status};target=_target(kind,settlement.id,'Fechamento')
    if target is None:
        raise HTTPException(404,'Registro não encontrado neste contexto.')
    return {'marca':brand,'destino':target,'dados':data}
