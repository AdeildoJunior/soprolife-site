"""Retestes financeiros e fechamento mensal, apenas dados sintéticos."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import FinancialEntry, FiscalDocument, PhysicianTransfer, SpirometryExam
from tests.test_conciliacao_extra_pastore import _criar_exame
from tests.test_m26_9_cadastro_repasses_medicos import _doctor_profile, _exam_and_document

API = "/api/v1"


def _marcar(db, exam_id):
    db.get(SpirometryExam, exam_id).reteste_sem_cobranca = True
    db.commit()


def test_reteste_separado_da_pendencia_e_dos_totais(client, auth, person, db):
    normal = _criar_exame(client, auth, person["id"])
    reteste = _criar_exame(client, auth, person["id"])
    _marcar(db, reteste["id"])
    response = client.get(f"{API}/financeiro/conciliacao/extra-pastore", headers=auth("leitura"))
    data = response.json()
    assert data["total_exames"] == data["exames_pendentes"] == 1
    assert data["pendentes"][0]["exam_id"] == normal["id"]
    assert data["pendentes"][0]["status"] == "sem_lancamento"
    assert data["conciliados"] == []
    assert data["retestes_sem_cobranca"][0]["exam_id"] == reteste["id"]
    assert data["retestes_sem_cobranca"][0]["status"] == "reteste_sem_cobranca"
    assert data["total_vinculado"] == "0.00"
    history = client.get(f"{API}/espirometrias", params={"person_id": person["id"]}, headers=auth("leitura"))
    assert reteste["id"] in {e["id"] for e in history.json()["itens"]}


@pytest.mark.parametrize("tipo", ["receita", "repasse", "despesa"])
def test_reteste_recusa_lancamento_manual_e_replay(client, auth, person, db, tipo):
    exam = _criar_exame(client, auth, person["id"])
    _marcar(db, exam["id"])
    payload = {"tipo": tipo, "valor": "15.00", "spirometry_exam_id": exam["id"],
               "idempotency_key": "reteste-sintetico"}
    for _ in range(2):
        response = client.post(f"{API}/lancamentos", json=payload, headers=auth("gestor"))
        assert response.status_code == 422, response.text
        assert response.json()["erro"]["mensagem"]["codigo"] == "reteste_sem_cobranca"
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == 0
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0


def test_lote_com_reteste_recusado_sem_criar_receita_parcial(client, auth, person, db):
    normal = _criar_exame(client, auth, person["id"])
    reteste = _criar_exame(client, auth, person["id"])
    _marcar(db, reteste["id"])
    response = client.post(f"{API}/financeiro/conciliacao/extra-pastore/lote", json={
        "total_esperado": "3044.79", "itens": [
            {"spirometry_exam_id": normal["id"], "valor": "3000.00"},
            {"spirometry_exam_id": reteste["id"], "valor": "44.79"},
        ]}, headers=auth("gestor"))
    assert response.status_code == 422, response.text
    assert response.json()["erro"]["mensagem"]["codigo"] == "reteste_sem_cobranca"
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == 0


def test_33_remuneraveis_pagamento_e_reexecucao_sem_duplicar(client, auth, db, users):
    _, profile = _doctor_profile(db, "33")
    profile_id = profile.id
    for i in range(34):
        doc = _exam_and_document(db, profile, users["admin"], str(100 + i),
                                status="liberado", released_at=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))
        if i == 33:
            _marcar(db, doc.spirometry_exam_id)
    _exam_and_document(db, profile, users["admin"], "200", status="liberado",
                       released_at=datetime(2026, 9, 15, 12, tzinfo=timezone.utc))
    db.commit()
    def dashboard(month):
        return client.get(f"{API}/financeiro/repasses-medicos?competencia={month}", headers=auth("gestor")).json()
    september = dashboard("2026-09")
    payload = {"physician_profile_id": profile_id, "competencia": "2026-10",
               "expected_eligible_report_count": 33, "unit_amount": "15.00",
               "paid_amount": "495.00", "payment_date": "2026-11-05"}
    response = client.post(f"{API}/financeiro/repasses-medicos", json=payload, headers=auth("gestor"))
    assert response.status_code == 201, response.text
    row = response.json()["medicas"][0]
    assert row["quantidade_laudos_elegiveis"] == row["quantidade_laudos_atual"] == 33
    assert (row["valor_unitario"], row["total_calculado"], row["valor_pago"]) == ("15.00", "495.00", "495.00")
    assert (row["status"], row["competencia"], row["data_pagamento"]) == ("Pago", "2026-10", "2026-11-05")
    assert client.post(f"{API}/financeiro/repasses-medicos", json=payload, headers=auth("gestor")).status_code == 409
    assert client.patch(f"{API}/financeiro/repasses-medicos/{row['id']}/pagamento",
                        json={"paid_amount": "495.00", "payment_date": "2026-11-05"}, headers=auth("gestor")).status_code == 409
    assert dashboard("2026-09")["medicas"] == september["medicas"]
    assert db.scalar(select(func.count()).select_from(PhysicianTransfer)) == 1
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == 0
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0
