"""Histórico líquido e novo caixa bruto Maclin, com dados sintéticos."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (FinancialEntry, FiscalDocument, Partner, PartnerTransfer,
                        ProfitRevenueBasis, SpirometryExam)
from app.services.maclin import record_basis, split_gross
from app.services.profit_projection import build_projection
from app.services import nfse
from tests.test_profit_competence import seed
from tests.test_nfse_foundation import policy_payload

DAY = date(2026, 9, 15)
D = Decimal


def partner(db):
    p = Partner(public_code="CLI-SYN-M", nome="MACLIN", status="ativa")
    db.add(p)
    db.commit()
    return p


@pytest.mark.parametrize("net,gross,form,fee,profit", [
    ("130.00", None, "Pix", "0.00", "87.20"),
    ("219.00", "312.86", "Cartão", "15.64", "155.22"),
    ("100.00", "142.86", "Pix", "0.00", "59.00"),
])
@pytest.mark.parametrize("cash_transfer", [None, "partner_transfer", "financial_entry"])
def test_historical_net_preserves_cash_without_double_deduction(db, person, users, net, gross, form, fee, profit, cash_transfer):
    p = partner(db)
    make = seed(db, person, users)
    e = make(1, "MACLIN", partner_id=p.id, modalidade="cowork")
    f = FinancialEntry(public_code="LAN-SYN-M", tipo="receita", categoria="Espirometria",
                       valor=D(net), status="Recebido", forma_pagamento=form, spirometry_exam_id=e.id)
    db.add(f)
    db.flush()
    transfer = None
    if cash_transfer == "partner_transfer":
        transfer = PartnerTransfer(partner_id=p.id, financial_entry_id=f.id,
                                   valor=D("60"), status="pago", data_pagamento=DAY)
        db.add(transfer)
    if cash_transfer == "financial_entry":
        db.add(FinancialEntry(public_code="LAN-SYN-CASH", tipo="repasse", valor=D("60"),
                              status="Recebido", spirometry_exam_id=e.id))
    row, duplicate = record_basis(db, e, f, basis="liquido", gross_amount=D(gross) if gross else None,
        evidence_note="Confirmação sintética de histórico líquido", user_id=users["admin"].id, request_id="test")
    assert not duplicate
    db.commit()
    same, duplicate = record_basis(db, e, f, basis="liquido", gross_amount=D(gross) if gross else None,
        evidence_note="Reexecução sintética", user_id=users["admin"].id, request_id="test-again")
    assert duplicate and same.id == row.id
    db.commit()
    result = build_projection(db, DAY, DAY)["exames"][0]
    assert result["status"] == "completo", result
    assert result["receita"] == result["recebido"] == net
    assert result["bruto"] == gross
    assert result["custos"]["parceiro"] == "0.00"
    assert result["custos"]["cartao"] == fee
    assert result["lucro"] == profit
    assert result["partilha_maclin"]["modelo"] == "historico_liquido"
    assert db.get(FinancialEntry, f.id).valor == D(net)
    assert db.scalar(select(func.count()).select_from(ProfitRevenueBasis)) == 1
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == (2 if cash_transfer == "financial_entry" else 1)
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0
    if transfer:
        assert db.get(PartnerTransfer, transfer.id).valor == D("60")
        assert transfer.status == "pago"


@pytest.mark.parametrize("form,missing", [("Cartão", "valor_bruto_cartao_nao_comprovado"), (None, "forma_de_pagamento_nao_classificada")])
def test_gross_unknown_does_not_invent_card_or_payment(db, person, users, form, missing):
    p = partner(db)
    e = seed(db, person, users)(1, "MACLIN", partner_id=p.id)
    f = FinancialEntry(public_code="LAN-SYN-U", tipo="receita", valor=D("130"), status="Recebido",
                       forma_pagamento=form, spirometry_exam_id=e.id)
    db.add(f)
    db.flush()
    record_basis(db, e, f, basis="liquido", gross_amount=None, evidence_note="Histórico sintético",
                 user_id=users["admin"].id, request_id="test")
    db.commit()
    r = build_projection(db, DAY, DAY)["exames"][0]
    assert r["custos"]["cartao"] is None
    assert missing in r["pendencias"]
    assert "repasse_parceiro_nao_conciliado" not in r["pendencias"]


@pytest.mark.parametrize("form,fee", [("Pix", "0.00"), ("Cartão", "10.00")])
def test_new_attendance_gross_cash_net_profit_and_full_invoice(client, auth, db, person, users, form, fee):
    p = partner(db)
    seed(db, person, users)
    db.commit()
    payload = {"person_id":person["id"], "tipo":"espirometria_soprolife", "idempotency_key":"maclin-synthetic",
        "espirometria":{"data_exame":DAY.isoformat(), "modalidade":"cowork", "local_atendimento":"MACLIN", "parceria_comercial":"maclin"},
        "financeiro":{"espirometria":{"valor":"200.00", "status":"Recebido", "forma_pagamento":form}}}
    response = client.post("/api/v1/atendimentos", json=payload, headers=auth("operacional"))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["partilha_maclin"] == {"bruto":"200.00", "soprolife":"140.00", "maclin":"60.00", "pagamento_criado":False}
    assert data["espirometria"]["partner_id"] == p.id
    assert client.post("/api/v1/atendimentos", json=payload, headers=auth("operacional")).status_code == 409
    entry = db.scalar(select(FinancialEntry))
    assert entry.valor == D("200")
    r = build_projection(db, DAY, DAY)["exames"][0]
    assert (r["receita"], r["bruto"], r["recebido"]) == ("140.00", "200.00", "140.00")
    assert r["custos"]["parceiro"] == "0.00"
    assert r["custos"]["cartao"] == fee
    assert "cartao_superior_a_receita" not in r["pendencias"]
    assert r["partilha_maclin"]["parcela_maclin"] == "60.00"
    assert db.scalar(select(func.count()).select_from(PartnerTransfer)) == 0
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == 1
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0
    nfse.create_policy(db, policy_payload(flow="DIRECT"), users["admin"].id)
    fiscal = nfse.evaluate(db, entry.spirometry_exam_id, "mock", for_update=False)
    assert fiscal["amount_snapshot"] == D("200.00"), fiscal
    assert fiscal["blocking_reasons"] == []


def test_history_is_not_a_new_gross_fiscal_source(db, person, users):
    p = partner(db)
    e = seed(db, person, users)(1, "MACLIN", partner_id=p.id, modalidade="cowork")
    f = FinancialEntry(public_code="LAN-SYN-H", tipo="receita", categoria="Espirometria", valor=D("130"), status="Recebido", spirometry_exam_id=e.id)
    db.add(f)
    db.flush()
    record_basis(db, e, f, basis="liquido", gross_amount=None, evidence_note="Histórico sintético",
                 user_id=users["admin"].id, request_id="test")
    db.commit()
    nfse.create_policy(db, policy_payload(flow="DIRECT"), users["admin"].id)
    fiscal = nfse.evaluate(db, e.id, "mock", for_update=False)
    assert fiscal["amount_snapshot"] is None
    assert "maclin_historical_gross_not_confirmed" in fiscal["blocking_reasons"]


def test_centavos_conserve_both_parts():
    for cents in range(1, 10000):
        gross = D(cents) / 100
        net, maclin = split_gross(gross)
        assert net + maclin == gross
        assert maclin == (gross * D("0.30")).quantize(D("0.01"), rounding="ROUND_HALF_UP")


@pytest.mark.parametrize("gross", ["200.00", "185.711", "NaN"])
def test_admin_confirmation_validates_history_centavos(client, auth, db, person, users, gross):
    p = partner(db)
    e = seed(db, person, users)(1, "MACLIN", partner_id=p.id)
    f = FinancialEntry(public_code="LAN-SYN-V", tipo="receita", valor=D("130"), status="Recebido", spirometry_exam_id=e.id)
    db.add(f)
    db.commit()
    response = client.post("/api/v1/financeiro/lucro/bases-receita", headers=auth("admin"), json={
        "exam_id":e.id,"basis":"liquido","gross_amount":gross,"evidence_note":"Prova sintética"})
    assert response.status_code == 422, response.text
    assert db.scalar(select(func.count()).select_from(ProfitRevenueBasis)) == 0


def test_new_maclin_cannot_silently_use_old_input(client, auth, person):
    response = client.post("/api/v1/atendimentos", headers=auth("operacional"), json={
        "tipo":"espirometria_soprolife", "person_id":person["id"],
        "espirometria":{"data_exame":DAY.isoformat(),"modalidade":"cowork","local_atendimento":"MACLIN"},
        "financeiro":{"espirometria":{"valor":"130.00"}}})
    assert response.status_code == 422
