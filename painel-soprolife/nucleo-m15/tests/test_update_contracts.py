"""Contratos aditivos de edição da M15.3A — parceiro, unidade, contato,
parceria, lançamento e dados básicos de pessoa. Dados 100% sintéticos."""


def _partner(client, auth, nome="Clínica Exemplo Update"):
    resp = client.post(
        "/api/v1/parceiros",
        json={"nome": nome, "tipo": "clinica", "status": "prospecto"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _unit(client, auth, partner_id, nome="Unidade Centro"):
    resp = client.post(
        "/api/v1/unidades",
        json={"partner_id": partner_id, "nome": nome},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ----------------------------------------------------------------- parceiro

def test_atualiza_parceiro(client, auth):
    partner = _partner(client, auth)
    resp = client.patch(
        f"/api/v1/parceiros/{partner['id']}",
        json={"status": "ativa", "cidade": "Rio de Janeiro"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ativa" and body["cidade"] == "Rio de Janeiro"


def test_leitura_nao_edita_parceiro(client, auth):
    partner = _partner(client, auth, nome="Clínica Leitura Bloqueada")
    resp = client.patch(
        f"/api/v1/parceiros/{partner['id']}",
        json={"status": "ativa"},
        headers=auth("leitura"),
    )
    assert resp.status_code == 403


def test_parceiro_status_invalido_rejeitado(client, auth):
    partner = _partner(client, auth, nome="Clínica Status Inválido")
    resp = client.patch(
        f"/api/v1/parceiros/{partner['id']}",
        json={"status": "qualquer-coisa"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------ unidade

def test_atualiza_e_desativa_unidade(client, auth):
    partner = _partner(client, auth, nome="Clínica Unidade Edit")
    unit = _unit(client, auth, partner["id"])
    resp = client.patch(
        f"/api/v1/unidades/{unit['id']}",
        json={"bairro": "Ipanema", "ativo": False},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bairro"] == "Ipanema" and body["ativo"] is False


# ------------------------------------------------------------------ contato

def test_atualiza_contato_e_valida_unidade_cruzada(client, auth):
    p1 = _partner(client, auth, nome="Clínica Contato A")
    p2 = _partner(client, auth, nome="Clínica Contato B")
    unidade_de_p2 = _unit(client, auth, p2["id"], nome="Unidade da Outra")
    contato = client.post(
        "/api/v1/contatos-parceiros",
        json={"partner_id": p1["id"], "nome": "Contato Exemplo 001"},
        headers=auth("operacional"),
    ).json()
    ok = client.patch(
        f"/api/v1/contatos-parceiros/{contato['id']}",
        json={"cargo": "Coordenação", "principal": True},
        headers=auth("operacional"),
    )
    assert ok.status_code == 200
    assert ok.json()["cargo"] == "Coordenação"
    # unidade de OUTRO parceiro nunca pode ser vinculada (integridade cruzada)
    errado = client.patch(
        f"/api/v1/contatos-parceiros/{contato['id']}",
        json={"partner_unit_id": unidade_de_p2["id"]},
        headers=auth("operacional"),
    )
    assert errado.status_code in (409, 422)


# ----------------------------------------------------------------- parceria

def test_atualiza_parceria_exige_gestor(client, auth):
    partner = _partner(client, auth, nome="Clínica Parceria Edit")
    parceria = client.post(
        "/api/v1/parcerias",
        json={"partner_id": partner["id"], "modelo_repasse": "percentual",
              "percentual_repasse": "20.00"},
        headers=auth("gestor"),
    ).json()
    negado = client.patch(
        f"/api/v1/parcerias/{parceria['id']}",
        json={"status": "ativa"},
        headers=auth("operacional"),
    )
    assert negado.status_code == 403
    ok = client.patch(
        f"/api/v1/parcerias/{parceria['id']}",
        json={"status": "ativa", "data_inicio": "2026-07"},
        headers=auth("gestor"),
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "ativa"
    # data incompleta preserva original, precisão e dia assumido
    assert body["data_inicio_original"] == "2026-07"
    assert body["data_inicio_precisao"] == "mes"
    assert body["data_inicio_dia_assumido"] is True


# ---------------------------------------------------------------- lançamento

def _entry(client, auth):
    resp = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "categoria": "Espirometria", "valor": "250.00",
              "data_competencia": "2026-07-10"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_atualiza_lancamento_status_recebido(client, auth):
    entry = _entry(client, auth)
    sem_data = client.patch(
        f"/api/v1/lancamentos/{entry['id']}",
        json={"status": "Recebido"},
        headers=auth("gestor"),
    )
    assert sem_data.status_code == 422
    ok = client.patch(
        f"/api/v1/lancamentos/{entry['id']}",
        json={"status": "Recebido", "data_recebimento": "2026-07-15",
              "forma_pagamento": "Pix"},
        headers=auth("gestor"),
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "Recebido" and body["forma_pagamento"] == "Pix"
    # valor permanece imutável
    assert body["valor"] == "250.00"


def test_lancamento_update_exige_gestor_e_rejeita_pii(client, auth):
    entry = _entry(client, auth)
    negado = client.patch(
        f"/api/v1/lancamentos/{entry['id']}",
        json={"status": "Cancelado"},
        headers=auth("operacional"),
    )
    assert negado.status_code == 403
    com_pii = client.patch(
        f"/api/v1/lancamentos/{entry['id']}",
        json={"descricao": "pagamento do paciente João da Silva tel 21 99999-0000"},
        headers=auth("gestor"),
    )
    assert com_pii.status_code == 422
    valor_imutavel = client.patch(
        f"/api/v1/lancamentos/{entry['id']}",
        json={"valor": "999.00"},
        headers=auth("gestor"),
    )
    assert valor_imutavel.status_code == 422  # extra='forbid'


# ------------------------------------------------------------------- pessoa

def test_atualiza_nome_e_nascimento_de_pessoa(client, auth, person):
    resp = client.patch(
        f"/api/v1/pessoas/{person['id']}",
        json={"nome_completo": "Pessoa Teste Corrigida 001",
              "data_nascimento": "1980-05-04"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["nome_completo"] == "Pessoa Teste Corrigida 001"
    assert body["data_nascimento"] == "1980-05-04"
    # nome normalizado acompanha: busca pelo nome novo encontra a pessoa
    busca = client.post(
        "/api/v1/pessoas/busca",
        json={"q": "corrigida"},
        headers=auth("leitura"),
    )
    codigos = [i["public_code"] for i in busca.json()["itens"]]
    assert person["public_code"] in codigos
