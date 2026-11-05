"""Histórico sintético: fatos conhecidos sobrevivem ao fechamento incompleto."""
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import func, select
from app.models import (FinancialEntry, OperationalSession, OperationalSessionExam, Partner,
    PartnerUnit, Partnership, PartnerSettlement, PartnerSettlementItem, ProfitCostRule, SpirometryExam)
from app.services.exam_profit import DEFAULT_RULES, calculate_exam, session_cost
from app.services.profit_projection import summarize

PATH = "/api/v1/financeiro/lucro/painel"

def calc(**changes):
    args = dict(revenue=20000, received=20000, gross=20000, card_paid=0,
        payment_known=True, tax_base=20000, partner_transfer=0, physician_transfer=1500,
        shared={"room":6000,"parking":2500,"fuel":1000}, rules=DEFAULT_RULES)
    return calculate_exam(**{**args, **changes})

def row(result, code="SYN-1"):
    return dict(result, exame=code, local="downtown", data="2026-09-15")

def test_known_revenue_partial_costs_missing_rules():
    r = calc(revenue=22000, received=22000, gross=22000, rules={}, shared=None)
    assert r["receita"] == r["recebido"] == "220.00"
    assert r["custos"]["medico"] == r["custos_comprovados"] == "15.00"
    assert r["custos"]["material"] is None
    assert r["custo_total"] is r["lucro"] is r["margem"] is None
    s = summarize([row(r)])
    assert s["kpis"]["receita"] == s["por_mes"][0]["receita"] == "220.00"
    assert s["kpis"]["lucro"] is s["por_dia"][0]["lucro"] is None
    assert s["composicao"]["medico"] == "15.00"
    assert "material" not in s["composicao"]
    assert s["composicao_parcial"]

def test_unknown_never_zero():
    r = calc(revenue=None, received=None, gross=None, payment_known=False, card_paid=None,
        rules={}, shared=None, physician_transfer=None, partner_transfer=None, tax_base=None)
    assert r["receita"] is r["recebido"] is r["pendente"] is r["custos_comprovados"] is None
    assert summarize([row(r)])["kpis"]["receita"] is None
    assert summarize([row(r)])["kpis"]["custos"] is None

def test_complete_and_real_zero():
    r = calc()
    assert (r["status"],r["custo_total"],r["lucro"],r["margem"]) == ("completo","132.00","68.00","34.0")
    assert r["custos"]["cartao"] == "0.00"
    zero = calc(revenue=13200,received=13200,gross=13200,tax_base=20000)
    assert summarize([row(zero)])["kpis"]["lucro"] == "0.00"

def test_mixed_population_closed_denominator():
    pending=calc(revenue=22000,received=None,gross=22000,card_paid=None,payment_known=False,rules={},shared=None)
    s=summarize([row(calc()),row(pending,"SYN-2")]); k=s["kpis"]
    assert (k["receita"],k["custos"],k["lucro"],k["margem"]) == ("420.00","147.00","68.00","34.0")
    assert (k["receita_calculada"],k["custo_total_calculado"]) == ("200.00","132.00")
    assert (k["total_exames"],k["exames"],k["incompletos"]) == (2,1,1)
    assert k["cobertura"]["recebido"] == 1
    assert k["recebido"] == "200.00"
    assert s["por_local"][0]["margem"] == "34.0"
    assert s["por_local"][0]["informacoes_faltantes"] == len(pending["pendencias"])

def test_partial_session_overrides_without_rules():
    cost=session_cost("downtown",None,{},parking_override=Decimal("25"),partial=True)
    assert cost == {"parking":2500}
    r=calc(shared=cost,rules={})
    assert r["custos"]["estacionamento"] == "25.00"
    assert r["custos"]["combustivel"] is None
    assert "custos_da_sessao_incompletos" in r["pendencias"]
    assert "sessao_operacional_ausente" not in r["pendencias"]

def test_recorded_fees_need_no_estimated_rate():
    r=calc(rules={"material":Decimal("10"),"technician":Decimal("0")},card_fee_override=100,tax_amount_override=900,tax_base=None)
    assert r["status"] == "completo"
    assert r["custos"]["cartao"] == "1.00"
    assert r["custos"]["imposto"] == "9.00"

def make_exam(db,person,code,day,place="SHOPPING DOWNTOWN",**kwargs):
    exam=SpirometryExam(public_code=code,person_id=person["id"],data_exame=day,status="Realizado",local_atendimento=place,**kwargs)
    db.add(exam); db.flush(); return exam

def dashboard(client,auth,query=""):
    response=client.get(PATH+"?start=2026-09-01&end=2026-09-30"+query,headers=auth("gestor"))
    assert response.status_code == 200,response.text
    return response.json()

def test_history_no_rules_no_fiscal(client,auth,db,person):
    exam=make_exam(db,person,"ESP-SYN1",date(2026,9,15))
    db.add(FinancialEntry(public_code="LAN-SYN1",tipo="receita",categoria="Espirometria",valor=220,
        spirometry_exam_id=exam.id,status="Recebido",forma_pagamento="Pix",data_recebimento=date(2026,9,15)))
    db.add(ProfitCostRule(key="material",effective_from=date(2026,11,4),value=10,reason="Regra sintética atual"))
    db.commit(); s=dashboard(client,auth); r=s["exames"][0]
    assert s["kpis"]["receita"] == "220.00"
    assert s["kpis"]["lucro"] is r["base_imposto"] is r["custos"]["material"] is None
    assert r["data_recebimento"] == "2026-09-15"
    assert "base_tributavel_ausente" in r["pendencias"]
    assert "sessao_operacional_ausente" in r["pendencias"]
    assert db.scalar(select(func.count()).select_from(OperationalSession)) == 0

@pytest.mark.parametrize("query,expected",[("",2),("&local=downtown",1),
    ("&parceiro=Parceiro Sintético",1),("&forma_pagamento=Pix",1),("&status_financeiro=Pendente",1),
    ("&local=downtown&forma_pagamento=Pix&status_financeiro=Recebido",1),("&local=home",0)])
def test_filters(client,auth,db,person,query,expected):
    partner=Partner(public_code="CLI-SYN1",nome="Parceiro Sintético",tipo="clinica"); db.add(partner); db.flush()
    for n,place,status,form,pid in [(1,"SHOPPING DOWNTOWN","Recebido","Pix",None),(2,"MACLIN","Pendente","Cartão",partner.id)]:
        e=make_exam(db,person,f"ESP-SYN{n}",date(2026,9,15),place,partner_id=pid)
        db.add(FinancialEntry(public_code=f"LAN-SYN{n}",tipo="receita",valor=220,spirometry_exam_id=e.id,status=status,forma_pagamento=form))
    db.commit(); s=dashboard(client,auth,query)
    assert s["kpis"]["total_exames"] == s["kpis"]["incompletos"] == expected
    assert s["kpis"]["lucro"] is None
    assert s["kpis"]["receita"] == (f"{220*expected}.00" if expected else None)
    single=client.get(PATH+"?start=2026-09-15&end=2026-09-15"+query,headers=auth("gestor")).json()
    assert single["kpis"] == s["kpis"]
    outside=client.get(PATH+"?start=2026-10-01&end=2026-10-31",headers=auth("gestor")).json()
    assert outside["kpis"]["total_exames"] == 0

def test_pastore_canonical_receipt_and_no_double_count(client,auth,db,person):
    partner=Partner(public_code="CLI-SYN1",nome="Pastore",tipo="clinica",status="ativa"); db.add(partner); db.flush()
    unit=PartnerUnit(public_code="UNI-SYN1",partner_id=partner.id,nome="Pastore Ipanema",ativo=True); db.add(unit); db.flush()
    db.add(Partnership(public_code="PAR-SYN1",partner_id=partner.id,status="ativa",modelo_recebimento="valor_por_exame",valor_recebido_por_exame=Decimal("109.50"),vigencia_inicio=date(2026,7,1)))
    settlement=PartnerSettlement(partner_id=partner.id,partner_unit_id=unit.id,competencia=date(2026,9,1),status="recebido",valor_total=219); db.add(settlement); db.flush()
    for n in (1,2):
        e=make_exam(db,person,f"ESP-SYN{n}",date(2026,9,15),"Pastore Ipanema",partner_id=partner.id,partner_unit_id=unit.id)
        db.add(PartnerSettlementItem(settlement_id=settlement.id,spirometry_exam_id=e.id))
    receipt=FinancialEntry(public_code="LAN-SYN1",tipo="receita",valor=219,status="Recebido",forma_pagamento="Pix",data_recebimento=date(2026,10,1),partner_settlement_id=settlement.id); db.add(receipt)
    for key,value in [("pastore.parking",6),("pastore.fuel",30)]:
        db.add(ProfitCostRule(key=key,value=value,effective_from=date(2026,7,1),reason="Regra histórica sintética"))
    db.commit(); s=dashboard(client,auth,"&forma_pagamento=Pix&status_financeiro=Recebido")
    assert s["kpis"]["receita"] == s["kpis"]["recebido"] == "219.00"
    assert s["kpis"]["custos"] == "36.00"
    assert all(r["data_recebimento"] == "2026-10-01" for r in s["exames"])
    assert all(r["custos"]["combustivel"] == "15.00" for r in s["exames"])
    receipt.status="Cancelado"; db.commit()
    s=dashboard(client,auth)
    assert s["kpis"]["receita"] == "219.00"
    assert s["kpis"]["recebido"] is None

def test_no_session_inference_and_partial_session_distinct(client,auth,db,person,users):
    a=make_exam(db,person,"ESP-SYN1",date(2026,9,15)); b=make_exam(db,person,"ESP-SYN2",date(2026,9,15)); db.commit()
    assert all("sessao_operacional_ausente" in r["pendencias"] for r in dashboard(client,auth)["exames"])
    session=OperationalSession(place="downtown",service_date=date(2026,9,15),source="manual",status="confirmado",parking_override=25,created_by_user_id=users["admin"].id)
    db.add(session); db.flush()
    for e in (a,b): db.add(OperationalSessionExam(session_id=session.id,spirometry_exam_id=e.id))
    db.commit(); s=dashboard(client,auth)
    assert s["kpis"]["custos"] == "25.00"
    assert all(r["custos"]["estacionamento"] == "12.50" for r in s["exames"])
    assert all("custos_da_sessao_incompletos" in r["pendencias"] for r in s["exames"])

@pytest.mark.parametrize("form", ["Pix + Cartão", "Misto", "Outro", "não é cartão"])
def test_ambiguous_payment_does_not_infer_card_portion(form):
    from app.services.profit_projection import _payment
    entry=FinancialEntry(valor=Decimal("220"),status="Recebido",forma_pagamento=form)
    classified,received,card_paid=_payment(entry)
    assert received == 22000
    assert classified is False
    assert card_paid is None
