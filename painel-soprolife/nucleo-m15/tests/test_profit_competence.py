"""Regras expressamente aprovadas: competência independente de fiscal e caixa."""
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import func, select
from app.models import (FinancialEntry, FiscalDocument, OperationalSession, OperationalSessionExam,
    Partner, Partnership, PhysicianProfile, PhysicianTransfer, ProfitCostRule, ReportDocument, SpirometryExam)
from app.services.exam_profit import DEFAULT_RULES, calculate_exam
from app.services.profit_history import append_history, plan_history
from app.services.profit_projection import _payment, build_projection, rules_at


def calculate(**changes):
    values=dict(revenue=20000,received=None,gross=None,card_paid=0,payment_known=True,
        tax_base=None,partner_transfer=0,physician_transfer=1500,
        shared={"room":0,"parking":0,"fuel":1000},rules=DEFAULT_RULES)
    return calculate_exam(**{**values,**changes})


def test_zero_technician_and_tax_without_nfse_or_receipt():
    r=calculate()
    assert r["status"] == "completo"
    assert r["custos"]["tecnico"] == "0.00"
    assert r["custos"]["imposto"] == "12.00"
    assert r["lucro"] == r["lucro_competencia"] == "153.00"
    assert r["lucro_realizado"] is None
    assert r["pendencias_caixa"] == ["recebimento_nao_comprovado"]


@pytest.mark.parametrize("form,fee",[("Pix","0.00"),("Dinheiro","0.00"),("Cartão","10.00")])
def test_payment_proof_on_recognized_revenue(form,fee):
    known,received,card=_payment(FinancialEntry(valor=Decimal(200),forma_pagamento=form,status="Pendente"))
    r=calculate(payment_known=known,received=received,card_paid=card)
    assert r["status"] == "completo"
    assert r["custos"]["cartao"] == fee


def test_unknown_direct_payment_blocks_but_pastore_contract_does_not():
    assert calculate(payment_known=False,card_paid=None)["lucro"] is None
    r=calculate(revenue=10950,partner_revenue_is_net=True,partner_transfer=10950,
                payment_known=False,card_paid=None,contractual_partner=True)
    assert r["status"] == "completo"
    assert r["custos"]["parceiro"] == r["custos"]["cartao"] == "0.00"
    assert r["recebido"] is None
    assert r["cartao_fonte"] == "sem_evidencia_cartao_fechamento"


def seed(db,person,users,day=date(2026,9,15)):
    doctor=PhysicianProfile(user_id=users["admin"].id,professional_name="Médico Sintético",crm_number="000001",crm_state="RJ")
    db.add(doctor);db.flush()
    db.add(PhysicianTransfer(physician_profile_id=doctor.id,competencia=day.replace(day=1),eligible_report_count=8,unit_amount=15,reference_total=120,paid_amount=120,payment_date=day,status="Pago",payment_registered_by_user_id=users["admin"].id,payment_registered_at=datetime(day.year,day.month,day.day,12,tzinfo=timezone.utc),created_by_user_id=users["admin"].id))
    for k,v in DEFAULT_RULES.items():
        db.add(ProfitCostRule(key=k,value=v,effective_from=date(2026,7,1),reason="Confirmação sintética"))
    def make(n,place,**kwargs):
        e=SpirometryExam(public_code=f"ESP-SYN{n}",person_id=person["id"],data_exame=day,status="Realizado",local_atendimento=place,**kwargs)
        db.add(e);db.flush()
        release=datetime(day.year,day.month,day.day,12,tzinfo=timezone.utc)
        db.add(ReportDocument(public_code=f"LAU-SYN{n}",spirometry_exam_id=e.id,status="liberado",created_by_user_id=users["admin"].id,clinical_started_at=release,ready_for_signature_at=release,released_at=release,released_by_user_id=users["admin"].id,released_physician_profile_id=doctor.id,signature_status="liberada_institucional",validation_code=f"SYN{n}"))
        return e
    return make


@pytest.mark.parametrize("day",[date(2026,9,15),date(2026,11,4)])
def test_shared_fixed_location_and_independent_home_no_writes(db,person,users,day):
    make=seed(db,person,users,day)
    for n,place in [(1,"NOVA AMERICA"),(2,"NOVA AMERICA"),(3,"Domiciliar"),(4,"Domiciliar")]:
        e=make(n,place)
        db.add(FinancialEntry(public_code=f"LAN-SYN{n}",tipo="receita",valor=200,status="Recebido",forma_pagamento="Pix",spirometry_exam_id=e.id))
    db.commit();result=build_projection(db,day,day)
    assert result["kpis"]["exames"] == 4
    assert result["composicao"]["sala"] == "40.00"
    assert result["composicao"]["estacionamento"] == "25.00"
    assert result["composicao"]["combustivel"] == "30.00"
    assert result["kpis"]["lucro"] == "557.00"
    assert all(r["status_fiscal"] == "NÃO EMITIDA" for r in result["exames"])
    assert all(r["horas_faturaveis"] == "1.00" for r in result["exames"] if r["local"] == "nova_america")
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0
    assert db.scalar(select(func.count()).select_from(OperationalSession)) == 0


def test_eight_pastore_contract_without_receipt_or_double_partner(db,person,users):
    make=seed(db,person,users)
    p=Partner(public_code="CLI-SYN1",nome="Pastore",tipo="clinica");db.add(p);db.flush()
    db.add(Partnership(public_code="PAR-SYN1",partner_id=p.id,status="ativa",modelo_recebimento="valor_por_exame",valor_recebido_por_exame=Decimal("109.50"),vigencia_inicio=date(2026,7,1)))
    for n in range(8):make(n,"Pastore Ipanema",partner_id=p.id)
    db.commit();result=build_projection(db,date(2026,9,1),date(2026,9,30));k=result["kpis"]
    assert k["receita"] == "876.00"
    assert k["exames"] == 8
    assert k["recebido"] is k["lucro_realizado"] is None
    assert k["lucro"] == "587.44"
    assert result["composicao"]["parceiro"] == result["composicao"]["cartao"] == "0.00"
    assert result["composicao"]["medico"] == "120.00"
    assert result["composicao"]["combustivel"] == "30.00"


def test_history_requires_precision_decision_is_idempotent_and_append_only(db,person):
    db.add(SpirometryExam(public_code="ESP-SYN1",person_id=person["id"],status="Exame realizado",data_exame=date(2026,6,1),data_exame_dia_assumido=True,data_exame_original="06/2026",data_exame_precisao="mes"))
    db.add(SpirometryExam(public_code="ESP-SYN2",person_id=person["id"],status="Realizado",data_exame=date(2026,7,10),local_atendimento="Pastore Ipanema"))
    originals=[]
    for k,v in DEFAULT_RULES.items():
        if k.endswith("minimum_hours"):continue
        r=ProfitCostRule(key=k,value=v,effective_from=date(2026,7,1) if k.startswith("pastore.") else date(2026,11,4),reason="Regra original sintética");db.add(r);originals.append(r)
    db.commit()
    with pytest.raises(ValueError,match="apenas mês/ano"):plan_history(db,as_of=date(2026,11,5))
    plan=plan_history(db,as_of=date(2026,11,5),first_date_policy="month_boundary",minimum_room_hours=Decimal(1))
    assert plan["first_date_is_approximate"]
    assert plan["first_exact_date"] == "2026-07-10"
    assert len(plan["create"]) == 20
    assert not any(r["key"].startswith("alergocare.") for r in plan["create"])
    assert set(plan["preserved"]) == {"pastore.fuel","pastore.parking"}
    with pytest.raises(ValueError,match="Plano mudou"):append_history(db,plan,"wrong")
    append_history(db,plan,plan["sha256"]);db.commit()
    assert all(r.reason == "Regra original sintética" for r in originals)
    assert plan_history(db,as_of=date(2026,11,5),first_date_policy="month_boundary",minimum_room_hours=Decimal(1))["create"] == []
    rows=db.scalars(select(ProfitCostRule)).all()
    assert rules_at(rows,date(2026,6,1))["technician"] == 0
    assert rules_at(rows,date(2026,11,4))["material"] == 10
    originals[0].value=99
    with pytest.raises(ValueError,match="não pode ser alterada"):db.flush()
    db.rollback()


def test_explicit_shared_home_visit_overrides_independent_default(db,person,users):
    make=seed(db,person,users)
    exams=[make(n,"Domiciliar") for n in (1,2)]
    for n,e in enumerate(exams):
        db.add(FinancialEntry(public_code=f"LAN-SYN{n}",tipo="receita",valor=200,status="Recebido",forma_pagamento="Pix",spirometry_exam_id=e.id))
    sess=OperationalSession(place="home",service_date=date(2026,9,15),source="manual",status="confirmado",created_by_user_id=users["admin"].id)
    db.add(sess);db.flush()
    for e in exams:db.add(OperationalSessionExam(session_id=sess.id,spirometry_exam_id=e.id))
    db.commit();r=build_projection(db,date(2026,9,1),date(2026,9,30))
    assert r["kpis"]["exames"] == 2
    assert r["composicao"]["combustivel"] == "10.00"
    assert all(e["custos"]["combustivel"] == "5.00" for e in r["exames"])


def test_missing_room_hours_is_a_real_economic_gap(db,person,users):
    make=seed(db,person,users)
    e=make(1,"SHOPPING DOWNTOWN")
    db.add(FinancialEntry(public_code="LAN-SYN1",tipo="receita",valor=220,status="Recebido",forma_pagamento="Pix",spirometry_exam_id=e.id))
    # Uma sessão explícita sem horas não recebe mínimo inferido nem horas fictícias.
    sess=OperationalSession(place="downtown",service_date=e.data_exame,source="manual",status="confirmado",created_by_user_id=users["admin"].id)
    db.add(sess);db.flush();db.add(OperationalSessionExam(session_id=sess.id,spirometry_exam_id=e.id))
    db.commit();r=build_projection(db,e.data_exame,e.data_exame)["exames"][0]
    assert r["custos"]["sala"] is None
    assert r["lucro"] is None
    assert r["pendencias"] == ["custos_da_sessao_incompletos"]
