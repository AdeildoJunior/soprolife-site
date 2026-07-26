"""Testes da Central de Cadastros (M16) — fluxo canônico de entrada de dados.

Cobre os critérios de aceitação do fluxo único de cadastro:
lead → paciente (com aviso de duplicado) → espirometria → consulta →
lançamento financeiro vinculado sem PII, com contexto derivado por relação.
Inclui o novo status de exame "Laudo Liberado" e a busca por telefone.
"""

from tests.conftest import SYNTH_PHONE, SYNTH_PHONE_NORM

# segundo telefone sintético não discável (prefixo 0000)
SYNTH_PHONE_2 = "(21) 0000-9002"


def _criar_pessoa(client, auth, nome="Paciente Central 001", fone=SYNTH_PHONE):
    resp = client.post(
        "/api/v1/pessoas",
        json={
            "nome_completo": nome,
            "contatos": [{"tipo": "whatsapp", "valor": fone, "principal": True}],
            "consentimento_whatsapp": "concedido",
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------ status "Laudo Liberado"


def test_exame_aceita_status_laudo_liberado(client, auth, person):
    resp = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "2026-07-20",
            "status": "Laudo Liberado",
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "Laudo Liberado"
    # Laudo Liberado pressupõe exame realizado: follow-up de 6 meses ativo
    assert data["followup"]["id"] is not None


def test_transicao_realizado_para_laudo_liberado_mantem_followup(client, auth, person):
    criado = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "2026-07-20",
            "status": "Realizado",
        },
        headers=auth("operacional"),
    ).json()
    fup_original = criado["followup"]["id"]
    assert fup_original is not None
    atualizado = client.patch(
        f"/api/v1/espirometrias/{criado['id']}",
        json={"status": "Laudo Liberado"},
        headers=auth("operacional"),
    )
    assert atualizado.status_code == 200, atualizado.text
    data = atualizado.json()
    assert data["status"] == "Laudo Liberado"
    assert data["followup"]["id"] == fup_original


def test_exame_broncodilatador_persistido_e_editavel(client, auth, person):
    criado = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "2026-07-20",
            "status": "Realizado",
            "broncodilatador": True,
        },
        headers=auth("operacional"),
    )
    assert criado.status_code == 201, criado.text
    assert criado.json()["broncodilatador"] is True
    editado = client.patch(
        f"/api/v1/espirometrias/{criado.json()['id']}",
        json={"broncodilatador": False},
        headers=auth("operacional"),
    ).json()
    assert editado["broncodilatador"] is False
    # ausência do campo = não informado (NULL), nunca inferido
    sem_campo = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "status": "Aguardando"},
        headers=auth("operacional"),
    ).json()
    assert sem_campo["broncodilatador"] is None


def test_exame_rejeita_status_fora_do_vocabulario(client, auth, person):
    resp = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "status": "Exame realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


# ------------------------------------------------------------ busca por telefone


def test_busca_pessoas_por_telefone_normalizado(client, auth, person):
    resp = client.post(
        "/api/v1/pessoas/busca",
        json={"q": SYNTH_PHONE},
        headers=auth("leitura"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["itens"][0]["id"] == person["id"]
    # variação de formatação do mesmo número encontra a mesma pessoa
    resp2 = client.post(
        "/api/v1/pessoas/busca",
        json={"q": SYNTH_PHONE_NORM},
        headers=auth("leitura"),
    )
    assert resp2.json()["total"] == 1


def test_busca_pessoas_por_nome_continua_funcionando(client, auth, person):
    resp = client.post(
        "/api/v1/pessoas/busca",
        json={"q": "Pessoa Teste"},
        headers=auth("leitura"),
    )
    assert resp.json()["total"] == 1


# ------------------------------------------------- pré-checagem de duplicados


def test_verificar_duplicados_por_nome_e_telefone(client, auth, person):
    resp = client.post(
        "/api/v1/pessoas/verificar-duplicados",
        json={"nome_completo": "Pessoa Teste 001", "telefones": [SYNTH_PHONE]},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    cand = data["candidatos"][0]
    assert cand["public_code"] == person["public_code"]
    assert cand["motivo"] == "telefone_igual"


def test_verificar_duplicados_sem_candidato(client, auth, person):
    resp = client.post(
        "/api/v1/pessoas/verificar-duplicados",
        json={"nome_completo": "Nome Inexistente Zzz", "telefones": []},
        headers=auth("operacional"),
    )
    assert resp.json()["total"] == 0


def test_verificar_duplicados_nao_cria_nada(client, auth, person):
    antes = client.get("/api/v1/pessoas?tamanho=1", headers=auth("leitura")).json()["total"]
    client.post(
        "/api/v1/pessoas/verificar-duplicados",
        json={"nome_completo": "Pessoa Teste 001", "telefones": [SYNTH_PHONE]},
        headers=auth("operacional"),
    )
    depois = client.get("/api/v1/pessoas?tamanho=1", headers=auth("leitura")).json()["total"]
    assert antes == depois


def test_verificar_duplicados_exige_papel_operacional(client, auth):
    resp = client.post(
        "/api/v1/pessoas/verificar-duplicados",
        json={"nome_completo": "Qualquer Nome"},
        headers=auth("leitura"),
    )
    assert resp.status_code == 403


# ------------------------------------------- contexto derivado no financeiro


def test_lancamento_com_contexto_derivado_do_exame(client, auth, person):
    exame = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person["id"],
            "data_exame": "2026-07-20",
            "status": "Realizado",
        },
        headers=auth("operacional"),
    ).json()
    lanc = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "Espirometria",
            "valor": "250.00",
            "data_competencia": "2026-07-20",
            "status": "Recebido",
            "data_recebimento": "2026-07-20",
            "forma_pagamento": "Pix",
            "spirometry_exam_id": exame["id"],
        },
        headers=auth("gestor"),
    )
    assert lanc.status_code == 201, lanc.text
    # o registro persistido nunca carrega nome/telefone
    body = lanc.json()
    assert "nome" not in str(body.get("descricao") or "")
    # lista com contexto derivado por relação técnica
    lista = client.get(
        "/api/v1/lancamentos?incluir_contexto=true", headers=auth("leitura")
    ).json()
    item = next(i for i in lista["itens"] if i["id"] == body["id"])
    assert item["contexto"]["exame"]["public_code"] == exame["public_code"]
    assert item["contexto"]["exame"]["pessoa"]["nome_completo"] == "Pessoa Teste 001"
    # sem o parâmetro, resposta permanece sem contexto (compatível)
    lista2 = client.get("/api/v1/lancamentos", headers=auth("leitura")).json()
    assert "contexto" not in lista2["itens"][0]


def test_lancamento_rejeita_pii_na_descricao(client, auth, person):
    exame = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "status": "Aguardando"},
        headers=auth("operacional"),
    ).json()
    resp = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "valor": "250.00",
            "descricao": f"Exame de Pessoa Teste tel {SYNTH_PHONE}",
            "spirometry_exam_id": exame["id"],
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 422


def test_busca_lancamentos_por_nome_derivado_e_codigo(client, auth, person):
    exame = client.post(
        "/api/v1/espirometrias",
        json={"person_id": person["id"], "data_exame": "2026-07-21", "status": "Realizado"},
        headers=auth("operacional"),
    ).json()
    lanc = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "Espirometria",
            "valor": "230.00",
            "status": "Pendente",
            "spirometry_exam_id": exame["id"],
        },
        headers=auth("gestor"),
    ).json()
    # por nome do paciente (derivado por relação — nome não está no lançamento)
    por_nome = client.post(
        "/api/v1/lancamentos/busca",
        json={"q": "Pessoa Teste"},
        headers=auth("leitura"),
    ).json()
    assert any(i["id"] == lanc["id"] for i in por_nome["itens"])
    # por código técnico do exame
    por_esp = client.post(
        "/api/v1/lancamentos/busca",
        json={"q": exame["public_code"]},
        headers=auth("leitura"),
    ).json()
    assert por_esp["total"] >= 1
    assert all(i["contexto"]["exame"]["public_code"] == exame["public_code"]
               for i in por_esp["itens"])
    # por código do próprio lançamento
    por_lan = client.post(
        "/api/v1/lancamentos/busca",
        json={"q": lanc["public_code"]},
        headers=auth("leitura"),
    ).json()
    assert por_lan["total"] == 1


# --------------------------------------------------- fluxo de aceitação completo


def test_fluxo_canonico_completo(client, auth):
    """Lead → aviso de duplicado → espirometria → consulta → financeiro."""
    # 1-2. pessoa + lead aparecem imediatamente nas listas
    pessoa = _criar_pessoa(client, auth, "Fluxo Canonico 001", SYNTH_PHONE_2)
    lead = client.post(
        "/api/v1/leads",
        json={
            "person_id": pessoa["id"],
            "origem": "site",
            "canal_entrada": "whatsapp",
            "servico_interesse": "espirometria",
            "etapa": "novo",
            "responsavel": "Adeildo",
        },
        headers=auth("operacional"),
    )
    assert lead.status_code == 201, lead.text
    lista_leads = client.get("/api/v1/leads?tamanho=50", headers=auth("leitura")).json()
    assert any(l["id"] == lead.json()["id"] for l in lista_leads["itens"])

    # 3-4. recriar a mesma pessoa gera aviso de duplicado na pré-checagem
    dup = client.post(
        "/api/v1/pessoas/verificar-duplicados",
        json={"nome_completo": "Fluxo Canonico 001", "telefones": [SYNTH_PHONE_2]},
        headers=auth("operacional"),
    ).json()
    assert dup["total"] == 1

    # 5-6. espirometria aparece no histórico da pessoa
    exame = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": pessoa["id"],
            "data_exame": "2026-07-22",
            "status": "Realizado",
            "modalidade": "residencial",
        },
        headers=auth("operacional"),
    ).json()
    historico = client.get(
        f"/api/v1/espirometrias?person_id={pessoa['id']}", headers=auth("leitura")
    ).json()
    assert any(e["id"] == exame["id"] for e in historico["itens"])

    # 7. consulta vinculada à mesma pessoa
    consulta = client.post(
        "/api/v1/consultas",
        json={
            "person_id": pessoa["id"],
            "data_consulta": "2026-07-22",
            "status": "Realizada",
            "modalidade": "teleconsulta",
        },
        headers=auth("operacional"),
    )
    assert consulta.status_code == 201, consulta.text

    # 8-9. lançamento vinculado ao exame sem copiar telefone/nome; contexto
    # do paciente vem por relação técnica
    lanc = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "Espirometria",
            "valor": "250.00",
            "data_competencia": "2026-07-22",
            "status": "Recebido",
            "data_recebimento": "2026-07-22",
            "spirometry_exam_id": exame["id"],
        },
        headers=auth("gestor"),
    )
    assert lanc.status_code == 201, lanc.text
    ctx = client.get(
        "/api/v1/lancamentos?incluir_contexto=true", headers=auth("leitura")
    ).json()
    item = next(i for i in ctx["itens"] if i["id"] == lanc.json()["id"])
    assert item["contexto"]["exame"]["pessoa"]["nome_completo"] == "Fluxo Canonico 001"

    # 14. papel leitura não cria nada
    for path, payload in (
        ("/api/v1/pessoas", {"nome_completo": "Bloqueado Leitura"}),
        ("/api/v1/leads", {"person_id": pessoa["id"]}),
        ("/api/v1/espirometrias", {"person_id": pessoa["id"]}),
        ("/api/v1/lancamentos", {"tipo": "receita", "valor": "10.00"}),
    ):
        r = client.post(path, json=payload, headers=auth("leitura"))
        assert r.status_code == 403, f"{path}: {r.status_code}"
    # operacional não cria lançamento (gestor obrigatório)
    r = client.post(
        "/api/v1/lancamentos",
        json={"tipo": "receita", "valor": "10.00"},
        headers=auth("operacional"),
    )
    assert r.status_code == 403

    # 15. erro de validação não deixa entidade parcial
    antes = client.get("/api/v1/espirometrias?tamanho=1", headers=auth("leitura")).json()["total"]
    erro = client.post(
        "/api/v1/espirometrias",
        json={"person_id": "id-inexistente", "status": "Realizado"},
        headers=auth("operacional"),
    )
    assert erro.status_code == 404
    depois = client.get("/api/v1/espirometrias?tamanho=1", headers=auth("leitura")).json()["total"]
    assert antes == depois


# ------------------- M23.1: cadastro "apenas a pessoa" não cria atendimento


def test_cadastro_apenas_pessoa_nao_cria_nenhum_atendimento(client, auth):
    """Central de Cadastros: '+ Cadastrar nova pessoa' com a opção 'Cadastrar
    apenas a pessoa, sem criar exame ou consulta' deve chamar SOMENTE
    POST /pessoas. Prova pelo backend (não pela UI) que nenhuma espirometria,
    consulta ou lançamento financeiro nasce como efeito colateral: a linha do
    tempo do paciente deve conter exatamente o evento de cadastro."""
    pessoa = _criar_pessoa(client, auth, "Somente Pessoa 001", "(21) 0000-9003")

    timeline = client.get(
        f"/api/v1/crm/pacientes/{pessoa['id']}/timeline", headers=auth("leitura")
    ).json()
    assert len(timeline["eventos"]) == 1
    assert timeline["eventos"][0]["tipo"] == "cadastro"

    esp = client.get(
        f"/api/v1/espirometrias?person_id={pessoa['id']}", headers=auth("leitura")
    ).json()
    assert esp["total"] == 0
    con = client.get(
        f"/api/v1/consultas?person_id={pessoa['id']}", headers=auth("leitura")
    ).json()
    assert con["total"] == 0
    lanc = client.post(
        "/api/v1/lancamentos/busca",
        json={"q": pessoa["public_code"]},
        headers=auth("leitura"),
    ).json()
    assert lanc["total"] == 0


def test_busca_de_pessoa_existente_nunca_cria_duplicata(client, auth):
    """Selecionar uma pessoa já existente (fluxo de busca) nunca deve chamar
    POST /pessoas de novo — GET/busca são inertes por natureza; o contrato
    real é que o total de pessoas não muda ao simplesmente localizar e
    reutilizar quem já existe (o que a Central faz via picker.resolve(),
    que só cria quando modoNova está ativo)."""
    pessoa = _criar_pessoa(client, auth, "Ja Existe 001", "(21) 0000-9004")
    total_antes = client.get("/api/v1/pessoas", headers=auth("leitura")).json()["total"]

    encontrada = client.post(
        "/api/v1/pessoas/busca",
        json={"q": "Ja Existe 001", "tamanho": 8},
        headers=auth("operacional"),
    ).json()
    assert any(p["id"] == pessoa["id"] for p in encontrada["itens"])

    por_codigo = client.get(
        f"/api/v1/pessoas/{pessoa['id']}", headers=auth("leitura")
    ).json()
    assert por_codigo["id"] == pessoa["id"]

    total_depois = client.get("/api/v1/pessoas", headers=auth("leitura")).json()["total"]
    assert total_antes == total_depois
