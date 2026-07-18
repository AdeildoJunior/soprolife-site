"""Fila de follow-up, 'não contatar', consentimento e WhatsApp assistido."""

from datetime import date, timedelta

from app.services.followup import (
    FILA_AGUARDANDO,
    FILA_ATRASADO,
    FILA_HOJE,
    FILA_SEMANA,
    build_whatsapp_url,
    classify_queue,
    today_local,
)


class _FakeFup:
    def __init__(self, due, status="pendente"):
        self.due_date = due
        self.status = status


class _FakePerson:
    def __init__(self, nao_contatar=False):
        self.nao_contatar = nao_contatar


def test_classificacao_das_filas():
    today = date(2026, 7, 15)  # quarta-feira
    assert classify_queue(_FakeFup(date(2026, 7, 10)), _FakePerson(), today) == FILA_ATRASADO
    assert classify_queue(_FakeFup(date(2026, 7, 15)), _FakePerson(), today) == FILA_HOJE
    assert classify_queue(_FakeFup(date(2026, 7, 18)), _FakePerson(), today) == FILA_SEMANA
    assert classify_queue(_FakeFup(date(2026, 8, 20)), _FakePerson(), today) == FILA_AGUARDANDO
    assert classify_queue(_FakeFup(None), _FakePerson(), today) == FILA_AGUARDANDO
    assert classify_queue(_FakeFup(date(2026, 7, 15)), _FakePerson(True), today) == "nao_contatar"
    assert classify_queue(_FakeFup(date(2026, 7, 10), "concluido"), _FakePerson(), today) == "concluido"


def test_url_whatsapp():
    url = build_whatsapp_url("5521999990001", "Olá, tudo bem?")
    assert url.startswith("https://wa.me/5521999990001?text=")
    assert "%20" in url  # mensagem urlencoded
    assert " " not in url


def _exame_realizado(client, auth, person_id, key):
    return client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person_id,
            "data_exame": (today_local() - timedelta(days=200)).isoformat(),
            "status": "Realizado",
            "idempotency_key": key,
        },
        headers=auth("operacional"),
    )


def test_fila_exclui_nao_contatar(client, auth, person):
    _exame_realizado(client, auth, person["id"], "ESP-FILA-000001")
    fila = client.get("/api/v1/followups/fila", headers=auth("leitura")).json()
    assert fila["totais"]["atrasado"] == 1
    # marca não contatar -> some da fila
    client.patch(
        f"/api/v1/pessoas/{person['id']}",
        json={"nao_contatar": True},
        headers=auth("operacional"),
    )
    fila = client.get("/api/v1/followups/fila", headers=auth("leitura")).json()
    assert fila["totais"]["atrasado"] == 0
    assert fila["excluidos_nao_contatar"] == 1


def test_criar_followup_para_nao_contatar_bloqueado(client, auth, person):
    client.patch(
        f"/api/v1/pessoas/{person['id']}",
        json={"nao_contatar": True},
        headers=auth("operacional"),
    )
    resp = client.post(
        "/api/v1/followups",
        json={"person_id": person["id"], "tipo": "manual"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 409


def test_followup_nao_duplica(client, auth, person):
    first = _exame_realizado(client, auth, person["id"], "ESP-DUP-000001")
    exam_id = first.json()["id"]
    # atualizar o mesmo exame de novo não cria segundo follow-up
    client.patch(
        f"/api/v1/espirometrias/{exam_id}",
        json={"status": "Realizado"},
        headers=auth("operacional"),
    )
    fups = client.get(
        f"/api/v1/followups?person_id={person['id']}", headers=auth("leitura")
    ).json()
    assert fups["total"] == 1


def test_consentimento_desconhecido_fail_closed(client, auth):
    """Sem consentimento concedido: aviso na fila E URL bloqueada (409)."""
    pessoa = client.post(
        "/api/v1/pessoas",
        json={
            "nome_completo": "Sem Consentimento 001",
            "contatos": [{"tipo": "whatsapp", "valor": "(21) 0000-9077", "principal": True}],
        },
        headers=auth("operacional"),
    ).json()
    _exame_realizado(client, auth, pessoa["id"], "ESP-CONS-000001")
    fila = client.get("/api/v1/followups/fila?fila=atrasado", headers=auth("leitura")).json()
    item = fila["itens"][0]
    assert item["consentimento_whatsapp"] == "desconhecido"
    assert item["aviso_consentimento"] is True
    assert item["whatsapp_permitido"] is False
    resp = client.get(
        f"/api/v1/followups/{item['id']}/whatsapp-url", headers=auth("operacional")
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "sem_consentimento"
    confirm = client.post(
        f"/api/v1/followups/{item['id']}/whatsapp-confirmacao",
        json={"resultado": "enviado"},
        headers=auth("operacional"),
    )
    assert confirm.status_code == 409


def test_followup_do_parceiro_sem_whatsapp_soprolife(client, auth, person):
    """Follow-up controlado pela clínica não expõe contato da SoproLife."""
    partner = client.post(
        "/api/v1/parceiros",
        json={"nome": "Clínica Controla Contato", "status": "ativa"},
        headers=auth("operacional"),
    ).json()
    referral = client.post(
        "/api/v1/encaminhamentos",
        json={"person_id": person["id"], "partner_id": partner["id"],
              "autorizacao_contato_soprolife": True,
              "responsavel_followup": "parceiro",
              "data_agendada": "2026-01-05"},
        headers=auth("operacional"),
    ).json()
    fup_id = referral["followup"]["id"]
    fila = client.get("/api/v1/followups/fila?fila=atrasado", headers=auth("leitura")).json()
    item = next(i for i in fila["itens"] if i["id"] == fup_id)
    assert item["controlado_por_parceiro"] is True
    assert item["whatsapp_permitido"] is False
    resp = client.get(
        f"/api/v1/followups/{fup_id}/whatsapp-url", headers=auth("operacional")
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "followup_do_parceiro"


def test_whatsapp_url_e_confirmacao(client, auth, person):
    _exame_realizado(client, auth, person["id"], "ESP-WA-000001")
    fup = client.get(
        f"/api/v1/followups?person_id={person['id']}", headers=auth("leitura")
    ).json()["itens"][0]
    resp = client.get(
        f"/api/v1/followups/{fup['id']}/whatsapp-url", headers=auth("operacional")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("https://wa.me/552100009001?text=")
    assert body["envio_automatico"] is False
    assert body["consentimento_whatsapp"] == "concedido"
    # nenhuma interação registrada ainda (montar URL não registra nada)
    inter = client.get(
        f"/api/v1/interacoes?followup_id={fup['id']}", headers=auth("leitura")
    ).json()
    assert inter["total"] == 0
    # confirmação humana registra a interação
    confirm = client.post(
        f"/api/v1/followups/{fup['id']}/whatsapp-confirmacao",
        json={"resultado": "enviado", "resumo": "Mensagem de retorno enviada"},
        headers=auth("operacional"),
    )
    assert confirm.status_code == 201
    inter = client.get(
        f"/api/v1/interacoes?followup_id={fup['id']}", headers=auth("leitura")
    ).json()
    assert inter["total"] == 1
    assert inter["itens"][0]["canal"] == "whatsapp"


def test_whatsapp_bloqueado_para_nao_contatar(client, auth, person):
    _exame_realizado(client, auth, person["id"], "ESP-WA-000002")
    fup = client.get(
        f"/api/v1/followups?person_id={person['id']}", headers=auth("leitura")
    ).json()["itens"][0]
    client.patch(
        f"/api/v1/pessoas/{person['id']}",
        json={"nao_contatar": True},
        headers=auth("operacional"),
    )
    resp = client.get(
        f"/api/v1/followups/{fup['id']}/whatsapp-url", headers=auth("operacional")
    )
    assert resp.status_code == 409


def test_concluir_e_nova_tentativa_limpa_conclusao(client, auth, person):
    _exame_realizado(client, auth, person["id"], "ESP-CONC-000001")
    fup = client.get(
        f"/api/v1/followups?person_id={person['id']}", headers=auth("leitura")
    ).json()["itens"][0]
    done = client.post(
        f"/api/v1/followups/{fup['id']}/concluir",
        json={"resultado": "paciente retornou"},
        headers=auth("operacional"),
    )
    assert done.status_code == 200
    assert done.json()["status"] == "concluido"
    assert done.json()["concluido_em"] is not None
    blocked = client.get(
        f"/api/v1/followups/{fup['id']}/whatsapp-url",
        headers=auth("operacional"),
    )
    assert blocked.status_code == 409
    # nova tentativa reabre com nova data e LIMPA a conclusão anterior
    retry = client.post(
        f"/api/v1/followups/{fup['id']}/nova-tentativa",
        json={"nova_data": "2026-10-01"},
        headers=auth("operacional"),
    )
    assert retry.status_code == 200
    body = retry.json()
    assert body["status"] == "pendente"
    assert body["due_date"] == "2026-10-01"
    assert body["tentativas"] == 1
    assert body["concluido_em"] is None
    assert body["resultado"] is None
    # auditoria registrou a transição
    trilha = client.get(
        "/api/v1/auditoria?acao=followup.nova_tentativa", headers=auth("gestor")
    ).json()
    assert trilha["total"] >= 1


def test_followup_manual_sem_origem_nao_duplica(client, auth, person):
    """origem_id NULL também deduplica (índice parcial dedicado)."""
    first = client.post(
        "/api/v1/followups",
        json={"person_id": person["id"], "tipo": "manual", "due_date": "2026-09-01"},
        headers=auth("operacional"),
    )
    assert first.status_code == 201
    assert first.json()["motivo"] == "criado"
    second = client.post(
        "/api/v1/followups",
        json={"person_id": person["id"], "tipo": "manual", "due_date": "2026-09-15"},
        headers=auth("operacional"),
    )
    assert second.status_code == 201
    assert second.json()["motivo"] == "ja_existente"
    assert second.json()["id"] == first.json()["id"]
    lista = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=manual",
        headers=auth("leitura"),
    ).json()
    assert lista["total"] == 1


def test_cancelar_exame_cancela_followup(client, auth, person):
    """Mudança na origem sincroniza o follow-up na MESMA transação."""
    exam = _exame_realizado(client, auth, person["id"], "ESP-SYNC-000001").json()
    fups = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=pos_exame",
        headers=auth("leitura"),
    ).json()
    assert fups["total"] == 1
    cancel = client.patch(
        f"/api/v1/espirometrias/{exam['id']}",
        json={"status": "Cancelado"},
        headers=auth("operacional"),
    )
    assert cancel.json()["followup"]["motivo"] == "cancelado"
    pendentes = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=pos_exame&status=pendente",
        headers=auth("leitura"),
    ).json()
    assert pendentes["total"] == 0
    # reativar o exame recria o follow-up
    reativa = client.patch(
        f"/api/v1/espirometrias/{exam['id']}",
        json={"status": "Realizado"},
        headers=auth("operacional"),
    )
    assert reativa.json()["followup"]["motivo"] == "criado"


def test_mudar_data_do_exame_recalcula_vencimento(client, auth, person):
    from app.dates import add_months
    from datetime import date

    client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "data_exame": "10/01/2026",
              "status": "Realizado", "idempotency_key": "ESP-REC-000001"},
        headers=auth("operacional"),
    )
    fup = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=pos_exame",
        headers=auth("leitura"),
    ).json()["itens"][0]
    assert fup["due_date"] == add_months(date(2026, 1, 10), 6).isoformat()
    exam_id = client.get(
        f"/api/v1/espirometrias?person_id={person['id']}", headers=auth("leitura")
    ).json()["itens"][0]["id"]
    client.patch(
        f"/api/v1/espirometrias/{exam_id}",
        json={"data_exame": "20/02/2026"},
        headers=auth("operacional"),
    )
    fup2 = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=pos_exame",
        headers=auth("leitura"),
    ).json()["itens"][0]
    assert fup2["due_date"] == add_months(date(2026, 2, 20), 6).isoformat()
    assert fup2["id"] == fup["id"]  # mesmo registro, recalculado
