"""Base e ida confirmadas ALERGOCARE, somente dados sintéticos."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (FinancialEntry, FiscalDocument, OperationalSession,
                        OperationalSessionExam, Partner, PartnerTransfer, ProfitRevenueBasis)
from app.services.profit_projection import build_projection, classify_place
from tests.test_profit_competence import seed

DAY = date(2026, 10, 27)
D = Decimal


def make_case(db, person, users):
    p = Partner(public_code="CLI-SYN-A", nome="ALERGOCARE", status="ativa")
    db.add(p)
    db.flush()
    make = seed(db, person, users, day=DAY)
    e = make(1, "ALERGOCARE", partner_id=p.id, modalidade="cowork")
    f = FinancialEntry(public_code="LAN-SYN-A", tipo="receita", categoria="Espirometria",
                       valor=D("219"), status="Recebido", forma_pagamento="Cartão", spirometry_exam_id=e.id)
    db.add(f)
    db.commit()
    return e, f, make


def confirm(client, auth, exam_id, basis="bruto", gross="219.00"):
    response = client.post("/api/v1/financeiro/lucro/bases-receita", headers=auth("admin"), json={
        "exam_id":exam_id, "basis":basis, "gross_amount":gross,
        "evidence_note":"Confirmação sintética do total processado no cartão"})
    assert response.status_code == 201, response.text
    return response.json()


def session(client, auth, exam_id):
    return client.post("/api/v1/financeiro/lucro/sessoes", headers=auth("admin"), json={
        "place":"alergocare", "service_date":DAY.isoformat(), "exam_ids":[exam_id],
        "status":"confirmado", "parking_override":"10.00", "fuel_override":"10.00",
        "room_override":"0.00", "note":"Ida exclusiva confirmada, sem sala horária adicional"})


def test_33_complete_gross_preserved_and_no_second_deduction(client, auth, db, person, users):
    e, f, make = make_case(db, person, users)
    for n in range(2, 34):
        other = make(n, "Domiciliar", modalidade="residencial")
        db.add(FinancialEntry(public_code=f"LAN-SYN{n}", tipo="receita", valor=200,
                              status="Recebido", forma_pagamento="Pix", spirometry_exam_id=other.id))
    db.commit()
    first = confirm(client, auth, e.id)
    again = confirm(client, auth, e.id)
    assert again["id"] == first["id"] and again["idempotente"]
    response = session(client, auth, e.id)
    assert response.status_code == 201, response.text
    assert session(client, auth, e.id).status_code == 409
    result = build_projection(db, DAY, DAY)
    row = next(r for r in result["exames"] if r["id"] == e.id)
    assert row["local"] == "alergocare"
    assert row["pendencias"] == []
    assert (row["bruto"], row["receita"], row["recebido"]) == ("219.00", "153.30", "153.30")
    assert row["partilha_alergocare"]["parcela_parceiro"] == "65.70"
    assert row["custos"] == {"material":"10.00", "parceiro":"0.00", "medico":"15.00",
        "sala":"0.00", "estacionamento":"10.00", "combustivel":"10.00", "cartao":"10.95",
        "imposto":"9.20", "tecnico":"0.00"}
    assert row["lucro"] == "88.15"
    assert result["kpis"]["exames"] == 33 and result["kpis"]["incompletos"] == 0
    assert result["kpis"]["informacoes_faltantes"] == 0
    assert result["kpis"]["lucro"] is not None and result["kpis"]["margem"] is not None
    assert all(r["local"] != "não classificado" for r in result["exames"])
    assert db.get(FinancialEntry, f.id).valor == D("219")
    assert db.scalar(select(func.count()).select_from(ProfitRevenueBasis)) == 1
    assert db.scalar(select(func.count()).select_from(OperationalSession)) == 1
    assert db.scalar(select(func.count()).select_from(OperationalSessionExam)) == 1
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == 33
    assert db.scalar(select(func.count()).select_from(PartnerTransfer)) == 0
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0


def test_exclusive_visit_does_not_share_with_other_exam(client, auth, db, person, users):
    e, _, make = make_case(db, person, users)
    other = make(2, "ALERGOCARE", modalidade="cowork")
    db.commit()
    confirm(client, auth, e.id)
    assert session(client, auth, e.id).status_code == 201
    rows = {r["id"]:r for r in build_projection(db, DAY, DAY)["exames"]}
    assert rows[e.id]["custos"]["estacionamento"] == "10.00"
    assert rows[e.id]["custos"]["combustivel"] == "10.00"
    assert rows[other.id]["session_id"] is None
    assert rows[other.id]["custos"]["estacionamento"] is None
    assert rows[other.id]["custos"]["combustivel"] is None


def test_alergocare_is_distinct_and_unconfirmed_basis_blocks(db, person, users):
    e, _, _ = make_case(db, person, users)
    assert classify_place(e, None, None) == "alergocare"
    e.local_atendimento = "AlergoLife"
    assert classify_place(e, None, None) == "alergolife"
    e.local_atendimento = "ALERGOCARE"
    db.commit()
    row = build_projection(db, DAY, DAY)["exames"][0]
    assert "base_receita_parceiro_nao_confirmada" in row["pendencias"]
    assert row["lucro"] is None


def test_liquid_confirmation_does_not_take_thirty_again(client, auth, db, person, users):
    e, _, _ = make_case(db, person, users)
    confirm(client, auth, e.id, basis="liquido", gross="312.86")
    assert session(client, auth, e.id).status_code == 201
    row = build_projection(db, DAY, DAY)["exames"][0]
    assert row["receita"] == "219.00"
    assert row["custos"]["parceiro"] == "0.00"
    assert row["custos"]["cartao"] == "15.64"
