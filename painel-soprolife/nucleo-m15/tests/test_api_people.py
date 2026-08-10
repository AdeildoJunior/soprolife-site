"""Pessoas: cadastro canônico, candidatos de identidade, consentimento."""


def test_criar_pessoa_com_codigo_publico(client, auth):
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Maria Sintetica"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["public_code"].startswith("PES-")
    assert len(body["public_code"]) == 10
    assert body["nao_contatar"] is False


def test_telefone_igual_gera_candidato_sem_fusao(client, auth, person):
    from tests.conftest import SYNTH_PHONE

    resp = client.post(
        "/api/v1/pessoas",
        json={
            "nome_completo": "Outra Pessoa Homonima",
            "contatos": [{"tipo": "whatsapp", "valor": SYNTH_PHONE, "principal": True}],
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["candidatos_identidade"] == 1
    # sem fusão: as duas pessoas existem
    lista = client.get("/api/v1/pessoas", headers=auth("leitura")).json()
    assert lista["total"] == 2
    candidatos = client.get(
        "/api/v1/identidade/candidatos", headers=auth("gestor")
    ).json()
    assert candidatos["total"] == 1
    assert candidatos["itens"][0]["motivo"] == "telefone_igual"
    assert candidatos["itens"][0]["status"] == "pendente"


def test_nome_igual_gera_candidato_sem_fusao(client, auth):
    for _ in range(2):
        resp = client.post(
            "/api/v1/pessoas",
            json={"nome_completo": "João da Silva Sintético"},
            headers=auth("operacional"),
        )
        assert resp.status_code == 201
    lista = client.get("/api/v1/pessoas", headers=auth("leitura")).json()
    assert lista["total"] == 2  # NUNCA funde por nome


def test_marcar_nao_contatar(client, auth, person):
    resp = client.patch(
        f"/api/v1/pessoas/{person['id']}",
        json={"nao_contatar": True},
        headers=auth("operacional"),
    )
    assert resp.status_code == 200
    assert resp.json()["nao_contatar"] is True


def test_consentimento_rastreavel(client, auth, person):
    resp = client.post(
        f"/api/v1/pessoas/{person['id']}/consentimentos",
        json={"canal": "whatsapp", "status": "revogado", "origem": "pedido do titular"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    historico = client.get(
        f"/api/v1/pessoas/{person['id']}/consentimentos", headers=auth("leitura")
    ).json()["itens"]
    assert len(historico) == 2  # concedido (cadastro) + revogado
    assert historico[0]["status"] == "revogado"


def test_payload_com_campo_extra_rejeitado(client, auth):
    """`extra='forbid'`: campo desconhecido nunca é aceito em silêncio.

    O exemplo original era `cpf`, que a M25.18 transformou em campo REAL do
    cadastro. Trocado por um campo que continua não existindo — senão este
    teste passaria a medir a validação do CPF em vez da recusa de payload
    desconhecido, que é o que ele existe para proteger.
    """

    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Teste", "campo_que_nao_existe": "x"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "validacao"


def test_cpf_invalido_e_recusado_com_codigo_proprio(client, auth):
    """M25.18 — CPF preenchido e inválido não entra no cadastro."""

    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Teste", "cpf": "111.222.333-44"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "cpf_invalido"


def test_paginacao(client, auth):
    for i in range(5):
        client.post(
            "/api/v1/pessoas",
            json={"nome_completo": f"Pessoa Paginada {i:02d}"},
            headers=auth("operacional"),
        )
    page = client.get(
        "/api/v1/pessoas?pagina=2&tamanho=2", headers=auth("leitura")
    ).json()
    assert page["total"] == 5
    assert page["pagina"] == 2
    assert len(page["itens"]) == 2


def test_busca_por_nome_via_post(client, auth):
    """Busca por nome vai no CORPO (POST) — nome nunca em query string/logs."""
    client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Márcia Açúcar"},
        headers=auth("operacional"),
    )
    found = client.post(
        "/api/v1/pessoas/busca",
        json={"q": "marcia acucar"},
        headers=auth("leitura"),
    ).json()
    assert found["total"] == 1


def test_get_pessoas_nao_aceita_busca_por_query(client, auth):
    """O parâmetro q foi removido do GET (PII em access log)."""
    resp = client.get("/api/v1/pessoas?q=maria", headers=auth("leitura"))
    # parâmetro desconhecido é ignorado pelo FastAPI: não filtra nem vaza uso
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
