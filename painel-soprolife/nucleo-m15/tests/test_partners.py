"""Parceiros, unidades, contatos, parcerias, encaminhamentos e integridade."""


def _partner(client, auth, nome="Clínica Teste Parceiros"):
    return client.post(
        "/api/v1/parceiros",
        json={"nome": nome, "status": "ativa", "cidade": "Rio de Janeiro"},
        headers=auth("operacional"),
    ).json()


def test_parceiro_unidade_contato_parceria(client, auth):
    partner = _partner(client, auth)
    assert partner["public_code"].startswith("CLI-")
    unit = client.post(
        "/api/v1/unidades",
        json={"partner_id": partner["id"], "nome": "Unidade Teste", "bairro": "Bairro Sintético"},
        headers=auth("operacional"),
    ).json()
    assert unit["public_code"].startswith("UNI-")
    contact = client.post(
        "/api/v1/contatos-parceiros",
        json={
            "partner_id": partner["id"],
            "partner_unit_id": unit["id"],
            "nome": "Contato Teste",
            "cargo": "Diretor Médico",
            "principal": True,
        },
        headers=auth("operacional"),
    ).json()
    assert contact["public_code"].startswith("CTT-")
    partnership = client.post(
        "/api/v1/parcerias",
        json={
            "partner_id": partner["id"],
            "status": "ativa",
            "data_inicio": "06/2026",
            "responsavel_followup": "parceiro",
            "modelo_repasse": "percentual",
            "percentual_repasse": 20,
        },
        headers=auth("gestor"),
    ).json()
    assert partnership["public_code"].startswith("PAR-")
    assert partnership["data_inicio"] == "2026-06-01"
    assert partnership["data_inicio_dia_assumido"] is True
    assert partnership["percentual_repasse"] == "20.00"
    detail = client.get(
        f"/api/v1/parceiros/{partner['id']}", headers=auth("leitura")
    ).json()
    assert detail["unidades"]["total"] == 1
    assert detail["contatos"]["total"] == 1
    assert detail["parcerias"]["total"] == 1


def test_parceria_exige_gestor(client, auth):
    partner = _partner(client, auth, "Clínica Parceria Papel")
    resp = client.post(
        "/api/v1/parcerias",
        json={"partner_id": partner["id"], "percentual_repasse": 30},
        headers=auth("operacional"),
    )
    assert resp.status_code == 403


def test_encaminhamento_ciclo_completo(client, auth, person):
    partner = _partner(client, auth, "Clínica Encaminha Teste")
    unit = client.post(
        "/api/v1/unidades",
        json={"partner_id": partner["id"], "nome": "Unidade Enc"},
        headers=auth("operacional"),
    ).json()
    referral = client.post(
        "/api/v1/encaminhamentos",
        json={
            "person_id": person["id"],
            "partner_id": partner["id"],
            "partner_unit_id": unit["id"],
            "data_encaminhamento": "01/07/2026",
            "servico_solicitado": "espirometria",
            "autorizacao_contato_soprolife": True,
            "responsavel_followup": "parceiro",
        },
        headers=auth("operacional"),
    )
    assert referral.status_code == 201, referral.text
    body = referral.json()
    assert body["public_code"].startswith("ENC-")
    assert body["status"] == "Recebido da clínica"
    assert body["followup"]["motivo"] == "criado"
    fup = client.get(
        f"/api/v1/followups?person_id={person['id']}&tipo=encaminhamento_parceiro",
        headers=auth("leitura"),
    ).json()["itens"][0]
    assert fup["controlado_por_parceiro"] is True
    assert fup["partner_id"] == partner["id"]
    # ciclo: realizado com exame vinculado -> laudo enviado
    exame = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "10/07/2026",
            "status": "Realizado",
            "modalidade": "clinica_parceira",
            "partner_id": partner["id"],
            "partner_unit_id": unit["id"],
        },
        headers=auth("operacional"),
    ).json()
    updated = client.patch(
        f"/api/v1/encaminhamentos/{body['id']}",
        json={
            "status": "Laudo enviado",
            "data_realizacao": "2026-07-10",
            "spirometry_exam_id": exame["id"],
            "laudo_enviado": True,
            "data_envio_laudo": "2026-07-11",
        },
        headers=auth("operacional"),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["laudo_enviado"] is True
    # valores: endpoint financeiro exclusivo de gestor
    denied = client.patch(
        f"/api/v1/encaminhamentos/{body['id']}/financeiro",
        json={"valor_cobrado": 200},
        headers=auth("operacional"),
    )
    assert denied.status_code == 403
    fin = client.patch(
        f"/api/v1/encaminhamentos/{body['id']}/financeiro",
        json={"valor_cobrado": 200, "valor_recebido": 200,
              "tipo_repasse": "percentual", "percentual_repasse": 20,
              "valor_repasse": 40, "status_repasse": "aguardando"},
        headers=auth("gestor"),
    )
    assert fin.status_code == 200, fin.text
    assert fin.json()["valor_repasse"] == "40.00"


def test_criar_encaminhamento_com_valor_rejeitado(client, auth, person):
    """Campos financeiros saíram do payload operacional (extra=forbid)."""
    partner = _partner(client, auth, "Clínica Sem Valor No Create")
    resp = client.post(
        "/api/v1/encaminhamentos",
        json={"person_id": person["id"], "partner_id": partner["id"],
              "valor_cobrado": 100},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


def test_laudo_sem_atendimento_bloqueado(client, auth, person):
    partner = _partner(client, auth, "Clínica Laudo Cedo")
    referral = client.post(
        "/api/v1/encaminhamentos",
        json={"person_id": person["id"], "partner_id": partner["id"]},
        headers=auth("operacional"),
    ).json()
    resp = client.patch(
        f"/api/v1/encaminhamentos/{referral['id']}",
        json={"laudo_enviado": True},
        headers=auth("operacional"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "laudo_sem_atendimento"


def test_autorizacao_desconhecida_fail_closed(client, auth, person):
    """autorizacao_contato_soprolife=None NÃO cria follow-up (fail-closed)."""
    partner = _partner(client, auth, "Clínica Autorização Desconhecida")
    referral = client.post(
        "/api/v1/encaminhamentos",
        json={"person_id": person["id"], "partner_id": partner["id"]},
        headers=auth("operacional"),
    ).json()
    assert referral["followup"]["motivo"] == "nao_aplicavel"
    # autorização concedida depois -> follow-up nasce na atualização
    updated = client.patch(
        f"/api/v1/encaminhamentos/{referral['id']}",
        json={"autorizacao_contato_soprolife": True},
        headers=auth("operacional"),
    ).json()
    assert updated["followup"]["motivo"] == "criado"
    # autorização retirada -> follow-up cancelado NA MESMA transação
    revoked = client.patch(
        f"/api/v1/encaminhamentos/{referral['id']}",
        json={"autorizacao_contato_soprolife": False},
        headers=auth("operacional"),
    ).json()
    assert revoked["followup"]["motivo"] == "cancelado"


def test_unidade_de_outro_parceiro_rejeitada(client, auth, person):
    partner_a = _partner(client, auth, "Clínica Integridade A")
    partner_b = _partner(client, auth, "Clínica Integridade B")
    unit_b = client.post(
        "/api/v1/unidades",
        json={"partner_id": partner_b["id"], "nome": "Unidade do B"},
        headers=auth("operacional"),
    ).json()
    resp = client.post(
        "/api/v1/encaminhamentos",
        json={"person_id": person["id"], "partner_id": partner_a["id"],
              "partner_unit_id": unit_b["id"]},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "unidade_de_outro_parceiro"


def test_exame_de_outra_pessoa_rejeitado(client, auth, person):
    partner = _partner(client, auth, "Clínica Exame Alheio")
    outra = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Outra Pessoa Exame"},
        headers=auth("operacional"),
    ).json()
    exame_outra = client.post(
        "/api/v1/espirometrias",
        json={"person_id": outra["id"], "data_exame": "01/07/2026", "status": "Realizado"},
        headers=auth("operacional"),
    ).json()
    referral = client.post(
        "/api/v1/encaminhamentos",
        json={"person_id": person["id"], "partner_id": partner["id"]},
        headers=auth("operacional"),
    ).json()
    resp = client.patch(
        f"/api/v1/encaminhamentos/{referral['id']}",
        json={"spirometry_exam_id": exame_outra["id"]},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "exame_de_outra_pessoa"


def test_status_encaminhamento_invalido(client, auth, person):
    partner = _partner(client, auth, "Clínica Status Inválido")
    resp = client.post(
        "/api/v1/encaminhamentos",
        json={"person_id": person["id"], "partner_id": partner["id"],
              "status": "Status Inventado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
