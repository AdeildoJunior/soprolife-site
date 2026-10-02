"""Indicadores reais do banco sintético: isolamento, versões e datas."""
import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from app.models import Person, PhysicianProfile, ReportDocument, SpirometryExam


def _module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = _module('test_m25_2_native_report')
corrections = _module('test_m26_13_laudos_efetivos_producao')
reports_enabled = native.reports_enabled
case = native.case
URL = '/api/v1/laudos/estatisticas'


def _get(client, headers, **params):
    result = client.get(URL, headers=headers, params=params)
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    return result.json()


def _value(data, chart, key):
    return next(x['quantidade'] for x in data[chart] if x['chave'] == key)


def test_conclusao_publicada_uma_vez_sem_previa_e_sem_pii(client, auth, case):
    first = _get(client, auth('admin'))
    assert first['totais']['laudos'] == 1
    assert first['totais']['com_conclusao'] == 0
    assert native._preview(client, case).status_code == 200
    assert native._preview(client, case, conclusion_code='NORMAL').status_code == 200
    assert _get(client, auth('admin'))['totais']['com_conclusao'] == 0
    corrections._liberar(client, case, conclusion_code='DVO_MODERADO')
    data = _get(client, auth('admin'))
    assert data['totais']['laudos'] == data['totais']['com_conclusao'] == 1
    assert _value(data, 'resultados', 'obstrutivo') == 1
    assert _value(data, 'conclusoes', 'DVO_MODERADO') == 1
    assert sum(x['quantidade'] for x in data['broncodilatador']) == 1
    for private in ('patient', 'nome_completo', 'person_id', 'document_id', 'cpf', 'storage_path', 'interpretation_text_snapshot'):
        assert private not in str(data)
    assert case['document']['id'] not in str(data)


def test_corretiva_substitui_sem_duplicar_resultado(client, auth, case):
    corrections._liberar(client, case, conclusion_code='NORMAL')
    corrected = corrections._abrir_corretiva(client, case)
    data = _get(client, auth('admin'))
    assert data['totais']['laudos'] == 1
    assert data['totais']['com_conclusao'] == 0
    corrections._liberar(client, {**case, 'document': corrected}, conclusion_code='DVO_GRAVE')
    data = _get(client, auth('admin'))
    assert data['totais']['laudos'] == 1
    assert _value(data, 'resultados', 'normal') == 0
    assert _value(data, 'conclusoes', 'DVO_GRAVE') == 1


def test_recorte_medico_e_permissoes(client, auth, case, db, person):
    other = native._make_case(client, auth, db, person, suffix='002')
    corrections._liberar(client, case, conclusion_code='NORMAL')
    corrections._liberar(client, other, conclusion_code='DVO_LEVE')
    mine = _get(client, case['doctor_auth'])
    assert mine['escopo'] == 'meus_laudos'
    assert mine['totais']['laudos'] == 1
    assert _value(mine, 'resultados', 'normal') == 1
    assert _value(mine, 'resultados', 'obstrutivo') == 0
    assert _get(client, auth('admin'))['totais']['laudos'] == 2
    for role in ('leitura', 'operacional', 'gestor'):
        assert client.get(URL, headers=auth(role)).status_code == 403
    assert client.get(URL).status_code == 401
    profile = db.get(PhysicianProfile, case['profile']['id'])
    profile.active = False
    db.commit()
    assert client.get(URL, headers=case['doctor_auth']).status_code == 403


def test_arquivados_ficam_fora(client, auth, case, db, person):
    patient = db.get(Person, person['id'])
    patient.arquivado = True
    patient.arquivado_em = datetime.now(timezone.utc)
    patient.arquivado_motivo = 'Cenário sintético'
    db.commit()
    assert _get(client, auth('admin'))['totais']['laudos'] == 0
    assert _get(client, case['doctor_auth'])['totais']['laudos'] == 0


def test_datas_filtros_e_idade_sem_inventar_precisao(client, auth, case, db, person):
    exam = db.get(SpirometryExam, case['exam']['id'])
    patient = db.get(Person, person['id'])
    patient.data_nascimento = date(2008, 7, 16)
    patient.sexo = 'feminino'
    exam.data_exame = date(2026, 7, 15)
    exam.data_exame_precisao = 'dia'
    db.commit()
    data = _get(client, auth('admin'), inicio='2026-07-15', fim='2026-07-15')
    assert data['totais']['laudos'] == 1
    assert _value(data, 'faixa_etaria', 'Até 17 anos') == 1
    assert _value(data, 'sexo', 'feminino') == 1
    assert _get(client, auth('admin'), inicio='2026-07-16')['totais']['laudos'] == 0
    origin = db.get(ReportDocument, case['document']['id']).origin_type
    assert _get(client, auth('admin'), origem=origin)['totais']['laudos'] == 1
    assert _get(client, auth('admin'), origem='pastore')['totais']['laudos'] == 0
    exam.data_exame_precisao = 'mes'
    exam.data_exame_dia_assumido = True
    db.commit()
    data = _get(client, auth('admin'))
    assert data['totais']['sem_data_completa'] == 1
    assert data['evolucao'] == []
    assert _value(data, 'faixa_etaria', 'Não informado') == 1
    assert _get(client, auth('admin'), inicio='2026-01-01')['totais']['laudos'] == 0
    for params in ({'inicio':'2026-08-01','fim':'2026-07-01'}, {'origem':'inventada'}, {'inicio':'inválido'}):
        assert client.get(URL, params=params, headers=auth('admin')).status_code == 422


def test_mais_de_200_registros_e_meses_sem_producao(client, auth, db, person, users):
    for i in range(205):
        exam = SpirometryExam(public_code=f'ESP-{i+1:06d}', person_id=person['id'],
            data_exame=date(2026, 1 if i < 204 else 3, 1), data_exame_precisao='dia')
        db.add(exam)
        db.flush()
        db.add(ReportDocument(public_code=f'LAU-{i+1:06d}', spirometry_exam_id=exam.id,
            status='atribuido', created_by_user_id=users['admin'].id))
    db.commit()
    data = _get(client, auth('admin'))
    assert data['totais']['laudos'] == 205
    assert data['evolucao'] == [{'mes':'2026-01','quantidade':204}, {'mes':'2026-02','quantidade':0}, {'mes':'2026-03','quantidade':1}]
    assert data['totais']['sem_conclusao_publicada'] == 205


def test_feature_gate_e_nao_escreve(client, auth, case, db, monkeypatch):
    from app.config import get_settings
    from app.models import AuditLog
    before = len(db.execute(select(AuditLog)).all())
    _get(client, auth('admin'))
    assert len(db.execute(select(AuditLog)).all()) == before
    assert not db.new and not db.dirty and not db.deleted
    monkeypatch.setenv('M15_REPORTS_ENABLED', 'false')
    get_settings.cache_clear()
    assert client.get(URL, headers=auth('admin')).status_code == 503
