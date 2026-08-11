"""M21 — sessão persistente segura, CSRF e revogação.

Prova, contra a API real (SQLite isolado por teste, usuários sintéticos):
  - login emite cookie HttpOnly/Secure/SameSite=Strict com Path restrito;
  - recarregar a página restaura o MESMO usuário (GET /auth/sessao);
  - navegar entre áreas preserva a sessão;
  - "manter conectado" é o que define sobreviver ao fechamento do navegador;
  - logout revoga no servidor e limpa o cookie;
  - sessão expirada, usuário desativado e senha redefinida falham FECHADO;
  - CSRF rejeita escrita autenticada por cookie sem o cabeçalho;
  - RBAC continua sendo do servidor;
  - dois usuários não compartilham identidade no mesmo navegador;
  - nenhum token bearer é exigido pelo caminho de cookie.

Nada aqui toca produção: cada teste roda em banco próprio, com e-mails
@teste.local e senhas sintéticas.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import SESSION_PERSISTENT_MAX_DAYS, Settings, get_settings
from app.models import AuthSession
from app.security import (
    REVOKE_DESATIVADO,
    REVOKE_LOGOUT,
    REVOKE_SENHA,
    parse_session_cookie,
)

SENHA = "senha-teste-123"
COOKIE = "soprolife_m15_sessao"

# Em produção o cookie tem Path restrito ao prefixo público do proxy
# (/painel-soprolife/api/m15), e é o navegador que respeita esse escopo. O
# TestClient fala DIRETO com a API em /api/v1, então aqui o Path é "/" — sem
# isso o cliente de teste simplesmente não devolveria o cookie. O valor real de
# produção é fixado no teste test_path_padrao_e_restrito_ao_prefixo_do_painel.
@pytest.fixture(autouse=True)
def _cookie_path_para_teste_direto(monkeypatch):
    monkeypatch.setenv("M15_SESSION_COOKIE_PATH", "/")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _login(client, email=None, *, senha=SENHA, manter=False):
    return client.post(
        "/api/v1/auth/token",
        json={
            "email": email or "admin@teste.local",
            "password": senha,
            "manter_conectado": manter,
        },
    )


def _set_cookies(resp) -> list[str]:
    """TODOS os Set-Cookie da resposta, na ordem em que foram emitidos.

    `headers.get("set-cookie")` NÃO serve: com mais de um cabeçalho o httpx
    devolve os dois concatenados por vírgula, e o par vira uma string só.
    """
    if hasattr(resp.headers, "get_list"):
        return list(resp.headers.get_list("set-cookie"))
    if hasattr(resp.headers, "raw_items"):
        return [
            valor.decode()
            for nome, valor in resp.headers.raw_items()
            if nome.decode().lower() == "set-cookie"
        ]
    unico = resp.headers.get("set-cookie", "")
    return [unico] if unico else []


def _e_remocao(bruto: str) -> bool:
    """Set-Cookie de REMOÇÃO: valor vazio e/ou Max-Age=0."""
    return f'{COOKIE}=""' in bruto or "Max-Age=0" in bruto


def _set_cookie_header(resp) -> str:
    """O Set-Cookie que EMITE a sessão.

    M25.23 — o login passou a mandar dois Set-Cookie: primeiro a remoção do
    escopo antigo (`/painel-soprolife/api/m15`), depois o cookie novo. Pegar
    cegamente o primeiro faria os testes de atributo lerem o de remoção, que
    por definição carrega Max-Age=0 e expires.
    """
    for bruto in _set_cookies(resp):
        if not _e_remocao(bruto):
            return bruto
    return ""


def _logout(client, csrf):
    return client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )


# ─────────────────────────── cookie e atributos ─────────────────────────────


def test_login_emite_cookie_de_sessao_com_flags_corretas(client):
    resp = _login(client)
    assert resp.status_code == 200, resp.text
    bruto = _set_cookie_header(resp)
    assert COOKIE in bruto
    assert "HttpOnly" in bruto
    assert "SameSite=strict" in bruto.replace("SameSite=Strict", "SameSite=strict")
    assert "Path=" in bruto
    # o cookie é assinado: id.segredo.assinatura
    assert client.cookies.get(COOKIE)
    assert parse_session_cookie(client.cookies.get(COOKIE)) is not None


def test_path_padrao_e_restrito_ao_prefixo_do_painel(monkeypatch):
    """O default de produção NÃO é "/": o cookie fica preso ao painel.

    M25.23 — o escopo passou de ".../api/m15" para "/painel-soprolife". O
    ponto do teste continua o mesmo e é o que importa: o cookie NUNCA vale
    para a origem inteira, então não vaza para o site institucional. Ele
    precisou alargar para dentro do painel porque a camada estática também
    precisa reconhecer a sessão — sem isso ela servia o painel a qualquer um.
    """
    monkeypatch.delenv("M15_SESSION_COOKIE_PATH", raising=False)
    path = Settings().session_cookie_path
    assert path == "/painel-soprolife"
    assert path != "/"
    assert not path.startswith("/painel-soprolife/api")


def test_path_de_cookie_invalido_e_recusado():
    for ruim in ("painel", "/com espaco", "/com;ponto-e-virgula"):
        with pytest.raises(ValueError):
            Settings(session_cookie_path=ruim)


def test_cookie_e_secure_em_producao(monkeypatch):
    """Em prod o Secure é forçado por configuração, sem variável que desligue."""
    get_settings.cache_clear()
    try:
        s = Settings(
            env="prod",
            auth_secret="x9F!k2Lm#7Qp$4Rt&8Wz@1Bc^5Vn*3Hj",
            cors_origins=["https://exemplo.invalid"],
            session_cookie_secure=False,  # tentativa explícita de afrouxar
        )
        assert s.session_cookie_secure is True
    finally:
        get_settings.cache_clear()


def test_login_nao_devolve_a_senha_em_lugar_algum(client):
    resp = _login(client)
    assert SENHA not in resp.text
    assert SENHA not in _set_cookie_header(resp)


def test_resposta_de_login_traz_csrf_e_dados_da_sessao(client):
    body = _login(client, manter=True).json()
    assert body["csrf"]
    assert body["sessao"]["persistente"] is True
    assert body["sessao"]["duracao_segundos"] == SESSION_PERSISTENT_MAX_DAYS * 86400


# ──────────────────── restauração: o F5 não desloga mais ────────────────────


def test_recarregar_a_pagina_restaura_o_mesmo_usuario(client, users):
    _login(client)
    # GET /auth/sessao é exatamente o que o frontend chama ao carregar a página
    resp = client.get("/api/v1/auth/sessao")
    assert resp.status_code == 200, resp.text
    assert resp.json()["usuario"]["id"] == users["admin"].id
    assert resp.json()["csrf"]


def test_navegar_entre_areas_preserva_a_sessao(client):
    _login(client)
    # sem nenhum Authorization: só o cookie
    for rota in ("/api/v1/auth/me", "/api/v1/crm/kpis", "/api/v1/pessoas",
                 "/api/v1/parceiros"):
        resp = client.get(rota)
        assert resp.status_code == 200, f"{rota}: {resp.text}"


def test_sem_cookie_nao_ha_sessao_para_restaurar(client):
    assert client.get("/api/v1/auth/sessao").status_code == 401


def test_cookie_com_assinatura_invalida_e_recusado(client):
    _login(client)
    bom = client.cookies.get(COOKIE)
    client.cookies.set(COOKIE, bom[:-3] + "xyz")
    assert client.get("/api/v1/auth/sessao").status_code == 401


# ─────────────────────── persistência entre navegadores ─────────────────────


def test_sem_manter_conectado_o_cookie_morre_com_o_navegador(client):
    resp = _login(client, manter=False)
    bruto = _set_cookie_header(resp)
    # cookie de sessão do navegador: sem Max-Age e sem Expires
    assert "Max-Age" not in bruto and "expires" not in bruto.lower()
    assert resp.json()["sessao"]["persistente"] is False


def test_login_apaga_o_cookie_do_escopo_antigo(client):
    """M25.23 — o login limpa o resíduo do path anterior.

    Sem isto, quem já estava logado quando o escopo mudou ficaria com dois
    cookies de mesmo nome. O navegador manda o de path mais específico
    primeiro, então o antigo sombrearia a sessão recém-criada e o painel
    pareceria recusar um login que acabou de dar certo.
    """
    resp = _login(client, manter=False)
    brutos = _set_cookies(resp)
    remocoes = [b for b in brutos if _e_remocao(b)]
    emissoes = [b for b in brutos if not _e_remocao(b)]

    assert len(emissoes) == 1, "deve emitir exatamente um cookie de sessão"
    assert len(remocoes) == 1, "deve apagar exatamente o escopo legado"
    assert "Path=/painel-soprolife/api/m15" in remocoes[0]
    assert "Max-Age=0" in remocoes[0]
    # A remoção vem ANTES da emissão: a ordem importa no navegador.
    assert brutos.index(remocoes[0]) < brutos.index(emissoes[0])
    # O escopo do cookie EMITIDO é o configurado (aqui "/", por causa da
    # fixture do TestClient); o valor real de produção é fixado em
    # test_path_padrao_e_restrito_ao_prefixo_do_painel.
    assert "Path=" in emissoes[0]
    assert "Path=/painel-soprolife/api/m15" not in emissoes[0]


def test_com_manter_conectado_o_cookie_persiste_com_teto_de_7_dias(client, db):
    resp = _login(client, manter=True)
    bruto = _set_cookie_header(resp)
    assert f"Max-Age={SESSION_PERSISTENT_MAX_DAYS * 86400}" in bruto
    sessao = db.execute(select(AuthSession)).scalars().one()
    assert sessao.persistente is True
    duracao = sessao.expires_at.replace(tzinfo=timezone.utc) - sessao.created_at.replace(
        tzinfo=timezone.utc
    )
    assert duracao <= timedelta(days=SESSION_PERSISTENT_MAX_DAYS)


@pytest.mark.parametrize("dias", [0, 8, 30, 365])
def test_configuracao_nao_pode_estourar_o_teto_persistente(dias):
    with pytest.raises(ValueError):
        Settings(session_persistent_days=dias)


def test_duracao_da_sessao_e_ajustavel_dentro_da_faixa():
    assert Settings(session_ttl_minutes=60).session_ttl_minutes == 60
    assert Settings(session_persistent_days=3).session_persistent_days == 3
    with pytest.raises(ValueError):
        Settings(session_ttl_minutes=1)


# ─────────────────────────────── logout ─────────────────────────────────────


def test_logout_revoga_no_servidor_e_limpa_o_cookie(client, db):
    csrf = _login(client).json()["csrf"]
    resp = _logout(client, csrf)
    assert resp.status_code == 200
    assert resp.json()["sessao_revogada"] is True
    sessao = db.execute(select(AuthSession)).scalars().one()
    assert sessao.revoked_at is not None
    assert sessao.revoked_motivo == REVOKE_LOGOUT
    # o cookie foi apagado no navegador e a sessão não volta
    assert client.get("/api/v1/auth/sessao").status_code == 401


def test_cookie_roubado_nao_serve_depois_do_logout(client):
    csrf = _login(client).json()["csrf"]
    roubado = client.cookies.get(COOKIE)
    _logout(client, csrf)
    client.cookies.set(COOKIE, roubado)
    # revogação é server-side: reapresentar o mesmo valor não ressuscita nada
    assert client.get("/api/v1/auth/sessao").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_sem_sessao_e_rejeitado(client):
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 401


def test_logout_por_cookie_sem_csrf_e_rejeitado_sem_revogar(client):
    _login(client)
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 403
    assert client.get("/api/v1/auth/sessao").status_code == 200


def test_dois_usuarios_nao_compartilham_identidade_no_mesmo_navegador(client, users):
    csrf = _login(client, "admin@teste.local").json()["csrf"]
    assert client.get("/api/v1/auth/me").json()["id"] == users["admin"].id
    _logout(client, csrf)
    _login(client, "leitura@teste.local")
    me = client.get("/api/v1/auth/me").json()
    assert me["id"] == users["leitura"].id
    assert me["papeis"] == ["leitura"]


# ───────────────────────── expiração e fail-closed ──────────────────────────


def test_sessao_expirada_falha_fechado(client, db):
    _login(client)
    sessao = db.execute(select(AuthSession)).scalars().one()
    sessao.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/v1/auth/sessao").status_code == 401
    assert client.get("/api/v1/pessoas").status_code == 401


def test_usuario_desativado_nao_restaura_sessao(client, db, users, auth):
    _login(client, "leitura@teste.local")
    assert client.get("/api/v1/auth/sessao").status_code == 200
    resp = client.patch(
        f"/api/v1/admin/usuarios/{users['leitura'].id}",
        json={"ativo": False},
        headers=auth("admin"),   # bearer: caminho administrativo, sem CSRF
    )
    assert resp.status_code == 200, resp.text
    assert client.get("/api/v1/auth/sessao").status_code == 401
    sessao = db.execute(
        select(AuthSession).where(AuthSession.user_id == users["leitura"].id)
    ).scalars().one()
    assert sessao.revoked_motivo == REVOKE_DESATIVADO


def test_redefinir_senha_invalida_sessoes_anteriores(client, db, users, auth):
    _login(client, "gestor@teste.local")
    assert client.get("/api/v1/auth/sessao").status_code == 200
    resp = client.post(
        f"/api/v1/admin/usuarios/{users['gestor'].id}/redefinir-senha",
        json={"senha": "outra-senha-bem-longa"},
        headers=auth("admin"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sessoes_revogadas"] == 1
    assert client.get("/api/v1/auth/sessao").status_code == 401
    sessao = db.execute(
        select(AuthSession).where(AuthSession.user_id == users["gestor"].id)
    ).scalars().one()
    assert sessao.revoked_motivo == REVOKE_SENHA


def test_sessao_nao_sobrevive_a_troca_de_senha_mesmo_sem_revogacao_explicita(
    client, db, users
):
    """Defesa em profundidade: o fingerprint da senha entra na sessão."""
    _login(client, "oper@teste.local")
    sessao = db.execute(select(AuthSession)).scalars().one()
    sessao.password_fingerprint = "0" * 12  # simula senha trocada por outra via
    db.commit()
    assert client.get("/api/v1/auth/sessao").status_code == 401


# ──────────────────────────────── CSRF ──────────────────────────────────────


def test_escrita_por_cookie_sem_csrf_e_rejeitada(client):
    _login(client)
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Teste CSRF", "contatos": []},
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.text


def test_escrita_por_cookie_com_csrf_valido_e_aceita(client):
    csrf = _login(client).json()["csrf"]
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Teste CSRF OK", "contatos": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text


def test_csrf_de_outra_sessao_nao_serve(client):
    alheio = _login(client, "gestor@teste.local").json()["csrf"]
    _logout(client, alheio)
    _login(client, "admin@teste.local")
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Teste CSRF Cruzado", "contatos": []},
        headers={"X-CSRF-Token": alheio},
    )
    assert resp.status_code == 403


def test_leitura_por_cookie_nao_exige_csrf(client):
    _login(client)
    assert client.get("/api/v1/pessoas").status_code == 200


def test_restaurar_sessao_rotaciona_o_csrf(client):
    primeiro = _login(client).json()["csrf"]
    segundo = client.get("/api/v1/auth/sessao").json()["csrf"]
    assert segundo and segundo != primeiro
    # o antigo deixa de valer imediatamente
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Teste CSRF Antigo", "contatos": []},
        headers={"X-CSRF-Token": primeiro},
    )
    assert resp.status_code == 403


def test_bearer_continua_isento_de_csrf(client, auth):
    """Integrações e CLI não mudam: o navegador não envia bearer sozinho."""
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Teste Bearer", "contatos": []},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text


# ──────────────────────────────── RBAC ──────────────────────────────────────


def test_rbac_continua_no_servidor_pelo_caminho_de_cookie(client):
    csrf = _login(client, "leitura@teste.local").json()["csrf"]
    # leitura não cria pessoa, mesmo com CSRF válido
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Teste RBAC", "contatos": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 403
    # e não acessa administração de usuários
    assert client.get("/api/v1/admin/usuarios").status_code == 403


def test_bearer_tem_precedencia_e_nao_e_afetado_pelo_cookie(client, auth, users):
    _login(client, "leitura@teste.local")
    me = client.get("/api/v1/auth/me", headers=auth("admin")).json()
    assert me["id"] == users["admin"].id


# ─────────────────────── Marketing: atualização manual segura ───────────────


def _marketing_queue(monkeypatch, tmp_path):
    target = tmp_path / "marketing-refresh-request.json"
    monkeypatch.setenv("M15_MARKETING_REFRESH_QUEUE", str(target))
    get_settings.cache_clear()
    return target


def test_refresh_marketing_exige_autenticacao(client, monkeypatch, tmp_path):
    target = _marketing_queue(monkeypatch, tmp_path)
    resp = client.post("/api/v1/marketing/refresh")
    assert resp.status_code == 401
    assert not target.exists()


def test_refresh_marketing_preserva_rbac(client, monkeypatch, tmp_path):
    target = _marketing_queue(monkeypatch, tmp_path)
    csrf = _login(client, "leitura@teste.local").json()["csrf"]
    resp = client.post(
        "/api/v1/marketing/refresh",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 403
    assert not target.exists()


def test_refresh_marketing_exige_csrf(client, monkeypatch, tmp_path):
    target = _marketing_queue(monkeypatch, tmp_path)
    _login(client, "oper@teste.local")
    resp = client.post("/api/v1/marketing/refresh")
    assert resp.status_code == 403
    assert not target.exists()


def test_refresh_marketing_enfileira_metadado_minimo(
    client, monkeypatch, tmp_path
):
    target = _marketing_queue(monkeypatch, tmp_path)
    csrf = _login(client, "oper@teste.local").json()["csrf"]
    resp = client.post(
        "/api/v1/marketing/refresh",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["queued"] is True
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert set(saved) == {"requestId", "requestedAt", "origin", "state"}
    assert saved["origin"] == "painel-autenticado"
    assert saved["state"] == "pending"
    assert oct(target.stat().st_mode & 0o777) == "0o600"

    # Clique repetido é limitado e não cria uma fila crescente.
    repeated = client.post(
        "/api/v1/marketing/refresh",
        headers={"X-CSRF-Token": csrf},
    )
    assert repeated.status_code == 200
    assert repeated.json()["queued"] is False


def test_refresh_marketing_status_exige_login_e_detecta_consumo(
    client, monkeypatch, tmp_path
):
    target = _marketing_queue(monkeypatch, tmp_path)
    assert client.get("/api/v1/marketing/refresh-status").status_code == 401
    csrf = _login(client, "oper@teste.local").json()["csrf"]
    client.post(
        "/api/v1/marketing/refresh",
        headers={"X-CSRF-Token": csrf},
    )
    assert client.get("/api/v1/marketing/refresh-status").json()["pending"] is True
    request = json.loads(target.read_text(encoding="utf-8"))
    target.write_text(json.dumps({
        **request,
        "state": "completed",
        "completedAt": "2026-08-01T12:00:00+00:00",
        "success": False,
        "degraded": True,
        "snapshotGeneratedAt": "2026-08-01T12:00:00+00:00",
        "errorMessageSafe": "Falha ao consultar o Google. Snapshot preservado.",
    }), encoding="utf-8")
    status = client.get("/api/v1/marketing/refresh-status").json()
    assert status["pending"] is False
    assert status["state"] == "completed"
    assert status["success"] is False
    assert status["degraded"] is True
    assert "Snapshot preservado" in status["errorMessageSafe"]


# ───────────────────── rate limiting e anti-enumeração ──────────────────────


def test_rate_limit_de_login_preservado(client):
    for _ in range(5):
        assert _login(client, senha="senha-errada").status_code == 401
    assert _login(client, senha="senha-errada").status_code == 429
    # e o bloqueio não emite cookie
    assert not client.cookies.get(COOKIE)


def test_login_falho_nao_cria_sessao(client, db):
    _login(client, senha="senha-errada")
    assert db.execute(select(AuthSession)).scalars().all() == []


def test_credencial_invalida_nao_revela_existencia_do_usuario(client):
    inexistente = _login(client, "ninguem@teste.local", senha="qualquer")
    existente = _login(client, "admin@teste.local", senha="senha-errada")
    assert inexistente.status_code == existente.status_code == 401
    assert inexistente.json()["erro"]["mensagem"] == existente.json()["erro"]["mensagem"]
