"""Fluxos reais com People/API/storage e identidades exclusivamente sintéticas."""
from io import BytesIO
from zipfile import ZipFile
import json
from pathlib import Path
import pytest
from sqlalchemy import select, func, text
from app.models import EegExam, EegCase, EegCaseRevision, EegTechnicalFile, FinancialEntry, EegProfessional, AuditLog
from app.security import issue_token
from app.services.eeg_import import ManualNanoAdapter, MAX_TECHNICAL_BYTES
from .conftest import _make_user
from .test_fase2a_eeg_operacao import storage, _pdf

API='/api/v1/eeg'

@pytest.fixture
def eeg_roles(db):
    result={}
    for role in ('eeg_operador','eeg_tecnico','medical_eeg','eeg_financeiro'):
        u=_make_user(db,role+'@example.invalid',role)
        result[role]=(u.id,{'Authorization':'Bearer '+issue_token(u.id,u.password_hash)})
    return result


def create(client,auth):
    v=client.post(API+'/pessoas',headers=auth(),json={'nome_completo':'Paciente Operacional Sintetico','data_nascimento':'2000-02-03'}).json()
    p={'enrollment_id':v['id'],'modalidade_codigo':'vigilia','agendado_para':'2026-10-20T10:00:00-03:00',
       'local_atendimento':'Local Sintetico','municipio_atendimento_ibge':'3304557','origem_canal':'site',
       'valor':'250.00','idempotency_key':'eeg-operational-complete-001'}
    r=client.post(API+'/atendimentos',headers=auth(),json=p);assert r.status_code==201,r.text
    e=r.json();r=client.patch(API+'/exames/'+e['id']+'/status',headers=auth(),json={'status':'realizado'})
    assert r.status_code==200,r.text
    return v,e,p


def upload(client,headers,e,data=None,name='tecnico.pdf',mime='application/pdf',code=None):
    return client.post(API+'/exames/'+e['id']+'/importar',headers=headers,
        data={'confirmacao_codigo':code or e['public_code']},files={'arquivo':(name,data or _pdf(),mime)})


def step(client,headers,e,action):
    return client.post(API+'/exames/'+e['id']+'/fluxo',headers=headers,json={'acao':action,'confirmacao_codigo':e['public_code']})


def test_rbac_separacao_financeiro_medico_operador(client,auth,eeg_roles):
    v,e,p=create(client,auth)
    op=eeg_roles['eeg_operador'][1];tech=eeg_roles['eeg_tecnico'][1];med=eeg_roles['medical_eeg'][1];fin=eeg_roles['eeg_financeiro'][1]
    assert client.get(API+'/financeiro',headers=fin).status_code==200
    assert client.get(API+'/financeiro',headers=op).status_code==403
    assert client.get(API+'/pessoas',headers=fin).status_code==403
    assert client.get(API+'/exames',headers=fin).status_code==403
    assert client.get(API+'/laudos',headers=med).json()['total']==0
    assert upload(client,op,e).status_code==403
    assert upload(client,tech,e,code='EEG-999999').status_code==422
    r=upload(client,tech,e);assert r.status_code==201,r.text
    file_id=r.json()['arquivo_id']
    assert client.get(API+'/arquivos/'+file_id+'/conteudo',headers=med).status_code==403
    assert client.post(API+'/exames/'+e['id']+'/redacao',headers=auth(),json={'tecnica':'Tecnica sintetica','descricao':'Descricao sintetica','conclusao':'Conclusao sintetica'}).status_code==403
    assert step(client,op,e,'conferir').status_code==403


def test_medico_redige_conclui_pdf_assinatura_retorno_entrega(client,auth,eeg_roles,db):
    v,e,p=create(client,auth);med_id,med=eeg_roles['medical_eeg'];tech=eeg_roles['eeg_tecnico'][1]
    r=client.post(API+'/profissionais',headers=auth(),json={'nome':'Neurologista Sintetico','crm':'900002','uf':'RJ','user_id':med_id,'tipo':'interno','referencia_verificacao':'SYNTH-VERIFY-002'})
    assert r.status_code==201,r.text
    professional=r.json()
    assert client.post(API+'/exames/'+e['id']+'/atribuir',headers=auth(),json={'professional_id':professional['id']}).status_code==409
    assert upload(client,tech,e).status_code==201
    assert step(client,tech,e,'conferir').status_code==200
    assert client.post(API+'/exames/'+e['id']+'/atribuir',headers=auth(),json={'professional_id':professional['id']}).status_code==200
    assert client.get(API+'/laudos',headers=med).json()['total']==1
    body={'tecnica':'Técnica informada pelo médico sintético','descricao':'Descrição clínica sintética sem interpretação automática','conclusao':'Conclusão exclusivamente sintética'}
    assert client.post(API+'/exames/'+e['id']+'/redacao',headers=auth(),json=body).status_code==403
    r=client.post(API+'/exames/'+e['id']+'/redacao',headers=med,json=body);assert r.status_code==200,r.text
    assert step(client,auth(),e,'concluir').status_code==403
    r=step(client,med,e,'concluir');assert r.status_code==200,r.text
    assert r.json()['status']=='aguardando_assinatura'
    document_id=r.json()['final_document_id']
    pdf=client.get(API+'/laudos/'+document_id+'/arquivo',headers=med)
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
    from pypdf import PdfReader
    content=''.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
    assert 'eletroencefalografia' in content and 'Neurologista Sintetico' in content
    assert 'broncodilatador' not in content.lower()
    assert step(client,auth(),e,'publicar').status_code==409
    assert upload(client,tech,e).status_code==409
    assert client.post(API+'/exames/'+e['id']+'/redacao',headers=med,json=body).status_code==409
    received=client.post(API+'/exames/'+e['id']+'/laudo',headers=auth(),data={'assinatura_recebida':'true','confirmacao_codigo':e['public_code']},files={'arquivo':('assinado.pdf',_pdf(2),'application/pdf')})
    assert received.status_code==201,received.text
    assert received.json()['fluxo']['status']=='recebido_assinado'
    assert step(client,auth(),e,'publicar').status_code==200
    assert step(client,auth(),e,'entregar').json()['status']=='entregue'
    logs=list(db.scalars(select(AuditLog)))
    assert not any(body['descricao'] in json.dumps(a.detalhes) for a in logs)
    assert db.scalar(select(func.count()).select_from(EegCaseRevision))==1
    db.rollback()


def test_arquivo_substitui_sem_apagar_invalidando_conferencia(client,auth,db):
    v,e,p=create(client,auth)
    a=upload(client,auth(),e);assert a.status_code==201,a.text
    assert step(client,auth(),e,'conferir').status_code==200
    b=upload(client,auth(),e,_pdf(2));assert b.status_code==201,b.text
    assert a.json()['arquivo_id']!=b.json()['arquivo_id']
    assert b.json()['status']=='aguardando_conferencia'
    rows=client.get(API+'/exames/'+e['id']+'/arquivos',headers=auth()).json()['itens']
    assert len(rows)==2
    for row in rows:
        r=client.get(API+'/arquivos/'+row['id']+'/conteudo',headers=auth());assert r.status_code==200
    f=db.get(EegTechnicalFile,b.json()['arquivo_id'])
    from app.config import get_settings
    path=get_settings().resolved_reports_storage_dir()/f.object_key
    db.rollback();path.write_bytes(b'corrompido');path.chmod(0o600)
    assert client.get(API+'/arquivos/'+f.id+'/conteudo',headers=auth()).status_code==409


@pytest.mark.parametrize('name,mime,data',[('x.exe','application/octet-stream',b'MZ'),('x.pdf','application/pdf',b'MZ'),('x.edf','application/octet-stream',b'invalido'),('x.zip','application/pdf',b'PK\x03\x04')])
def test_upload_allowlist(client,auth,name,mime,data):
    v,e,p=create(client,auth)
    assert upload(client,auth(),e,data,name,mime).status_code==422


@pytest.mark.parametrize('name',['../tracado.edf','/etc/passwd','a\\b.edf','C:evil.edf','run.exe'])
def test_zip_traversal_e_executavel_recusados(name):
    out=BytesIO()
    with ZipFile(out,'w') as z:z.writestr(name,b'synthetic')
    with pytest.raises(Exception):ManualNanoAdapter().validate_export(out.getvalue(),'zip','application/zip')


def test_edf_envelope_zip_valido_e_limite():
    data=bytearray(b' '*512);data[:8]=b'0       ';data[184:192]=b'512     ';data[252:256]=b'1   '
    assert ManualNanoAdapter().validate_export(bytes(data),'edf','application/octet-stream')['size_bytes']==512
    out=BytesIO()
    with ZipFile(out,'w') as z:z.writestr('export/tracado.bin',b'synthetic nano export')
    assert ManualNanoAdapter().validate_export(out.getvalue(),'zip','application/zip')['extensao']=='zip'
    with pytest.raises(Exception):ManualNanoAdapter().validate_export(b'a'*(MAX_TECHNICAL_BYTES+1),'edf','application/octet-stream')


def test_crm_sem_seis_meses_contato_consentimento_e_cadastro(client,auth):
    v,e,p=create(client,auth)
    r=client.get(API+'/crm',headers=auth()).json()['itens'][0]
    assert r['proximo_contato'] is None and r['whatsapp_url'] is None
    r=client.patch(API+'/pessoas/'+v['id']+'/cadastro',headers=auth(),json={'telefone':'(21) 0000-0011','consentimento_whatsapp':'concedido','nome_completo':'Pessoa Corrigida Sintetica'})
    assert r.status_code==200,r.text
    r=client.get(API+'/crm',headers=auth()).json()['itens'][0];assert r['whatsapp_url']=='https://wa.me/552100000011'
    assert client.patch(API+'/crm/'+v['id'],headers=auth(),json={'proximo_contato':'2026-11-03','situacao':'acompanhamento'}).status_code==200
    assert client.get(API+'/crm',headers=auth()).json()['itens'][0]['proximo_contato']=='2026-11-03'
    corporate=client.get('/api/v1/pessoas/'+v['pessoa']['id'],headers=auth()).json()
    assert corporate['nome_completo']=='Pessoa Corrigida Sintetica'
    history=client.get(API+'/pessoas/'+v['id']+'/historico',headers=auth()).json()
    assert [x['id'] for x in history['exames']]==[e['id']]


def test_lead_conversao_idempotente_tarefa_sem_papel_invalido(client,auth,eeg_roles):
    v,e,p=create(client,auth)
    body={'tipo':'lead','titulo':'Oportunidade Sintetica','categoria':'oportunidade','status':'interessado','origem':'site'}
    a=client.post(API+'/atividades',headers=auth(),json=body);assert a.status_code==201,a.text
    aid=a.json()['id']
    for _ in range(2):assert client.post(API+'/atividades/'+aid+'/converter',headers=auth(),json={'enrollment_id':v['id']}).status_code==200
    assert client.post(API+'/atividades/'+aid+'/converter',headers=auth(),json={'enrollment_id':v['id'],'exam_id':e['id']}).status_code==200
    changed={**body,'status':'faltou','enrollment_id':v['id'],'exam_id':e['id']}
    assert client.patch(API+'/atividades/'+aid,headers=auth(),json=changed).json()['status']=='faltou'
    assert client.post(API+'/atividades/'+aid+'/converter',headers=auth(),json={'enrollment_id':v['id'],'exam_id':e['id']}).json()['status']=='faltou'
    body={'tipo':'tarefa','titulo':'Confirmar agenda sintetica','categoria':'confirmar_agenda','status':'pendente','responsavel_id':eeg_roles['medical_eeg'][0]}
    assert client.post(API+'/atividades',headers=auth(),json=body).status_code==422
    body['responsavel_id']=eeg_roles['eeg_operador'][0]
    assert client.post(API+'/atividades',headers=auth(),json=body).status_code==201


def test_reteste_sem_receita_nova_e_financeiro_segregado(client,auth,db):
    v,e,p=create(client,auth)
    p.update(idempotency_key='eeg-operational-retest-0002',reteste_sem_cobranca=True,valor='0')
    a=client.post(API+'/atendimentos',headers=auth(),json=p);assert a.status_code==201,a.text
    b=client.post(API+'/atendimentos',headers=auth(),json=p);assert b.json()['id']==a.json()['id']
    assert a.json()['fluxo']['reteste_sem_cobranca'] is True
    assert client.get(API+'/financeiro',headers=auth()).json()['total']==1
    assert client.get('/api/v1/lancamentos',headers=auth()).json()['total']==0


def test_documento_privado_versionado_e_preparo_sem_aprovacao(client,auth,eeg_roles):
    v,e,p=create(client,auth)
    data={'titulo':'Contrato Sintetico','categoria':'contratos'}
    a=client.post(API+'/documentos',headers=auth(),data=data,files={'arquivo':('contrato.pdf',_pdf(),'application/pdf')});assert a.status_code==201,a.text
    aid=a.json()['id'];data['predecessor_id']=aid
    b=client.post(API+'/documentos',headers=auth(),data=data,files={'arquivo':('contrato.pdf',_pdf(2),'application/pdf')});assert b.status_code==201,b.text
    assert client.get(API+'/documentos/'+aid+'/conteudo',headers=auth()).status_code==200
    assert client.get(API+'/documentos/'+aid+'/conteudo',headers=eeg_roles['medical_eeg'][1]).status_code==403
    assert len(client.get(API+'/documentos',headers=auth()).json()['itens'])==2
    assert all(p['status']=='aguardando_aprovacao' for p in client.get(API+'/preparos',headers=auth()).json()['itens'])
    prep={'modalidade_codigo':'vigilia','texto':'Texto de preparo sintético aprovado para teste.','referencia_aprovacao':'SYNTH-PREP-001'}
    assert client.post(API+'/preparos',headers=auth(),json=prep).status_code==403
    assert client.post(API+'/preparos',headers=eeg_roles['medical_eeg'][1],json=prep).status_code==201


def test_dashboard_fontes_eventos_e_parceiros(client,auth):
    v,e,p=create(client,auth)
    r=client.get(API+'/indicadores',headers=auth());assert r.status_code==200,r.text
    assert r.json()['realizados']==1 and r.json()['aguardando_tracado']==1
    sources=client.get(API+'/fontes',headers=auth()).json()['itens']
    assert any('Importação manual' in s['status'] for s in sources)
    events=client.get(API+'/eventos',headers=auth()).json()['itens']
    assert events and not any('detalhes' in e for e in events)
    r=client.post(API+'/parceiros',headers=auth(),json={'nome':'Hospital Sintetico','categoria':'hospital','unidade':'Unidade Sintetica'})
    assert r.status_code==201,r.text
    pid=r.json()['partner_id']
    assert client.get(API+'/parceiros/corporativos',headers=auth()).json()['itens'][0]['id']==pid
    for _ in range(2):assert client.post(API+'/parceiros',headers=auth(),json={'partner_id':pid,'categoria':'hospital','unidade':'Unidade Sintetica'}).status_code==201
    assert client.get(API+'/parceiros',headers=auth()).json()['total']==1


def test_sql_exception_does_not_format_clinical_parameters():
    from app.db import build_engine
    from sqlalchemy.exc import DBAPIError
    engine=build_engine('sqlite://')
    with engine.connect() as conn:
        with pytest.raises(DBAPIError) as caught:
            conn.execute(text('SELECT * FROM synthetic_missing WHERE field=:private'),{'private':'conteudo-clinico-sintetico-nao-logar'})
    assert 'conteudo-clinico-sintetico-nao-logar' not in str(caught.value)
    engine.dispose()


def test_legacy_published_exam_cannot_replace_trace_without_review(client,auth,db):
    v,e,p=create(client,auth)
    row=db.get(EegExam,e['id'])
    row.status='laudo_disponivel';db.commit()
    assert upload(client,auth(),e).status_code==409


def test_external_reference_cannot_shadow_private_trace(client,auth):
    v,e,p=create(client,auth)
    assert upload(client,auth(),e).status_code==201
    r=client.post(API+'/exames/'+e['id']+'/arquivo-tecnico',headers=auth(),json={'armazenamento_origem':'equipamento'})
    assert r.status_code==409
