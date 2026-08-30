"""M18 — conciliação de exames extra-Pastore: alvo, vinculado, pendente,
lote atômico com bloqueio de soma divergente."""

from decimal import Decimal

SYNTH_PHONE = "(21) 0000-9001"


def _criar_exame(client, auth, person_id, data_exame="10/07/2026"):
    resp = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person_id, "data_exame": data_exame, "status": "Realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_alvo_sempre_3044_79(client, auth, person):
    resp = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura"))
    assert resp.status_code == 200
    assert resp.json()["total_alvo"] == "3044.79"
    assert "Total histórico informado" in resp.json()["rotulo"]


def test_exame_pastore_excluido_do_alvo(client, auth, person):
    # cria parceiro Pastore e exame vinculado a ele
    resp = client.post(
        "/api/v1/parceiros",
        json={"nome": "Pastore", "tipo": "clinica"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 201, resp.text
    partner_id = resp.json()["id"]
    resp = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person["id"], "data_exame": "10/07/2026",
            "status": "Realizado", "partner_id": partner_id,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text

    conc = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    codes = {e["exam_public_code"] for e in conc["pendentes"] + conc["conciliados"]}
    assert resp.json()["public_code"] not in codes


def test_exame_sem_lancamento_aparece_pendente(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    conc = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    pendente_codes = {e["exam_public_code"] for e in conc["pendentes"]}
    assert exam["public_code"] in pendente_codes


def test_exame_com_lancamento_aparece_conciliado(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    resp = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita", "categoria": "Espirometria", "valor": "220.00",
            "status": "Recebido", "spirometry_exam_id": exam["id"],
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 201, resp.text

    conc = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    conciliado_codes = {e["exam_public_code"] for e in conc["conciliados"]}
    assert exam["public_code"] in conciliado_codes
    assert conc["total_vinculado"] == "220.00"
    assert conc["total_pendente"] == "2824.79"


def test_lote_bloqueia_soma_divergente(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    conc = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    pendente = conc["total_pendente"]

    resp = client.post(
        "/api/v1/financeiro/conciliacao/extra-pastore/lote",
        json={
            "itens": [{"spirometry_exam_id": exam["id"], "valor": "1.00"}],
            "total_esperado": pendente,
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["erro"]["mensagem"]["codigo"] == "soma_nao_bate"

    # nada foi criado
    conc2 = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    assert conc2["total_vinculado"] == "0.00"


def test_lote_bloqueia_total_esperado_desatualizado(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    resp = client.post(
        "/api/v1/financeiro/conciliacao/extra-pastore/lote",
        json={
            "itens": [{"spirometry_exam_id": exam["id"], "valor": "3044.79"}],
            "total_esperado": "999.99",  # desatualizado de propósito
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "total_esperado_desatualizado"


def test_lote_commita_atomicamente_quando_soma_bate(client, auth, person):
    exam1 = _criar_exame(client, auth, person["id"], "10/07/2026")
    exam2 = _criar_exame(client, auth, person["id"], "11/07/2026")
    conc = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    pendente = Decimal(conc["total_pendente"])

    resp = client.post(
        "/api/v1/financeiro/conciliacao/extra-pastore/lote",
        json={
            "itens": [
                {"spirometry_exam_id": exam1["id"], "valor": "100.00"},
                {"spirometry_exam_id": exam2["id"], "valor": str(pendente - Decimal("100.00"))},
            ],
            "total_esperado": str(pendente),
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["criados"]) == 2

    conc2 = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    assert conc2["total_pendente"] == "0.00"
    assert conc2["exames_pendentes"] == 0


def test_lote_rejeita_exame_ja_conciliado(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "220.00", "spirometry_exam_id": exam["id"]},
        headers=auth("gestor"),
    )
    conc = client.get("/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")).json()
    resp = client.post(
        "/api/v1/financeiro/conciliacao/extra-pastore/lote",
        json={
            "itens": [{"spirometry_exam_id": exam["id"], "valor": "50.00"}],
            "total_esperado": conc["total_pendente"],
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "exame_ja_conciliado"


def test_lote_rejeita_leitura_role(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    resp = client.post(
        "/api/v1/financeiro/conciliacao/extra-pastore/lote",
        json={"itens": [{"spirometry_exam_id": exam["id"], "valor": "1.00"}], "total_esperado": "1.00"},
        headers=auth("leitura"),
    )
    assert resp.status_code == 403


# ------------------------------------------------------------------ M26
#
# O alvo histórico é fechado; a lista de exames próprios não é. Quando a
# operação voltou a faturar (ESP-000038 e ESP-000039, ago/2026), o painel
# passou a exibir "pendente de −R$ 450,00" — dívida histórica não fica
# negativa, ela acaba.


def _lancar(client, auth, exam_id, valor):
    resp = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita", "categoria": "Espirometria", "valor": valor,
            "status": "Recebido", "data_recebimento": "2026-07-10",
            "forma_pagamento": "Pix", "spirometry_exam_id": exam_id,
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_receita_acima_do_alvo_nao_vira_pendente_negativo(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    _lancar(client, auth, exam["id"], "3500.00")

    conc = client.get(
        "/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")
    ).json()
    assert conc["total_vinculado"] == "3500.00"
    assert conc["total_pendente"] == "0.00"
    assert conc["total_alem_do_alvo"] == "455.21"
    assert conc["alvo_conciliado"] is True


def test_alem_do_alvo_e_zero_enquanto_falta_conciliar(client, auth, person):
    exam = _criar_exame(client, auth, person["id"])
    _lancar(client, auth, exam["id"], "1000.00")

    conc = client.get(
        "/api/v1/financeiro/conciliacao/extra-pastore", headers=auth("leitura")
    ).json()
    assert conc["total_pendente"] == "2044.79"
    assert conc["total_alem_do_alvo"] == "0.00"
    assert conc["alvo_conciliado"] is False


def test_lote_recusa_envio_quando_o_alvo_ja_foi_ultrapassado(client, auth, person):
    conciliado = _criar_exame(client, auth, person["id"])
    _lancar(client, auth, conciliado["id"], "3500.00")
    pendente = _criar_exame(client, auth, person["id"], data_exame="11/07/2026")

    resp = client.post(
        "/api/v1/financeiro/conciliacao/extra-pastore/lote",
        json={
            "itens": [{"spirometry_exam_id": pendente["id"], "valor": "100.00"}],
            "total_esperado": "100.00",
        },
        headers=auth("gestor"),
    )
    # O servidor responde com o pendente REAL — 0,00, não −455,21. O alvo
    # histórico acabou; conciliar contra ele deixou de fazer sentido, e a
    # mensagem devolve o número que o painel exibe.
    assert resp.status_code == 409
    erro = resp.json()["erro"]["mensagem"]
    assert erro["codigo"] == "total_esperado_desatualizado"
    assert erro["pendente_atual"] == "0.00"
