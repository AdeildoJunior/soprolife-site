"""Operação EEGLife sobre People, sessão, razão e storage Life compartilhados."""
from datetime import datetime, timezone, date
from decimal import Decimal
from pathlib import Path
from io import BytesIO
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response
from sqlalchemy import select, func, or_, text
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import (User, Person, PersonContact, EegEnrollment, EegExam, EegTechnicalFile,
    EegCase, EegCaseRevision, EegProfessional, EegRelation, EegActivity, EegDocument,
    EegPreparation, EegPartnerScope, Partner, PartnerUnit, FinancialEntry, AuditLog,
    EegReportDocument)
from ..config import get_settings
from ..ids import new_uuid, allocate_public_code
from ..audit import audit
from ..errors import ReportDomainError as DomainError
from ..pagination import PageParams, paginate
from ..security import (require_eeg_access, require_eeg_operator, require_eeg_technician,
    require_eeg_finance, require_role, user_effective_roles, get_current_user)
from ..schemas import PatientRegistrationUpdate
from ..schemas_eeg import (EegRegistration, RelationIn, ActivityIn, ConvertIn, ProfessionalIn,
    AssignIn, ReportContent, CaseAction, PartnerIn, PreparationIn, DOC_CATEGORIES, CrmSearch)
from ..services.eeg_import import ManualNanoAdapter, MAX_TECHNICAL_BYTES, storage_path, read_checked
from ..services.report_storage import atomic_write_new_file, ReportStorageError
from ..services.eeg_workflow import case_for, snapshot, serialize, require_author, require_exam
from ..services.eeg_modalidades import obter, MODALIDADES
from ..services import eeg_flow
from ..normalize import normalize_name, normalize_phone

router = APIRouter(prefix='/eeg', tags=['eeg-operacional'])

def now(): return datetime.now(timezone.utc)

def event(db, request, user, action, entity, entity_id, **details):
    audit(db, 'eeg.'+action, entity, entity_id, user_id=user.id,
          request_id=getattr(request.state,'request_id',None), detalhes={'contexto':'eeglife',**details})

def enrollment(db, eid):
    v=db.get(EegEnrollment,eid)
    if not v or v.status != 'ativo': raise HTTPException(404,'Vínculo EEG não encontrado.')
    p=db.get(Person,v.person_id)
    if not p or p.arquivado: raise DomainError(409,'pessoa_arquivada','Pessoa arquivada.')
    return v,p

def exam(db, eid):
    e=db.get(EegExam,eid,with_for_update=True)
    if not e: raise HTTPException(404,'EEG não encontrado.')
    enrollment(db,e.enrollment_id)
    if e.status == 'cancelado': raise DomainError(409,'exame_cancelado','EEG cancelado.')
    return e

def serial_activity(a):
    return {k:getattr(a,k) for k in ('id','tipo','titulo','categoria','status','origem','contato','consentimento','enrollment_id','exam_id','responsavel_id','prazo','created_at')}

def valid_activity(db,payload):
    if payload.enrollment_id: enrollment(db,payload.enrollment_id)
    if payload.exam_id:
        e=exam(db,payload.exam_id)
        if payload.enrollment_id != e.enrollment_id: raise DomainError(422,'vinculo_incoerente','Exame e pessoa não correspondem.')
    if payload.tipo == 'tarefa' and payload.contato:
        raise DomainError(422,'contato_no_lead','Contato pertence ao lead ou cadastro, não à tarefa.')
    if payload.responsavel_id:
        u=db.get(User,payload.responsavel_id)
        roles=user_effective_roles(u) if u and u.ativo else set()
        needed={'admin','eeg_operador'}
        if payload.categoria == 'cobrar_tracado': needed={'admin','eeg_tecnico'}
        if payload.categoria == 'cobranca': needed={'admin','eeg_financeiro'}
        if not roles & needed: raise DomainError(422,'responsavel_invalido','Responsável sem papel adequado à tarefa.')


@router.get('/equipe')
def team(db: Session=Depends(get_db), user: User=Depends(require_eeg_operator)):
    return {'itens':[{'id':u.id,'nome':u.nome,'papeis':sorted(user_effective_roles(u))} for u in db.scalars(select(User).where(User.ativo.is_(True)))
        if user_effective_roles(u)&{'admin','eeg_operador','eeg_tecnico','medical_eeg','eeg_financeiro'}]}


@router.get('/municipios')
def municipalities(user: User=Depends(require_eeg_operator)):
    from ..services.nfse_national.service_location import SUPPORTED_SERVICE_MUNICIPALITIES
    return {'itens':[{'codigo':c,'rotulo':n} for c,n in SUPPORTED_SERVICE_MUNICIPALITIES.items()]}


@router.patch('/pessoas/{eid}/cadastro')
def registration(eid: str, payload: EegRegistration, request: Request,
                 db: Session=Depends(get_db), user: User=Depends(require_eeg_operator)):
    v,p=enrollment(db,eid)
    # Serviço corporativo existente preserva a identidade e a trilha cadastral.
    from .people import update_patient_registration
    fields=payload.model_dump(exclude_unset=True,exclude={'consentimento_whatsapp'})
    if fields: update_patient_registration(p.id,PatientRegistrationUpdate(**fields),request,db,user)
    if payload.consentimento_whatsapp is not None:
        before=v.consentimento_whatsapp
        v.consentimento_whatsapp=payload.consentimento_whatsapp
        event(db,request,user,'consentimento_atualizado','eeg_enrollments',v.id,status=v.consentimento_whatsapp,status_anterior=before)
    db.commit()
    from .eeg import _ser_vinculo
    return _ser_vinculo(db,v)


@router.get('/pessoas/{eid}/historico')
def history(eid: str, db: Session=Depends(get_db), user: User=Depends(require_eeg_operator)):
    enrollment(db,eid)
    from .eeg import _ser_exame
    exams=db.scalars(select(EegExam).where(EegExam.enrollment_id==eid).order_by(EegExam.created_at.desc())).all()
    exam_ids=[e.id for e in exams]
    ids=[eid,*exam_ids]
    for model in (EegTechnicalFile,EegReportDocument,EegCase):
        ids.extend(db.scalars(select(model.id).where(model.exam_id.in_(exam_ids))).all())
    logs=db.scalars(select(AuditLog).where(AuditLog.acao.like('eeg.%'),AuditLog.entidade_id.in_(ids)).order_by(AuditLog.ts_utc.desc()).limit(100))
    return {'exames':[_ser_exame(db,e) for e in exams], 'eventos':[{'acao':a.acao,'quando':a.ts_utc,'entidade_id':a.entidade_id} for a in logs]}


@router.post('/crm/busca')
def crm(payload: CrmSearch,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    q,situacao=payload.q,payload.situacao
    params=PageParams(pagina=payload.pagina,tamanho=payload.tamanho)
    stmt=select(EegEnrollment).join(Person,Person.id==EegEnrollment.person_id).outerjoin(EegRelation,EegRelation.enrollment_id==EegEnrollment.id).where(EegEnrollment.status=='ativo',Person.arquivado.is_(False))
    if q: stmt=stmt.where(Person.nome_normalizado.contains(normalize_name(q),autoescape=True))
    if situacao: stmt=stmt.where(EegRelation.situacao==situacao)
    stmt=stmt.order_by(EegRelation.proximo_contato.asc().nulls_last(),Person.nome_normalizado)
    def ser(v):
        p=db.get(Person,v.person_id); rel=db.scalar(select(EegRelation).where(EegRelation.enrollment_id==v.id))
        last=db.scalar(select(EegExam).where(EegExam.enrollment_id==v.id).order_by(EegExam.realizado_em.desc().nulls_last(),EegExam.created_at.desc()))
        contact=db.scalar(select(PersonContact).where(PersonContact.person_id==p.id,PersonContact.ativo.is_(True),PersonContact.tipo.in_(['telefone','whatsapp'])).order_by(PersonContact.principal.desc(),PersonContact.created_at.desc()))
        phone=contact.valor_normalizado if contact else None
        return {'id':v.id,'codigo':p.public_code,'paciente':p.nome_completo,'contato':phone,
            'whatsapp_url':('https://wa.me/'+phone) if phone and v.consentimento_whatsapp=='concedido' and not p.nao_contatar else None,
            'ultimo_eeg':{'codigo':last.public_code,'data':last.realizado_em,'status':last.status} if last else None,
            'proximo_contato':rel.proximo_contato if rel else None,'situacao':rel.situacao if rel else 'ativo',
            'origem':last.origem_canal if last else v.origem,'consentimento':v.consentimento_whatsapp}
    return paginate(db,stmt,params,ser)


@router.get('/crm')
def crm_index(db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    return crm(CrmSearch(),db,user)


@router.patch('/crm/{eid}')
def update_crm(eid: str,payload: RelationIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    enrollment(db,eid)
    r=db.scalar(select(EegRelation).where(EegRelation.enrollment_id==eid).with_for_update())
    if not r: r=EegRelation(enrollment_id=eid);db.add(r)
    r.proximo_contato=payload.proximo_contato;r.situacao=payload.situacao
    event(db,request,user,'crm_atualizado','eeg_enrollments',eid,status=r.situacao)
    db.commit();return {'status':r.situacao}


@router.get('/atividades')
def activities(tipo: str,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator),params: PageParams=Depends()):
    return paginate(db,select(EegActivity).where(EegActivity.tipo==tipo).order_by(EegActivity.prazo.asc().nulls_last(),EegActivity.created_at.desc()),params,serial_activity)


@router.post('/atividades',status_code=201)
def create_activity(payload: ActivityIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    if payload.status=='convertido': raise DomainError(422,'usar_conversao','Use a conversão explícita.')
    valid_activity(db,payload)
    a=EegActivity(**payload.model_dump(),criado_por=user.id);db.add(a);db.flush()
    event(db,request,user,'atividade_criada','eeg_activities',a.id,tipo=a.tipo,status=a.status)
    db.commit();return serial_activity(a)


@router.patch('/atividades/{aid}')
def update_activity(aid: str,payload: ActivityIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    a=db.get(EegActivity,aid,with_for_update=True)
    if not a or a.tipo!=payload.tipo: raise HTTPException(404,'Atividade EEG não encontrada.')
    if a.exam_id and (payload.exam_id!=a.exam_id or payload.enrollment_id!=a.enrollment_id):
        raise DomainError(409,'vinculo_congelado','Preserve a pessoa e o EEG do atendimento convertido.')
    if payload.status=='convertido': raise DomainError(422,'usar_conversao','Use a conversão explícita.')
    valid_activity(db,payload)
    for k,v in payload.model_dump().items(): setattr(a,k,v)
    event(db,request,user,'atividade_atualizada','eeg_activities',a.id,status=a.status)
    db.commit();return serial_activity(a)


@router.post('/atividades/{aid}/converter')
def convert(aid: str,payload: ConvertIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    a=db.get(EegActivity,aid,with_for_update=True)
    if not a or a.tipo!='lead': raise HTTPException(404,'Lead EEG não encontrado.')
    enrollment(db,payload.enrollment_id)
    if payload.exam_id and exam(db,payload.exam_id).enrollment_id!=payload.enrollment_id:
        raise DomainError(422,'vinculo_incoerente','Atendimento não pertence à pessoa selecionada.')
    if a.enrollment_id and a.enrollment_id!=payload.enrollment_id:
        raise DomainError(409,'conversao_divergente','Pessoa já vinculada; preserve a identidade conferida.')
    if a.exam_id:
        if a.exam_id!=payload.exam_id: raise DomainError(409,'conversao_divergente','Lead já ligado a outro EEG.')
        return serial_activity(a)
    a.enrollment_id=payload.enrollment_id;a.exam_id=payload.exam_id
    a.status='confirmado' if payload.exam_id else 'pre_agendado'
    event(db,request,user,'lead_convertido','eeg_activities',a.id,status=a.status)
    db.commit();return serial_activity(a)


@router.get('/profissionais')
def professionals(db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    return {'itens':[dict(snapshot(p),user_id=p.user_id) for p in db.scalars(select(EegProfessional).where(EegProfessional.ativo.is_(True)).order_by(EegProfessional.nome))]}


@router.post('/profissionais',status_code=201)
def professional(payload: ProfessionalIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_role('admin'))):
    if payload.user_id:
        u=db.get(User,payload.user_id)
        if not u or not u.ativo or 'medical_eeg' not in user_effective_roles(u) or u.id==user.id:
            raise DomainError(422,'profissional_invalido','Conta exige medical_eeg explícito e verificação por outro administrador.')
    if payload.tipo=='interno' and not payload.user_id: raise DomainError(422,'conta_obrigatoria','Médico interno exige conta medical_eeg.')
    if db.scalar(select(EegProfessional.id).where(EegProfessional.crm==payload.crm,EegProfessional.uf==payload.uf)):
        raise DomainError(409,'crm_duplicado','CRM/UF já cadastrado.')
    p=EegProfessional(**payload.model_dump(),verificado_por=user.id);db.add(p);db.flush()
    event(db,request,user,'profissional_verificado','eeg_professionals',p.id,tipo=p.tipo)
    db.commit();return snapshot(p)


@router.post('/exames/{eid}/importar',status_code=201)
def upload_trace(eid: str,request: Request,arquivo: UploadFile=File(...),confirmacao_codigo: str=Form(...),
                 db: Session=Depends(get_db),user: User=Depends(require_eeg_technician)):
    e=exam(db,eid)
    if e.public_code!=confirmacao_codigo: raise DomainError(422,'confirmacao_divergente','Confirme o código EEG e paciente antes de importar.')
    if e.status not in eeg_flow.EXAME_JA_REALIZADO: raise DomainError(409,'exame_nao_realizado','Registre a realização antes de importar.')
    if e.status in {eeg_flow.LAUDO_RECEBIDO,eeg_flow.LAUDO_DISPONIVEL}:
        raise DomainError(409,'laudo_congelado','Exame com laudo recebido/publicado exige atendimento corretivo explícito.')
    c=case_for(db,e.id,create=True)
    if c.status not in {'aguardando_arquivo','aguardando_conferencia','pronto_laudo','atribuido','em_elaboracao'}:
        raise DomainError(409,'laudo_congelado','Traçado de laudo concluído exige revisão e novo atendimento corretivo.')
    data=arquivo.file.read(MAX_TECHNICAL_BYTES+1)
    meta=ManualNanoAdapter().validate_export(data,Path(arquivo.filename or '').suffix,arquivo.content_type or '')
    f=EegTechnicalFile(id=new_uuid(),exam_id=e.id,armazenamento_origem='object_storage',formato=meta['extensao'],mime_type=meta['mime_type'],tamanho_bytes=meta['size_bytes'],sha256=meta['sha256'],equipamento='Nano EEG iCelera',storage_provider='life_private_filesystem',criado_por=user.id)
    root=get_settings().resolved_reports_storage_dir();path=storage_path(root,e.id,f.id,meta['extensao'])
    atomic_write_new_file(path,data,root=root);f.object_key=str(path.relative_to(root));db.add(f);db.flush()
    previous=c.current_file_id;c.current_file_id=f.id;c.status='aguardando_conferencia';c.conferido_em=None;c.conferido_por=None
    # Uma substituição invalida a atribuição até nova conferência explícita.
    c.professional_id=None;c.professional_snapshot=None;c.atribuido_em=None
    event(db,request,user,'arquivo_importado','eeg_exams',e.id,sha256=f.sha256,size_bytes=f.tamanho_bytes,previous_version_id=previous)
    db.commit();return {'arquivo_id':f.id,**serialize(c)}


@router.get('/exames/{eid}/arquivos')
def files(eid: str,db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    require_exam(db,user,eid)
    return {'itens':[{'id':f.id,'formato':f.formato,'sha256':f.sha256,'size_bytes':f.tamanho_bytes,'criado_em':f.criado_em,'privado':bool(f.object_key),'criado_por':f.criado_por} for f in db.scalars(select(EegTechnicalFile).where(EegTechnicalFile.exam_id==eid).order_by(EegTechnicalFile.criado_em.desc()))]}


@router.get('/arquivos/{fid}/conteudo')
def trace_download(fid: str,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    f=db.get(EegTechnicalFile,fid)
    if not f or not f.object_key or f.storage_provider!='life_private_filesystem': raise HTTPException(404,'Arquivo privado não encontrado.')
    require_exam(db,user,f.exam_id)
    root=get_settings().resolved_reports_storage_dir()
    try: data=read_checked(root,f.object_key,f.sha256,f.tamanho_bytes)
    except (OSError,ReportStorageError): raise HTTPException(404,'Arquivo indisponível.') from None
    event(db,request,user,'arquivo_baixado','eeg_technical_files',f.id)
    db.commit()
    return Response(data,media_type=f.mime_type,headers={'Content-Disposition':f'attachment; filename="tracado-{f.id}.{f.formato}"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.get('/exames/{eid}/bancada')
def report_desk(eid: str,db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    require_exam(db,user,eid)
    c=case_for(db,eid)
    if not c: return {'fluxo':serialize(None),'conteudo':None,'versoes':0}
    require_author(db,user,c)
    r=db.scalar(select(EegCaseRevision).where(EegCaseRevision.case_id==c.id).order_by(EegCaseRevision.numero.desc()))
    return {'fluxo':serialize(c),'conteudo':r.conteudo if r else None,'versoes':r.numero if r else 0}


@router.post('/exames/{eid}/atribuir')
def assign(eid: str,payload: AssignIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    e=exam(db,eid);c=case_for(db,eid,create=True)
    if c.status not in {'pronto_laudo','atribuido'}: raise DomainError(409,'tracado_nao_conferido','Confira o arquivo técnico vigente antes da atribuição.')
    p=db.get(EegProfessional,payload.professional_id)
    if not p or not p.ativo: raise HTTPException(404,'Profissional EEG não encontrado.')
    if p.user_id:
        u=db.get(User,p.user_id)
        if not u or not u.ativo or 'medical_eeg' not in user_effective_roles(u): raise DomainError(409,'medico_sem_papel','Profissional sem papel medical_eeg ativo.')
    c.professional_id=p.id;c.professional_snapshot=snapshot(p);c.atribuido_em=now();c.status='atribuido'
    e.status=eeg_flow.ENVIADO;e.enviado_para_laudo_em=now()
    event(db,request,user,'laudo_atribuido','eeg_exams',e.id,target_user_id=p.user_id,physician_profile_id=p.id)
    db.commit();return serialize(c)


@router.post('/exames/{eid}/redacao')
def draft(eid: str,payload: ReportContent,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    e=exam(db,eid);c=case_for(db,eid)
    if not c: raise DomainError(409,'atribuicao_ausente','Atribua o laudo antes da redação.')
    require_author(db,user,c)
    if c.status not in {'atribuido','em_elaboracao'}: raise DomainError(409,'laudo_congelado','Laudo fora da etapa de elaboração.')
    number=(db.scalar(select(func.max(EegCaseRevision.numero)).where(EegCaseRevision.case_id==c.id)) or 0)+1
    r=EegCaseRevision(case_id=c.id,numero=number,conteudo=payload.model_dump(),professional_snapshot={**c.professional_snapshot, "technical_file_id":c.current_file_id},criado_por=user.id)
    db.add(r);c.status='em_elaboracao';e.status=eeg_flow.EM_LAUDO
    event(db,request,user,'laudo_redigido','eeg_exams',eid,version=number)
    db.commit();return {'versao':number,'status':c.status}


def generate_final(db,e,c):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.colors import HexColor
    from ..services.pdf_validation import validate_pdf_bytes
    revision=db.scalar(select(EegCaseRevision).where(EegCaseRevision.case_id==c.id).order_by(EegCaseRevision.numero.desc()))
    if not revision: raise DomainError(409,'redacao_ausente','Salve a redação antes da conclusão.')
    _,p=enrollment(db,e.enrollment_id)
    buf=BytesIO();styles=getSampleStyleSheet();styles['Heading1'].textColor=HexColor('#865b1d')
    elements=[Paragraph('EEGLife · Laudo de eletroencefalografia',styles['Heading1'])]
    for label,value in [('Código EEG',e.public_code),('Paciente',p.nome_completo),('Nascimento',p.data_nascimento.strftime('%d/%m/%Y') if p.data_nascimento else 'Não informado'),('Modalidade',obter(e.modalidade_codigo).nome),('Local',e.local_atendimento or 'Não informado'),('Realização',e.realizado_em.isoformat() if e.realizado_em else 'Não informada')]:
        elements.append(Paragraph(escape(label+': '+str(value)),styles['Normal']))
    for key,label in [('indicacao','Indicação clínica'),('tecnica','Técnica'),('descricao','Descrição'),('conclusao','Conclusão'),('observacoes','Observações')]:
        value=revision.conteudo.get(key)
        if value: elements.extend([Spacer(1,12),Paragraph(label,styles['Heading2']),Paragraph(escape(value).replace('\n','<br/>'),styles['Normal'])])
    p=c.professional_snapshot
    elements.extend([Spacer(1,20),Paragraph(escape(p['nome']+' · CRM '+p['crm']+'/'+p['uf']+(' · RQE '+p['rqe'] if p.get('rqe') else '')),styles['Normal']),Paragraph('PDF final · aguardando assinatura externa',styles['Normal'])])
    SimpleDocTemplate(buf,title='Laudo EEG',author='EEGLife',pageCompression=1).build(elements)
    data=buf.getvalue();v=validate_pdf_bytes(data,max_size_bytes=get_settings().reports_max_upload_bytes)
    number=(db.scalar(select(func.max(EegReportDocument.versao)).where(EegReportDocument.exam_id==e.id)) or 0)+1
    d=EegReportDocument(public_code=allocate_public_code(db,'eeg_report_documents'),exam_id=e.id,versao=number,storage_path='',sha256=v.sha256,size_bytes=len(data),page_count=v.page_count,origem='editor_eeg',medico_laudador=p['nome'])
    db.add(d);db.flush()
    from ..services.eeg_storage import eeg_report_storage_path
    root=get_settings().resolved_reports_storage_dir();path=eeg_report_storage_path(root,exam_id=e.id,document_id=d.id)
    atomic_write_new_file(path,data,root=root);d.storage_path=str(path.relative_to(root));c.final_document_id=d.id


@router.post('/exames/{eid}/fluxo')
def action(eid: str,payload: CaseAction,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    e=exam(db,eid)
    if payload.confirmacao_codigo!=e.public_code: raise DomainError(422,'confirmacao_divergente','Confira o paciente e confirme o código EEG.')
    c=case_for(db,eid,create=True);roles=user_effective_roles(user)
    if payload.acao=='concluir':
        require_author(db,user,c)
        if c.status!='em_elaboracao': raise DomainError(409,'etapa_invalida','Salve a redação para concluir.')
        generate_final(db,e,c);c.status='aguardando_assinatura';c.concluido_em=now()
    else:
        allowed={'admin','eeg_tecnico'} if payload.acao=='conferir' else {'admin','eeg_operador'}
        if not roles & allowed: raise HTTPException(403,'Papel inadequado à ação.')
        if payload.acao=='conferir':
            if c.status!='aguardando_conferencia' or not c.current_file_id: raise DomainError(409,'arquivo_ausente','Importe um traçado para conferência.')
            f=db.get(EegTechnicalFile,c.current_file_id)
            read_checked(get_settings().resolved_reports_storage_dir(),f.object_key,f.sha256,f.tamanho_bytes)
            c.status='pronto_laudo';c.conferido_em=now();c.conferido_por=user.id
        elif payload.acao=='publicar':
            if c.status!='recebido_assinado' or not c.assinado_document_id: raise DomainError(409,'assinatura_ausente','Receba e confira o PDF assinado antes de liberar.')
            d=db.get(EegReportDocument,c.assinado_document_id)
            from ..services.report_storage import read_and_validate_stored_pdf
            root=get_settings().resolved_reports_storage_dir()
            read_and_validate_stored_pdf(root/d.storage_path,root=root,expected_sha256=d.sha256,expected_size_bytes=d.size_bytes,expected_page_count=d.page_count,max_size_bytes=get_settings().reports_max_upload_bytes,allow_signature_form=True)
            d.status='vigente';d.disponibilizado_em=now();e.status=eeg_flow.LAUDO_DISPONIVEL;e.disponibilizado_em=now();c.status='pronto_entrega'
        elif payload.acao=='entregar':
            if c.status!='pronto_entrega': raise DomainError(409,'etapa_invalida','Libere o resultado antes de registrar entrega.')
            c.status='entregue';c.entregue_em=now()
    event(db,request,user,'laudo_'+payload.acao,'eeg_exams',eid,status=c.status)
    db.commit();return serialize(c)


@router.get('/preparos')
def preparations(db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    result=[]
    for m in MODALIDADES:
        p=db.scalar(select(EegPreparation).where(EegPreparation.modalidade_codigo==m.codigo).order_by(EegPreparation.criado_em.desc()))
        result.append({'codigo':m.codigo,'modalidade':m.nome,'texto':p.texto if p else None,'referencia_aprovacao':p.referencia_aprovacao if p else None,'status':'aprovado' if p else 'aguardando_aprovacao'})
    return {'itens':result}


@router.post('/preparos',status_code=201)
def approve_preparation(payload: PreparationIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    if 'medical_eeg' not in user_effective_roles(user): raise HTTPException(403,'Aprovação de preparo exige médico EEG.')
    if not obter(payload.modalidade_codigo): raise HTTPException(422,'Modalidade desconhecida.')
    p=EegPreparation(**payload.model_dump(),aprovado_por=user.id);db.add(p);db.flush()
    event(db,request,user,'preparo_aprovado','eeg_preparations',p.id,codigo=p.modalidade_codigo)
    db.commit();return {'id':p.id}


@router.get('/parceiros')
def partners(db: Session=Depends(get_db),user: User=Depends(require_eeg_operator),params: PageParams=Depends()):
    def ser(s):
        p=db.get(Partner,s.partner_id)
        return {'id':s.id,'partner_id':p.id,'nome':p.nome,'categoria':s.categoria,'ativo':s.ativo,'unidades':[{'id':u.id,'nome':u.nome} for u in db.scalars(select(PartnerUnit).where(PartnerUnit.partner_id==p.id,PartnerUnit.ativo.is_(True)))]}
    return paginate(db,select(EegPartnerScope).order_by(EegPartnerScope.created_at.desc()),params,ser)


@router.get('/parceiros/corporativos')
def corporate_partners(db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    return {'itens':[{'id':p.id,'codigo':p.public_code,'nome':p.nome} for p in db.scalars(select(Partner).where(Partner.arquivado.is_(False)).order_by(Partner.nome,Partner.id))]}


@router.post('/parceiros',status_code=201)
def partner(payload: PartnerIn,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    p=db.get(Partner,payload.partner_id) if payload.partner_id else None
    if payload.partner_id and (not p or p.arquivado): raise HTTPException(404,'Parceiro corporativo não encontrado.')
    if not p:
        if not payload.nome: raise HTTPException(422,'Informe nome ou parceiro corporativo existente.')
        if db.scalar(select(Partner.id).where(func.lower(Partner.nome)==payload.nome.lower(),Partner.arquivado.is_(False))): raise DomainError(409,'parceiro_existente','Vincule o parceiro corporativo existente pelo identificador.')
        p=Partner(public_code=allocate_public_code(db,'partners'),nome=payload.nome,tipo=payload.categoria);db.add(p);db.flush()
    s=db.scalar(select(EegPartnerScope).where(EegPartnerScope.partner_id==p.id))
    if not s: s=EegPartnerScope(partner_id=p.id,categoria=payload.categoria);db.add(s)
    if payload.unidade and not db.scalar(select(PartnerUnit.id).where(PartnerUnit.partner_id==p.id,PartnerUnit.nome==payload.unidade)):
        db.add(PartnerUnit(public_code=allocate_public_code(db,'partner_units'),partner_id=p.id,nome=payload.unidade))
    event(db,request,user,'parceiro_vinculado','partners',p.id)
    db.commit();return {'partner_id':p.id,'id':s.id}


@router.get('/documentos')
def documents(db: Session=Depends(get_db),user: User=Depends(require_eeg_operator),params: PageParams=Depends()):
    return paginate(db,select(EegDocument).order_by(EegDocument.created_at.desc()),params,lambda d:{k:getattr(d,k) for k in ('id','titulo','categoria','validade','predecessor_id','sha256','size_bytes','created_at')})


@router.post('/documentos',status_code=201)
def document(request: Request,arquivo: UploadFile=File(...),titulo: str=Form(...,min_length=2,max_length=200),categoria: str=Form(...),validade: date|None=Form(None),predecessor_id: str|None=Form(None),db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    if categoria not in DOC_CATEGORIES: raise HTTPException(422,'Categoria inválida.')
    old=db.get(EegDocument,predecessor_id) if predecessor_id else None
    if predecessor_id and not old: raise HTTPException(404,'Versão anterior não encontrada.')
    if old and db.scalar(select(EegDocument.id).where(EegDocument.predecessor_id==old.id)): raise DomainError(409,'versao_substituida','Substitua a versão vigente.')
    data=arquivo.file.read(MAX_TECHNICAL_BYTES+1);meta=ManualNanoAdapter().validate_export(data,Path(arquivo.filename or '').suffix,arquivo.content_type or '')
    d=EegDocument(id=new_uuid(),titulo=titulo,categoria=categoria,validade=validade,predecessor_id=predecessor_id,storage_path='',sha256=meta['sha256'],size_bytes=meta['size_bytes'],mime_type=meta['mime_type'],extensao=meta['extensao'],criado_por=user.id)
    root=get_settings().resolved_reports_storage_dir();path=storage_path(root,d.id,d.id,d.extensao,documents=True)
    atomic_write_new_file(path,data,root=root);d.storage_path=str(path.relative_to(root));db.add(d);db.flush()
    event(db,request,user,'documento_registrado','eeg_documents',d.id,sha256=d.sha256,size_bytes=d.size_bytes)
    db.commit();return {'id':d.id}


@router.get('/documentos/{did}/conteudo')
def document_download(did: str,request: Request,db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    d=db.get(EegDocument,did)
    if not d: raise HTTPException(404,'Documento EEG não encontrado.')
    try: data=read_checked(get_settings().resolved_reports_storage_dir(),d.storage_path,d.sha256,d.size_bytes)
    except (OSError,ReportStorageError): raise HTTPException(404,'Arquivo indisponível.') from None
    event(db,request,user,'documento_baixado','eeg_documents',d.id);db.commit()
    return Response(data,media_type=d.mime_type,headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="documento-eeg-{d.id}.{d.extensao}"','X-Content-Type-Options':'nosniff'})


@router.get('/indicadores')
def indicators(db: Session=Depends(get_db),user: User=Depends(require_eeg_operator)):
    from ..services.followup import today_local
    today=today_local();start=today.replace(day=1)
    es=dict(db.execute(select(EegExam.status,func.count()).group_by(EegExam.status)).all())
    cs=dict(db.execute(select(EegCase.status,func.count()).join(EegExam,EegExam.id==EegCase.exam_id).where(EegExam.status!='cancelado').group_by(EegCase.status)).all())
    revenue=db.scalar(select(func.sum(FinancialEntry.valor)).where(FinancialEntry.eeg_exam_id.is_not(None),FinancialEntry.data_competencia>=start,FinancialEntry.data_competencia<=today))
    return {'fonte':'PostgreSQL','periodo_inicio':start,'periodo_fim':today,'fuso':'America/Sao_Paulo',
        'agendados':es.get('agendado',0)+es.get('confirmado',0),'realizados':sum(v for k,v in es.items() if k in eeg_flow.EXAME_JA_REALIZADO),
        'aguardando_tracado':db.scalar(select(func.count()).select_from(EegExam).outerjoin(EegCase,EegCase.exam_id==EegExam.id).where(EegExam.status.in_(tuple(eeg_flow.EXAME_JA_REALIZADO)),or_(EegCase.id.is_(None),EegCase.status=='aguardando_arquivo'))),
        'aguardando_laudo':sum(cs.get(s,0) for s in ('pronto_laudo','atribuido','em_elaboracao')),
        'aguardando_assinatura':cs.get('aguardando_assinatura',0),'prontos_entrega':cs.get('pronto_entrega',0),
        'receita_competencia':str(revenue or Decimal('0.00')),'tarefas_pendentes':db.scalar(select(func.count()).select_from(EegActivity).where(EegActivity.tipo=='tarefa',EegActivity.status.in_(['pendente','em_andamento'])))}


@router.get('/fontes')
def sources(db: Session=Depends(get_db),user: User=Depends(require_eeg_access)):
    db.execute(text('SELECT 1'))
    settings=get_settings();root=settings.resolved_reports_storage_dir()
    import os
    storage_ok=root.is_dir() and os.access(root,os.R_OK|os.W_OK) and not root.is_symlink()
    return {'verificado_em':now(),'itens':[{'nome':'PostgreSQL','status':'ok'},
        {'nome':'Storage Life privado','status':'disponivel' if storage_ok else 'indisponivel'},
        {'nome':'Portal EEG','status':'configurado' if settings.portal_enabled and settings.portal_eeg_public_base_url else 'aguardando_configuracao'},
        {'nome':'Fiscal EEG','status':'Configuração fiscal EEG pendente de aprovação'},
        {'nome':'Marketing · eeglife.com.br','status':'Aguardando integração'},
        {'nome':'Nano EEG iCelera','status':'Importação manual · sem API disponível'}]}


@router.get('/eventos')
def events(db: Session=Depends(get_db),user: User=Depends(require_eeg_operator),params: PageParams=Depends()):
    return paginate(db,select(AuditLog).where(AuditLog.acao.like('eeg.%')).order_by(AuditLog.ts_utc.desc()),params,
        lambda a:{'id':a.id,'acao':a.acao,'quando':a.ts_utc,'entidade':a.entidade,'entidade_id':a.entidade_id,'responsavel_id':a.user_id})
