"""Relacionamento sintético: calendário, proveniência e leitura sem efeitos."""
from datetime import date, timedelta
import hashlib
import json

import pytest
from sqlalchemy import select

from app.db import Base
from app.models import Followup, Person, PersonContact, PersonRelationship, SpirometryExam
from app.services import crm
from app.services.crm_schedule import next_contact_date, project_followups


@pytest.mark.parametrize("base,expected", [
    (date(2026, 10, 22), date(2027, 4, 26)),
    (date(2026, 10, 26), date(2027, 4, 26)),
    (date(2026, 8, 31), date(2027, 3, 1)),
    (date(2023, 8, 31), date(2024, 3, 4)),
    (date(2024, 2, 29), date(2024, 9, 2)),
    (date(2026, 12, 31), date(2027, 7, 5)),
])
def test_calendar_months_then_monday(base, expected):
    assert next_contact_date(base) == expected
    assert expected.weekday() == 0


def person(db, code="PES-000001", name="Pessoa Sintética"):
    p = Person(public_code=code, nome_completo=name, nome_normalizado=name.casefold())
    db.add(p)
    db.flush()
    return p


def exam(db, p, code="ESP-000001", day=date(2026, 10, 20), **kw):
    e = SpirometryExam(person_id=p.id, public_code=code, data_exame=day,
                       data_exame_precisao="dia", status="Realizado", **kw)
    db.add(e)
    db.flush()
    return e


def fup(db, p, code="FUP-000001", day=date(2027, 4, 20), e=None, manual=False, **kw):
    f = Followup(person_id=p.id, public_code=code, tipo="pos_exame" if e else "manual",
                 origem_entidade="spirometry_exams" if e else None,
                 origem_id=e.id if e else None, due_date=day, due_date_manual=manual, **kw)
    db.add(f)
    db.flush()
    return f


def snapshot(db):
    # Inclui financeiro, fiscal, laudos, técnicos e relacionamentos, sem expor conteúdo.
    db.flush()
    return {table.name: hashlib.sha256(json.dumps(
        sorted((dict(row) for row in db.execute(select(table)).mappings()), key=lambda r: str(r)),
        default=str, sort_keys=True).encode()).hexdigest() for table in Base.metadata.sorted_tables}


def test_retest_is_latest_even_without_charge_and_read_never_writes(db, monkeypatch):
    monkeypatch.setattr(crm, "today_local", lambda: date(2026, 10, 29))
    p = person(db)
    original = exam(db, p)
    retest = exam(db, p, "ESP-000002", date(2026, 10, 22), reteste_sem_cobranca=True)
    old = fup(db, p, e=original)
    new = fup(db, p, "FUP-000002", date(2027, 4, 22), e=retest)
    db.commit()
    before = snapshot(db)
    rows = crm.build_patient_rows(db, com_financeiro=True)
    queue = crm.build_queue_rows(db)
    assert rows[0]["proximo_contato"]["due_date"] == "2027-04-26"
    assert rows[0]["proximo_contato"]["fonte_data"] == "derivada_exame"
    assert rows[0]["ultima_atividade"] == {"tipo": "Espirometria", "data": "2026-10-22", "precisao": "dia", "dia_assumido": False}
    assert len(queue) == 1
    assert not db.dirty and snapshot(db) == before
    assert old.due_date == date(2027, 4, 20) and new.due_date == date(2027, 4, 22)


def test_manual_followup_prevails_and_unknown_import_is_preserved(db):
    p = person(db)
    e = exam(db, p)
    auto = fup(db, p, e=e)
    manual = fup(db, p, "FUP-000002", date(2027, 8, 10), manual=True)
    views = project_followups([auto, manual], [e], date(2026, 10, 29))
    assert len(views) == 1 and views[0].id == manual.id
    assert views[0].due_date == manual.due_date and views[0].date_source == "manual"
    manual.due_date_manual = False
    manual.origem_entidade = "importacao_multiaba"
    projected = project_followups([manual], [e], date(2026, 10, 29))[0]
    assert projected.date_source == "herdada" and projected.due_date == manual.due_date


def test_past_manual_is_not_assumed_wrong(db):
    p = person(db)
    e = exam(db, p)
    f = fup(db, p, day=date(2026, 10, 22), e=e, manual=True)
    v = project_followups([f], [e], date(2026, 10, 29))[0]
    assert v.due_date == date(2026, 10, 22) and v.date_source == "manual"


@pytest.mark.parametrize("precision,assumed,status", [("mes",False,"Realizado"),("dia",True,"Realizado"),("dia",False,"Aguardando")])
def test_uncertain_or_unperformed_exam_does_not_generate_precise_date(db, precision, assumed, status):
    p = person(db)
    e = exam(db, p)
    e.data_exame_precisao, e.data_exame_dia_assumido, e.status = precision, assumed, status
    f = fup(db, p, e=e)
    v = project_followups([f], [e], date(2026,10,29))[0]
    assert v.due_date == f.due_date and v.date_source == "automatica"


def test_sort_overdue_future_nulls_before_pagination_and_filters(client, auth, db, monkeypatch):
    today = date(2026,10,29)
    monkeypatch.setattr(crm, "today_local", lambda: today)
    cases = [("Z", today+timedelta(days=30)), ("B", None), ("C",today-timedelta(days=3)),
             ("A",today-timedelta(days=10)), ("D",today), ("E",today+timedelta(days=4))]
    for i, (name, day) in enumerate(cases,1):
        p = person(db, f"PES-{i:06}", name)
        fup(db, p, f"FUP-{i:06}", day, manual=True)
    db.commit()
    headers = auth("leitura")
    data = client.post('/api/v1/crm/pacientes/busca', json={"tamanho":2}, headers=headers).json()
    assert [r['nome_completo'] for r in data['itens']] == ['A','C']
    assert data['resumo'] == {"total_pacientes":6,"atrasados":2,"proximos_30":3,"sem_data":1}
    remaining = client.post('/api/v1/crm/pacientes/busca', json={"pagina":3,"tamanho":2}, headers=headers).json()
    assert [r['nome_completo'] for r in remaining['itens']] == ['Z','B']
    filtered = client.post('/api/v1/crm/pacientes/busca',json={"situacao":"sem_data"},headers=headers).json()
    assert [r['nome_completo'] for r in filtered['itens']] == ['B']
    named = client.post('/api/v1/crm/pacientes/busca',json={"ordenar_por":"nome"},headers=headers).json()
    assert [r['nome_completo'] for r in named['itens']] == ['A','B','C','D','E','Z']


def test_related_contact_never_replaces_personal_contact(db):
    p = person(db)
    guardian = person(db, "PES-000002", "Responsável Sintético")
    db.add(PersonContact(person_id=guardian.id, tipo="telefone", valor="(21) 98888-7766", valor_normalizado="5521988887766", principal=True, nao_discavel=False))
    db.add(PersonRelationship(minor_person_id=p.id, guardian_person_id=guardian.id, relationship_type="mae", is_legal_guardian=True))
    fup(db,p,manual=True)
    db.commit()
    row = crm.build_patient_rows(db,com_financeiro=False)[0]
    assert row['contato_pessoal']['telefone_utilizavel'] is False
    assert row['contato']['telefone_utilizavel'] is True
    assert row['contato']['eh_responsavel'] is True
    assert row['contato']['person_id'] == guardian.id


def test_manual_schedule_and_reschedule_are_audited_and_only_relationship_changes(client, auth, db):
    from app.models import AuditLog
    p = person(db)
    exam(db, p, reteste_sem_cobranca=True)
    db.commit()
    pid = p.id
    before = snapshot(db)
    db.rollback()  # libera a transação de leitura do SQLite antes de outra sessão escrever
    response = client.post('/api/v1/followups', headers=auth('operacional'), json={
        'patient_person_id': pid, 'tipo': 'manual', 'due_date': '2027-05-03'})
    assert response.status_code == 201, response.text
    fid = response.json()['id']
    response = client.post('/api/v1/crm/contatos', headers=auth('operacional'), json={
        'followup_id': fid, 'resultado': 'reagendar', 'nova_data': '2027-06-07'})
    assert response.status_code == 201, response.text
    db.expire_all()
    assert db.get(Followup, fid).due_date_manual
    assert db.get(Followup, fid).due_date == date(2027, 6, 7)
    assert db.scalars(select(AuditLog).where(AuditLog.acao == 'followup.criado')).first()
    assert db.scalars(select(AuditLog).where(AuditLog.entidade_id == fid)).first()
    after = snapshot(db)
    allowed = {'followups', 'interactions', 'audit_logs', 'code_sequences'}
    assert {t for t in before if before[t] != after[t]} <= allowed


def test_latest_exam_and_interaction_sorts_are_server_side(client, auth, db):
    from datetime import datetime, timezone
    from app.models import Interaction
    for i in range(1,4):
        p = person(db, f'PES-{i:06}', f'Pessoa {i}')
        exam(db,p,f'ESP-{i:06}',date(2026,10,i))
        db.add(Interaction(person_id=p.id,public_code=f'INT-{i:06}',canal='telefone',ts_utc=datetime(2026,10,4-i,tzinfo=timezone.utc)))
    db.commit()
    for sort,expected in [('ultimo_exame','PES-000003'),('ultima_interacao','PES-000001')]:
        r = client.post('/api/v1/crm/pacientes/busca',headers=auth('leitura'),json={'ordenar_por':sort,'tamanho':1})
        assert r.status_code == 200
        assert r.json()['itens'][0]['public_code'] == expected
