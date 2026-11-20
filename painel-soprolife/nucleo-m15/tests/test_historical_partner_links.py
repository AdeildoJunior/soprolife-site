"""Legacy identity repair never rewrites clinical/monetary/fiscal facts."""
from copy import deepcopy
from decimal import Decimal
import pytest
from sqlalchemy import select
from app.db import Base
from app.models import (AuditLog, FinancialEntry, SpirometryExam, FiscalDocument,
    ReportDocument, ProfitRevenueBasis, Partner, PartnerUnit)
from app.services.historical_partner_links import apply_plan, digest, make_plan
from app.services import partnership_center as pc
from tests.test_partnership_center import canonical, exam, DAY


def snapshot(db):
    db.flush()
    return {t.name: [dict(r) for r in db.execute(select(t).order_by(*t.primary_key.columns)).mappings()]
        for t in Base.metadata.sorted_tables}


def legacy(db, canonical, users, number=59, partner='maclin', amount='123.45'):
    e, _ = exam(db, canonical, partner, number, amount, share=False, users=users)
    e.partner_id = e.partner_unit_id = None
    e.origem = pc.LABELS[partner]
    db.commit()
    return e


def test_legacy_cents_exact_only_structural_columns(db, canonical, users):
    e = legacy(db, canonical, users)
    db.add(FiscalDocument(spirometry_exam_id=e.id, environment='mock',
        created_by=users['admin'].id, idempotency_key='synthetic-link-fiscal',
        idempotency_fingerprint='x'*64))
    db.add(ReportDocument(public_code='LAU-SYN-LINK', spirometry_exam_id=e.id,
        created_by_user_id=users['admin'].id, origin_label='Maclin'))
    db.commit()
    before = snapshot(db)
    plan = make_plan(db, [{'exam_code': e.public_code, 'partner_code': 'maclin'}])
    assert snapshot(db) == before  # dry-run physically performs no write
    results = apply_plan(db, plan, digest(plan), users['admin'].id)
    db.commit()
    after = snapshot(db)
    assert results[0]['changed']
    for table, rows in before.items():
        if table not in ('spirometry_exams', 'audit_logs'):
            assert after[table] == rows, table
    a, b = before['spirometry_exams'][0], after['spirometry_exams'][0]
    assert {k for k in a if a[k] != b[k]} == {'partner_id', 'partner_unit_id'}
    assert db.scalar(select(FinancialEntry.valor)) == Decimal('123.45')
    assert after['exam_partner_shares'] == [] and after['partner_settlement_items'] == []
    event = db.scalar(select(AuditLog).where(AuditLog.acao == 'espirometria.vinculo_historico_corrigido'))
    assert event.detalhes['previous_partner_id'] is None
    assert event.detalhes['partner_id'] == canonical['maclin']['partner_id']
    # Idempotent repeat neither changes facts nor adds audit events.
    assert not apply_plan(db, plan, digest(plan), users['admin'].id)[0]['changed']
    db.commit()
    assert snapshot(db) == after
    data, rows = pc.project(db, True, DAY.replace(day=1), DAY)
    assert data['consolidado']['exames'] == 1
    assert rows['maclin'][0]['share_id'] is None
    assert rows['maclin'][0]['soprolife'] is None
    assert rows['maclin'][0]['a_pagar'] is None


def test_existing_partner_only_fills_unit_and_alergolife_untouched(db, canonical, users):
    e, _ = exam(db, canonical, 'alergocare', share=False, users=users)
    e.partner_unit_id = None; e.origem = 'Alergocare'; db.commit()
    other = Partner(public_code='CLI-SYN-LIFE', nome='Alergo Life'); db.add(other); db.flush()
    unit = PartnerUnit(public_code='UNI-SYN-LIFE', partner_id=other.id, nome='Alergo Life'); db.add(unit); db.flush()
    untouched, _ = exam(db, canonical, 'maclin', 2, share=False, users=users)
    untouched.partner_id = other.id; untouched.partner_unit_id = unit.id
    untouched.local_atendimento = untouched.origem = 'Alergo Life'
    exam(db, canonical, 'pastore', 3, gross=None, users=users)
    db.commit()
    before = snapshot(db)
    p = make_plan(db, [{'exam_code': e.public_code, 'partner_code': 'alergocare'}])
    apply_plan(db, p, digest(p), users['admin'].id); db.commit()
    after = snapshot(db)
    for a, b in zip(before['spirometry_exams'], after['spirometry_exams']):
        assert {k for k in a if a[k] != b[k]} == ({'partner_unit_id'} if a['id'] == e.id else set())
    assert before['financial_entries'] == after['financial_entries']
    assert before['partners'] == after['partners']
    assert before['partnerships'] == after['partnerships']


def test_conflict_and_alergolife_fail_closed(db, canonical, users):
    e = legacy(db, canonical, users)
    for origin in ('Alergocare', 'Alergo Life'):
        e.origem = origin; db.commit(); before = snapshot(db)
        with pytest.raises(ValueError, match='conflitante'):
            make_plan(db, [{'exam_code': e.public_code, 'partner_code': 'maclin'}])
        assert snapshot(db) == before
    e.origem = 'Maclin'; e.partner_id = canonical['alergocare']['partner_id']; db.commit()
    with pytest.raises(ValueError, match='conflitante'):
        make_plan(db, [{'exam_code': e.public_code, 'partner_code': 'maclin'}])


def test_stale_batch_and_bad_hash_no_partial_writes(db, canonical, users):
    a = legacy(db, canonical, users)
    b = legacy(db, canonical, users, number=69)
    plan = make_plan(db, [{'exam_code': x.public_code, 'partner_code': 'maclin'} for x in (a,b)])
    with pytest.raises(ValueError, match='Hash'):
        apply_plan(db, plan, 'wrong', users['admin'].id)
    b.status = 'Aguardando'; db.commit(); before = snapshot(db)
    with pytest.raises(ValueError, match='mudou'):
        apply_plan(db, plan, digest(plan), users['admin'].id)
    assert snapshot(db) == before
    assert db.get(SpirometryExam, a.id).partner_id is None


def test_only_admin_and_canonical_config_must_remain_same(db, canonical, users):
    e = legacy(db, canonical, users)
    plan = make_plan(db, [{'exam_code': e.public_code, 'partner_code': 'maclin'}])
    for role in ('gestor','operacional','leitura'):
        with pytest.raises(ValueError, match='administrador'):
            apply_plan(db, plan, digest(plan), users[role].id)
    bad = deepcopy(plan); bad['links'][0]['after'] = canonical['alergocare']['partner_id']
    with pytest.raises(ValueError, match='conflitante'):
        apply_plan(db, bad, digest(bad), users['admin'].id)


@pytest.mark.parametrize('number,partner,basis,gross', [
    (59,'maclin','liquido',None), (69,'maclin','liquido','176.36'),
    (72,'maclin','liquido','176.36'), (84,'alergocare','bruto','123.45')])
def test_existing_confirmed_financial_projection_unchanged(db, canonical, users, number, partner, basis, gross):
    from app.services.profit_projection import build_projection
    e, _ = exam(db, canonical, partner, number, share=False, users=users)
    e.partner_unit_id = None
    entry = db.scalar(select(FinancialEntry).where(FinancialEntry.spirometry_exam_id == e.id))
    db.add(ProfitRevenueBasis(spirometry_exam_id=e.id, financial_entry_id=entry.id,
        entry_amount_snapshot=entry.valor, basis=basis, gross_amount=gross,
        evidence_note='Synthetic preexisting evidence', created_by_user_id=users['admin'].id))
    db.commit()
    before = build_projection(db, DAY.replace(day=1), DAY)
    plan = make_plan(db, [{'exam_code':e.public_code,'partner_code':partner}])
    apply_plan(db, plan, digest(plan), users['admin'].id); db.commit()
    assert build_projection(db, DAY.replace(day=1), DAY) == before
