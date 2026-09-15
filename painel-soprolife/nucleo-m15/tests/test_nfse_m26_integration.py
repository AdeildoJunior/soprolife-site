"""NFS-e foundation integrated over M26.9/M26.10.

Physician remuneration (physician_transfers) and the fiscal queue coexist in
the same schema. This file proves the boundary between them: the fiscal amount
comes only from the exam's own-revenue FinancialEntry, never from the
physician's unit price, reference total or paid amount, and the fiscal flow
never writes into physician_transfers or financial_entries.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, func

from app.models import FinancialEntry, PhysicianProfile, PhysicianTransfer
from app.services import nfse
from tests.conftest import _make_user
from tests.test_nfse_foundation import policy_payload, issue, settings, source

ROOT = Path(__file__).resolve().parents[1]


def _physician_transfer(db, competencia, unit_amount, count=1):
    user = _make_user(db, 'medica-fiscal@teste.local', 'medico')
    profile = PhysicianProfile(user_id=user.id, professional_name='Dra. Sintética Fiscal',
                               crm_number='926900', crm_state='RJ', active=True,
                               verification_status='pending')
    db.add(profile)
    db.flush()
    transfer = PhysicianTransfer(physician_profile_id=profile.id, competencia=competencia,
                                 eligible_report_count=count, unit_amount=unit_amount,
                                 reference_total=unit_amount * count,
                                 created_by_user_id=user.id)
    db.add(transfer)
    db.commit()
    return transfer


def test_physician_transfer_never_becomes_fiscal_amount(db, users, source, settings):
    exam, revenue = source
    # M26.9 physician payable for the same competence as the service, with a
    # unit price deliberately larger than the SoproLife revenue.
    transfer = _physician_transfer(db, date(2026, 8, 1), Decimal('300.00'))
    # Ledger repasse and expense linked to the SAME exam.
    db.add(FinancialEntry(public_code='LAN-REPASSE', tipo='repasse', categoria='Repasse ao médico',
                          valor=Decimal('300.00'), status='Pago', spirometry_exam_id=exam.id))
    db.add(FinancialEntry(public_code='LAN-DESPESA', tipo='despesa', categoria='Custo do exame',
                          valor=Decimal('40.00'), status='Pago', spirometry_exam_id=exam.id))
    db.commit()
    nfse.create_policy(db, policy_payload(), users['admin'].id)

    data = nfse.evaluate(db, exam.id, 'mock')
    assert data['blocking_reasons'] == []
    assert data['financial_entry_id'] == revenue.id
    # Gross own revenue, not the physician price and not revenue minus payable.
    assert data['amount_snapshot'] == Decimal('123.45')
    assert data['amount_snapshot'] != transfer.unit_amount
    assert data['amount_snapshot'] != Decimal('123.45') - transfer.unit_amount

    # Marking the physician as paid changes nothing on the fiscal side.
    transfer.status, transfer.paid_amount = 'Pago', Decimal('300.00')
    transfer.payment_date = date(2026, 9, 5)
    transfer.payment_registered_by_user_id = users['admin'].id
    transfer.payment_registered_at = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    db.commit()
    again = nfse.evaluate(db, exam.id, 'mock')
    assert again['financial_entry_id'] == revenue.id
    assert again['amount_snapshot'] == Decimal('123.45')


def test_fiscal_flow_writes_neither_ledger_nor_physician_transfers(db, users, source, settings):
    exam, revenue = source
    transfer = _physician_transfer(db, date(2026, 8, 1), Decimal('300.00'))
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    ledger_before = db.scalar(select(func.count()).select_from(FinancialEntry))
    transfers_before = db.scalar(select(func.count()).select_from(PhysicianTransfer))
    snapshot = (transfer.status, transfer.paid_amount, transfer.reference_total)

    doc = nfse.prepare(db, exam.id, settings, users['gestor'].id)
    doc = issue(db, users, settings, doc)
    assert doc.state == 'simulated'
    prep = nfse.latest_preparation(db, doc.id)
    assert prep.financial_entry_id == revenue.id and prep.amount_snapshot == Decimal('123.45')

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == ledger_before
    assert db.scalar(select(func.count()).select_from(PhysicianTransfer)) == transfers_before
    fresh = db.get(PhysicianTransfer, transfer.id)
    assert (fresh.status, fresh.paid_amount, fresh.reference_total) == snapshot
    assert db.get(FinancialEntry, revenue.id).valor == Decimal('123.45')


def test_only_physician_payable_without_revenue_is_blocked(db, users, source, settings):
    exam, revenue = source
    _physician_transfer(db, date(2026, 8, 1), Decimal('300.00'))
    db.add(FinancialEntry(public_code='LAN-REPASSE', tipo='repasse', categoria='Repasse ao médico',
                          valor=Decimal('300.00'), status='Pago', spirometry_exam_id=exam.id))
    db.delete(revenue)
    db.commit()
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    data = nfse.evaluate(db, exam.id, 'mock')
    assert 'financial_entry_missing' in data['blocking_reasons']
    assert data['financial_entry_id'] is None and data['amount_snapshot'] is None


def test_alembic_graph_fiscal_after_m26_9(tmp_path, monkeypatch):
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', f'sqlite:///{tmp_path}/graph.db')
    script = ScriptDirectory.from_config(cfg)
    # M27 added one migration (9c310c422ce2, fiscal_artifacts) on top of the
    # M26.10 head, M29 added one more (ba3afa480112, national_dps_configurations)
    # on top of that, M31 added one more (a58f6c31d9e7, service_location) on
    # top of that, and M36 added one more (3a97d7535a49, durable dps
    # numbering) on top of that — this now confirms THAT chain, not a fork.
    assert script.get_heads() == ['3a97d7535a49']
    assert script.get_revision('3a97d7535a49').down_revision == 'a58f6c31d9e7'
    assert script.get_revision('a58f6c31d9e7').down_revision == 'ba3afa480112'
    assert script.get_revision('ba3afa480112').down_revision == '9c310c422ce2'
    assert script.get_revision('9c310c422ce2').down_revision == 'f6a1d9e28b40'
    assert script.get_revision('f6a1d9e28b40').down_revision == 'd6a9f20c3e41'
    assert script.get_revision('d6a9f20c3e41').down_revision == 'c3a9e15f7d84'
    ids = [r.revision for r in script.walk_revisions()]
    assert len(ids) == len(set(ids))
