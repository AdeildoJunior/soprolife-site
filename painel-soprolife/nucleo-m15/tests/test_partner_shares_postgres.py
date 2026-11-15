"""PostgreSQL real: concorrência de baixa, imutabilidade e rollback com evidência."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from alembic import command
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.models import Person, User, SpirometryExam, FinancialEntry, PartnerShareMovement, PartnerCommercialRule
from app.routers.partner_shares import ShareMovementCreate
from app.services.partner_share_setup import configure
from app.services.partner_shares import create_share, record_movement
from tests.test_nfse_migrations import postgres_url, config


def test_concurrent_receipts_immutable_and_rollback_safe(postgres_url):
    cfg=config(postgres_url);command.upgrade(cfg,'w141126wa')
    engine=create_engine(postgres_url,hide_parameters=True)
    with Session(engine) as db:
        db.add(User(id='synthetic-admin',email='admin@synthetic.local',nome='Sintético',password_hash='not-a-real-password'))
        db.add(Person(id='synthetic-person',public_code='PES-SYN-PG',nome_completo='Pessoa Sintética',nome_normalizado='pessoa sintetica'))
        db.commit()
    command.upgrade(cfg,'head');command.check(cfg)
    with Session(engine) as db:
        rule=configure(db,'maclin',date(2026,11,9),'synthetic-admin')
        from app.models import Partnership
        partner=db.get(Partnership,rule.partnership_id)
        e=SpirometryExam(public_code='ESP-SYN-PG',person_id='synthetic-person',data_exame=date(2026,11,9),
             partner_id=partner.partner_id,partner_unit_id=rule.partner_unit_id,modalidade=rule.modalidade)
        db.add(e);db.flush()
        f=FinancialEntry(public_code='LAN-SYN-PG',tipo='receita',valor=Decimal(100),status='Recebido',forma_pagamento='Dinheiro',spirometry_exam_id=e.id)
        db.add(f);db.flush()
        s=create_share(db,e,f,'maclin','parceiro','synthetic-admin','test');db.commit();share_id=s.id
    def receive(key):
        with Session(engine) as db:
            payload=ShareMovementCreate(valor='70',natureza='acerto',direcao='entrada',conta='Conta sintética',forma_pagamento='Pix',
                ocorrido_em='2026-11-13T19:28:24-03:00',idempotency_key=key)
            try:
                result=record_movement(db,share_id,payload,'synthetic-admin','test');db.commit();return result
            except HTTPException as e:
                db.rollback();return e.status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(receive,['synthetic-repeat']*12))
    assert all(isinstance(r,dict) and r['a_receber']=='0.00' for r in results)
    assert receive('synthetic-overpayment')==422
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(PartnerShareMovement))==1
        assert db.scalar(select(func.sum(FinancialEntry.valor)).where(FinancialEntry.tipo=='receita'))==Decimal(100)
    for table in ('partner_commercial_rules','exam_partner_shares','partner_share_movements'):
        with engine.begin() as db:
            with pytest.raises(DBAPIError):db.execute(text(f'UPDATE {table} SET id=id'))
    with pytest.raises(RuntimeError,match='evidência'):command.downgrade(cfg,'w141126wa')
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM exam_partner_shares'))==1
    engine.dispose()
