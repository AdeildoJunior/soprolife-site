"""Espirometrias, consultas e leads — idempotência e follow-up de 6 meses."""

from datetime import date

from app.dates import add_months


def test_exame_idempotente(client, auth, person):
    payload = {
        "person_id": person["id"],
        "data_exame": "10/01/2026",
        "modalidade": "residencial",
        "status": "Realizado",
        "idempotency_key": "ESP-20260110-100000-TESTE1",
    }
    first = client.post("/api/v1/espirometrias", json=payload, headers=auth("operacional"))
    assert first.status_code == 201
    second = client.post("/api/v1/espirometrias", json=payload, headers=auth("operacional"))
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotente"] is True
    lista = client.get("/api/v1/espirometrias", headers=auth("leitura")).json()
    assert lista["total"] == 1


def test_exame_realizado_agenda_followup_6_meses(client, auth, person):
    resp = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "data_exame": "10/01/2026", "status": "Realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    assert resp.json()["followup"]["motivo"] == "criado"
    fups = client.get(
        f"/api/v1/followups?person_id={person['id']}", headers=auth("leitura")
    ).json()["itens"]
    assert len(fups) == 1
    assert fups[0]["tipo"] == "pos_exame"
    assert fups[0]["due_date"] == add_months(date(2026, 1, 10), 6).isoformat()


def test_exame_aguardando_nao_agenda_followup(client, auth, person):
    resp = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "data_exame": "10/08/2026", "status": "Aguardando"},
        headers=auth("operacional"),
    )
    assert resp.json()["followup"]["motivo"] == "nao_aplicavel"


def test_exame_data_incompleta_preserva_metadados(client, auth, person):
    resp = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "data_exame": "06/2026", "status": "Realizado"},
        headers=auth("operacional"),
    )
    body = resp.json()
    assert body["data_exame"] == "2026-06-01"
    assert body["data_exame_original"] == "06/2026"
    assert body["data_exame_precisao"] == "mes"
    assert body["data_exame_dia_assumido"] is True


def test_consulta_realizada_agenda_followup(client, auth, person):
    resp = client.post(
        "/api/v1/consultas",
        json={
            "person_id": person["id"],
            "data_consulta": "15/02/2026",
            "modalidade": "teleconsulta",
            "status": "Realizada",
            "idempotency_key": "CON-TESTE-000001",
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    assert resp.json()["followup"]["motivo"] == "criado"
    fups = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=pos_consulta",
        headers=auth("leitura"),
    ).json()["itens"]
    assert fups[0]["due_date"] == add_months(date(2026, 2, 15), 6).isoformat()


def test_consulta_idempotente(client, auth, person):
    payload = {
        "person_id": person["id"],
        "data_consulta": "15/02/2026",
        "status": "Agendada",
        "idempotency_key": "CON-TESTE-000002",
    }
    first = client.post("/api/v1/consultas", json=payload, headers=auth("operacional"))
    second = client.post("/api/v1/consultas", json=payload, headers=auth("operacional"))
    assert second.json()["id"] == first.json()["id"]


def test_lead_com_retomada_manual(client, auth, person):
    resp = client.post(
        "/api/v1/leads",
        json={
            "person_id": person["id"],
            "origem": "instagram",
            "modalidade": "residencial",
            "data_primeiro_contato": "01/03/2026",
            "data_retomada_manual": "2026-09-15",
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    assert resp.json()["followup"]["motivo"] == "criado"
    fups = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=lead_sem_atendimento",
        headers=auth("leitura"),
    ).json()["itens"]
    # retomada manual tem precedência sobre a data do primeiro contato
    assert fups[0]["due_date"] == "2026-09-15"
    assert fups[0]["due_date_manual"] is True


def test_modalidade_pcmso_inexistente(client, auth, person):
    resp = client.post(
        "/api/v1/leads",
        json={"person_id": person["id"], "modalidade": "pcmso"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422  # PCMSO fora da operação ativa


def test_pessoa_inexistente_404(client, auth):
    resp = client.post(
        "/api/v1/espirometrias",
        json={"person_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 404
