"""Administração de usuários (M15.3A): RBAC, anti-lockout, revogação de tokens.

Todos os dados são sintéticos (e-mails .local, senhas de teste).
"""

BASE = "/api/v1/admin/usuarios"


def _login(client, email, senha):
    return client.post("/api/v1/auth/token", json={"email": email, "password": senha})


def _create(client, auth, email, papel="leitura", senha="senha-nova-123", nome="Usuário Teste"):
    return client.post(
        BASE,
        json={"email": email, "nome": nome, "papel": papel, "senha": senha},
        headers=auth("admin"),
    )


# ------------------------------------------------------------------- RBAC

def test_somente_admin_acessa_administracao(client, auth):
    for papel in ("gestor", "operacional", "leitura"):
        assert client.get(BASE, headers=auth(papel)).status_code == 403, papel
        resp = client.post(
            BASE,
            json={"email": "x@teste.local", "nome": "X", "papel": "leitura",
                  "senha": "senha-nova-123"},
            headers=auth(papel),
        )
        assert resp.status_code == 403, papel
    assert client.get(BASE, headers=auth("admin")).status_code == 200


def test_gestor_nao_herda_administracao(client, auth, users):
    """Regra explícita: gestor administra operação, NUNCA usuários."""
    alvo = users["leitura"].id
    resp = client.patch(f"{BASE}/{alvo}", json={"papel": "gestor"}, headers=auth("gestor"))
    assert resp.status_code == 403


# ------------------------------------------------------------------ criação

def test_criar_usuario_e_login(client, auth):
    resp = _create(client, auth, "novo@teste.local", papel="operacional")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["papeis"] == ["operacional"]
    assert body["ativo"] is True
    assert "senha" not in body and "password_hash" not in body
    login = _login(client, "novo@teste.local", "senha-nova-123")
    assert login.status_code == 200
    token = login.json()["token"]
    ok = client.get("/api/v1/pessoas", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200


def test_criar_usuario_email_duplicado(client, auth, users):
    resp = _create(client, auth, "admin@teste.local")
    assert resp.status_code == 409


def test_criar_usuario_senha_curta_e_email_invalido(client, auth):
    curto = _create(client, auth, "curto@teste.local", senha="curta")
    assert curto.status_code == 422
    invalido = _create(client, auth, "sem-arroba")
    assert invalido.status_code == 422


def test_payload_extra_rejeitado(client, auth):
    resp = client.post(
        BASE,
        json={"email": "extra@teste.local", "nome": "X", "papel": "leitura",
              "senha": "senha-nova-123", "superuser": True},
        headers=auth("admin"),
    )
    assert resp.status_code == 422


# ------------------------------------------------------------ papel e estado

def test_alterar_papel(client, auth, users):
    alvo = users["leitura"].id
    resp = client.patch(f"{BASE}/{alvo}", json={"papel": "operacional"}, headers=auth("admin"))
    assert resp.status_code == 200
    assert resp.json()["papeis"] == ["operacional"]


def test_papel_novo_vale_para_proximo_login(client, auth, users):
    alvo = users["leitura"].id
    client.patch(f"{BASE}/{alvo}", json={"papel": "operacional"}, headers=auth("admin"))
    token = _login(client, "leitura@teste.local", "senha-teste-123").json()["token"]
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Papel Novo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201


def test_desativar_bloqueia_login_e_tokens_em_uso(client, auth, users):
    alvo = users["operacional"].id
    token_antigo = _login(client, "oper@teste.local", "senha-teste-123").json()["token"]
    resp = client.patch(f"{BASE}/{alvo}", json={"ativo": False}, headers=auth("admin"))
    assert resp.status_code == 200 and resp.json()["ativo"] is False
    # token emitido ANTES da desativação para de funcionar na hora
    negado = client.get(
        "/api/v1/pessoas", headers={"Authorization": f"Bearer {token_antigo}"}
    )
    assert negado.status_code == 401
    # e o login passa a ser recusado
    assert _login(client, "oper@teste.local", "senha-teste-123").status_code == 401
    # reativação restaura o acesso por novo login
    resp = client.patch(f"{BASE}/{alvo}", json={"ativo": True}, headers=auth("admin"))
    assert resp.status_code == 200 and resp.json()["ativo"] is True
    assert _login(client, "oper@teste.local", "senha-teste-123").status_code == 200


def test_nao_desativa_a_propria_conta(client, auth, users):
    resp = client.patch(
        f"{BASE}/{users['admin'].id}", json={"ativo": False}, headers=auth("admin")
    )
    assert resp.status_code == 409


def test_nao_altera_o_proprio_papel(client, auth, users):
    resp = client.patch(
        f"{BASE}/{users['admin'].id}", json={"papel": "leitura"}, headers=auth("admin")
    )
    assert resp.status_code == 409


def test_protege_ultimo_admin_ativo(client, auth, users):
    """Com um segundo admin, o primeiro pode ser rebaixado; sem outro, nunca."""
    resp = _create(client, auth, "admin2@teste.local", papel="admin")
    assert resp.status_code == 201
    admin2 = resp.json()["id"]
    token2 = _login(client, "admin2@teste.local", "senha-nova-123").json()["token"]
    h2 = {"Authorization": f"Bearer {token2}"}
    # admin2 rebaixa o admin original (ainda existe admin2 ativo) — permitido
    ok = client.patch(f"{BASE}/{users['admin'].id}", json={"papel": "gestor"}, headers=h2)
    assert ok.status_code == 200
    # agora admin2 é o último admin ativo: rebaixar/desativar é bloqueado
    bloqueado = client.patch(f"{BASE}/{admin2}", json={"papel": "gestor"}, headers=h2)
    assert bloqueado.status_code == 409


# ------------------------------------------------------------------- senha

def test_redefinir_senha_revoga_tokens_antigos(client, auth, users):
    alvo = users["gestor"].id
    token_antigo = _login(client, "gestor@teste.local", "senha-teste-123").json()["token"]
    resp = client.post(
        f"{BASE}/{alvo}/redefinir-senha",
        json={"senha": "senha-trocada-456"},
        headers=auth("admin"),
    )
    assert resp.status_code == 200
    assert resp.json()["tokens_revogados"] is True
    # token antigo morre imediatamente (fingerprint da credencial mudou)
    negado = client.get(
        "/api/v1/auditoria", headers={"Authorization": f"Bearer {token_antigo}"}
    )
    assert negado.status_code == 401
    # senha antiga não loga mais; a nova sim
    assert _login(client, "gestor@teste.local", "senha-teste-123").status_code == 401
    assert _login(client, "gestor@teste.local", "senha-trocada-456").status_code == 200


# --------------------------------------------------------------- auditoria

def test_auditoria_sem_senha_nem_email(client, auth, users):
    _create(client, auth, "auditado@teste.local", senha="senha-super-secreta-789")
    client.post(
        f"{BASE}/{users['leitura'].id}/redefinir-senha",
        json={"senha": "outra-senha-secreta-000"},
        headers=auth("admin"),
    )
    trilha = client.get("/api/v1/auditoria?tamanho=100", headers=auth("admin")).json()
    acoes = [i["acao"] for i in trilha["itens"]]
    assert "usuario.criado" in acoes
    assert "usuario.senha_redefinida" in acoes
    dump = str(trilha)
    assert "senha-super-secreta-789" not in dump
    assert "outra-senha-secreta-000" not in dump
    assert "auditado@teste.local" not in dump


def test_listagem_nao_expoe_hash(client, auth):
    corpo = client.get(BASE, headers=auth("admin")).json()
    assert corpo["total"] >= 4
    for item in corpo["itens"]:
        assert "password_hash" not in item and "senha" not in item
