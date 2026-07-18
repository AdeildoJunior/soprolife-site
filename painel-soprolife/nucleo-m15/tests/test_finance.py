"""Financeiro: sem PII, idempotente, vinculado só por IDs técnicos."""


def test_criar_lancamento(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "espirometria",
            "descricao": "exame domiciliar",
            "valor": "250.00",
            "data_competencia": "06/2026",
            "status": "Recebido",
            "forma_pagamento": "Pix",
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["public_code"].startswith("LAN-")
    assert body["data_competencia"] == "2026-06-01"
    assert body["data_competencia_dia_assumido"] is True
    # Decimal serializado como string monetária, sem perda; moeda explícita
    assert body["valor"] == "250.00"
    assert body["moeda"] == "BRL"


def test_valor_decimal_sem_float(client, auth):
    """Decimal com quantização ROUND_HALF_UP em 2 casas."""
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "199.90"},
        headers=auth("gestor"),
    )
    assert resp.json()["valor"] == "199.90"
    # mais de 2 casas decimais é rejeitado na validação (fail-closed)
    bad = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "10.999"},
        headers=auth("gestor"),
    )
    assert bad.status_code == 422


def test_valor_negativo_rejeitado(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "despesa", "valor": "-10.00"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_lancamento_rejeita_campo_nome(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": 100.0, "nome": "Paciente Real"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_lancamento_rejeita_telefone_na_descricao(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": 100.0, "descricao": "pagar para 21 99999-0001"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_lancamento_rejeita_cpf_na_descricao(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": 100.0, "descricao": "CPF 123.456.789-00"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_lancamento_rejeita_nome_proprio_na_descricao(client, auth):
    """Validação semântica: texto com cara de nome próprio é recusado."""
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": 100.0, "descricao": "pagamento de Maria Silva"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_lancamento_rejeita_email_na_descricao(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": 100.0, "descricao": "cobrar via x@y.com"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_lancamento_rejeita_informacao_clinica(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": 100.0,
              "descricao": "exame com diagnóstico de asma"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_lancamento_idempotente(client, auth):
    payload = {
        "tipo": "receita",
        "valor": 300.0,
        "idempotency_key": "LAN-TESTE-000001",
    }
    first = client.post("/api/v1/lancamentos", json=payload, headers=auth("gestor"))
    second = client.post("/api/v1/lancamentos", json=payload, headers=auth("gestor"))
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotente"] is True


def test_lancamento_exige_gestor(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": 100.0},
        headers=auth("operacional"),
    )
    assert resp.status_code == 403


def test_lancamento_vinculado_a_exame_por_id(client, auth, person):
    exame = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "data_exame": "10/01/2026", "status": "Realizado"},
        headers=auth("operacional"),
    ).json()
    resp = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "valor": 250.0,
            "spirometry_exam_id": exame["id"],
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 201
    assert resp.json()["spirometry_exam_id"] == exame["id"]


def test_lancamento_rejeita_atendimentos_de_pessoas_diferentes(client, auth, person):
    outra = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Outra Pessoa Financeiro 001"},
        headers=auth("operacional"),
    ).json()
    exame = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "data_exame": "10/01/2026"},
        headers=auth("operacional"),
    ).json()
    consulta = client.post(
        "/api/v1/consultas",
        json={"person_id": outra["id"], "data_consulta": "10/01/2026"},
        headers=auth("operacional"),
    ).json()
    resp = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "valor": "100.00",
            "spirometry_exam_id": exame["id"],
            "consultation_id": consulta["id"],
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "financeiro_pessoas_incoerentes"


def test_lancamento_pcmso_rejeitado(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "100.00", "categoria": "PCMSO"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pcmso_fora_da_operacao"


def test_repasse_para_parceiro(client, auth):
    partner = client.post(
        "/api/v1/parceiros",
        json={"nome": "Clínica Repassa Teste", "status": "ativa"},
        headers=auth("operacional"),
    ).json()
    resp = client.post(
        "/api/v1/repasses",
        json={"partner_id": partner["id"], "valor": 80.0, "status": "previsto"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "previsto"
    assert resp.json()["valor"] == "80.00"


def test_repasse_idempotente_e_conflito(client, auth):
    partner = client.post(
        "/api/v1/parceiros",
        json={"nome": "Clínica Repasse Idem", "status": "ativa"},
        headers=auth("operacional"),
    ).json()
    payload = {"partner_id": partner["id"], "valor": "50.00",
               "idempotency_key": "REP-TESTE-000001"}
    first = client.post("/api/v1/repasses", json=payload, headers=auth("gestor"))
    second = client.post("/api/v1/repasses", json=payload, headers=auth("gestor"))
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotente"] is True
    # mesma chave + payload diferente -> 409
    conflito = client.post(
        "/api/v1/repasses",
        json={"partner_id": partner["id"], "valor": "99.00",
              "idempotency_key": "REP-TESTE-000001"},
        headers=auth("gestor"),
    )
    assert conflito.status_code == 409


def test_repasse_exige_gestor(client, auth):
    partner = client.post(
        "/api/v1/parceiros",
        json={"nome": "Clínica Repasse Papel", "status": "ativa"},
        headers=auth("operacional"),
    ).json()
    resp = client.post(
        "/api/v1/repasses",
        json={"partner_id": partner["id"], "valor": 10.0},
        headers=auth("operacional"),
    )
    assert resp.status_code == 403


def test_repasse_parceria_de_outro_parceiro(client, auth):
    partner_a = client.post(
        "/api/v1/parceiros", json={"nome": "Clínica Repasse A", "status": "ativa"},
        headers=auth("operacional"),
    ).json()
    partner_b = client.post(
        "/api/v1/parceiros", json={"nome": "Clínica Repasse B", "status": "ativa"},
        headers=auth("operacional"),
    ).json()
    parceria_b = client.post(
        "/api/v1/parcerias", json={"partner_id": partner_b["id"]},
        headers=auth("gestor"),
    ).json()
    resp = client.post(
        "/api/v1/repasses",
        json={"partner_id": partner_a["id"], "valor": 10.0,
              "partnership_id": parceria_b["id"]},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "parceria_de_outro_parceiro"


def test_repasse_pago_exige_data(client, auth):
    partner = client.post(
        "/api/v1/parceiros", json={"nome": "Clínica Repasse Pago", "status": "ativa"},
        headers=auth("operacional"),
    ).json()
    resp = client.post(
        "/api/v1/repasses",
        json={"partner_id": partner["id"], "valor": 10.0, "status": "pago"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_idempotencia_mesma_chave_payload_diferente_409(client, auth, person):
    payload = {"person_id": person["id"], "data_exame": "10/01/2026",
               "status": "Realizado", "idempotency_key": "ESP-CONF-000001"}
    first = client.post("/api/v1/espirometrias", json=payload, headers=auth("operacional"))
    assert first.status_code == 201
    alterado = dict(payload, data_exame="11/01/2026")
    conflito = client.post("/api/v1/espirometrias", json=alterado, headers=auth("operacional"))
    assert conflito.status_code == 409
    assert "Idempotency" in conflito.json()["erro"]["mensagem"]
