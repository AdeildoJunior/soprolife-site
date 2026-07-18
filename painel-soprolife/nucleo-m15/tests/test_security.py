"""Autenticação, papéis e trilha de auditoria."""

from app.audit import sanitize_details
from app.security import (
    ROLE_IMPLIES,
    hash_password,
    issue_token,
    parse_token,
    verify_password,
)


def test_hash_e_verificacao_de_senha():
    stored = hash_password("minha-senha-segura")
    assert verify_password("minha-senha-segura", stored)
    assert not verify_password("outra-senha", stored)
    assert "minha-senha-segura" not in stored


def test_token_valido_e_invalido():
    token = issue_token("user-123")
    assert parse_token(token) == "user-123"
    assert parse_token(token + "x") is None
    assert parse_token("lixo") is None


def test_token_expirado():
    token = issue_token("user-123", ttl_minutes=-1)
    assert parse_token(token) is None


def test_hierarquia_de_papeis():
    assert "leitura" in ROLE_IMPLIES["admin"]
    assert "operacional" in ROLE_IMPLIES["gestor"]
    assert "admin" not in ROLE_IMPLIES["gestor"]
    assert ROLE_IMPLIES["leitura"] == {"leitura"}


def test_sanitizacao_remove_pii_da_auditoria():
    details = {
        "public_code": "PES-000001",
        "nome": "Fulano Real",
        "telefone": "21999990000",
        "cpf": "111.222.333-44",
        "campos": ["status"],
    }
    clean = sanitize_details(details)
    assert clean == {"public_code": "PES-000001", "campos": ["status"]}


def test_api_sem_token_retorna_401(client):
    resp = client.get("/api/v1/pessoas")
    assert resp.status_code == 401
    assert resp.json()["erro"]["codigo"] == "http_401"


def test_api_papel_insuficiente_retorna_403(client, auth):
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Teste Papel"},
        headers=auth("leitura"),
    )
    assert resp.status_code == 403


def test_health_sem_token(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["banco"] == "ok"
    assert body["agora_utc"].endswith("+00:00")
    assert "-03:00" in body["agora_local"] or "-02:00" in body["agora_local"]


def test_login_e_uso_do_token(client, users):
    resp = client.post(
        "/api/v1/auth/token",
        json={"email": "admin@teste.local", "password": "senha-teste-123"},
    )
    assert resp.status_code == 200
    token = resp.json()["token"]
    ok = client.get("/api/v1/pessoas", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200


def test_cli_inativacao_revoga_token_e_permite_reativar(
    client, users, engine, monkeypatch
):
    from app import cli
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(cli, "_session", sessionmaker(bind=engine))

    token = client.post(
        "/api/v1/auth/token",
        json={"email": "admin@teste.local", "password": "senha-teste-123"},
    ).json()["token"]
    assert cli.main(["desativar-usuario", "--email", "admin@teste.local"]) == 0
    denied = client.get(
        "/api/v1/pessoas", headers={"Authorization": f"Bearer {token}"}
    )
    assert denied.status_code == 401
    assert cli.main(["ativar-usuario", "--email", "admin@teste.local"]) == 0
    allowed = client.get(
        "/api/v1/pessoas", headers={"Authorization": f"Bearer {token}"}
    )
    assert allowed.status_code == 200


def test_login_senha_errada(client, users):
    resp = client.post(
        "/api/v1/auth/token",
        json={"email": "admin@teste.local", "password": "errada-errada"},
    )
    assert resp.status_code == 401


def test_request_id_no_header(client):
    resp = client.get("/api/v1/health")
    assert resp.headers.get("X-Request-ID")


def test_request_id_invalido_e_substituido(client):
    """request_id fora do formato/tamanho vira id gerado (nunca 500)."""
    longo = "x" * 500
    resp = client.get("/api/v1/health", headers={"X-Request-ID": longo})
    assert resp.status_code == 200
    rid = resp.headers["X-Request-ID"]
    assert rid != longo and len(rid) <= 64
    resp2 = client.get("/api/v1/health", headers={"X-Request-ID": "com espaco e *"})
    assert resp2.headers["X-Request-ID"] != "com espaco e *"
    # id válido é preservado
    resp3 = client.get("/api/v1/health", headers={"X-Request-ID": "meu-id-123"})
    assert resp3.headers["X-Request-ID"] == "meu-id-123"


def test_login_usuario_inexistente_mesma_resposta(client, users):
    """Usuário inexistente e senha errada retornam exatamente o mesmo erro."""
    inexistente = client.post(
        "/api/v1/auth/token",
        json={"email": "naoexiste@teste.local", "password": "qualquer-coisa"},
    )
    senha_errada = client.post(
        "/api/v1/auth/token",
        json={"email": "admin@teste.local", "password": "senha-incorreta"},
    )
    assert inexistente.status_code == senha_errada.status_code == 401
    assert inexistente.json()["erro"]["mensagem"] == senha_errada.json()["erro"]["mensagem"]


def test_login_rate_limit(client, users):
    for _ in range(5):
        client.post(
            "/api/v1/auth/token",
            json={"email": "alvo-ratelimit@teste.local", "password": "errada"},
        )
    resp = client.post(
        "/api/v1/auth/token",
        json={"email": "alvo-ratelimit@teste.local", "password": "errada"},
    )
    assert resp.status_code == 429


def test_auditoria_append_only_na_aplicacao(db):
    """Guarda de sessão bloqueia UPDATE/DELETE em audit_logs."""
    import pytest as _pytest
    from sqlalchemy import select

    from app.audit import AuditAppendOnlyError, audit
    from app.models import AuditLog

    audit(db, "teste.acao")
    db.commit()
    registro = db.execute(select(AuditLog)).scalars().first()
    registro.acao = "alterado"
    with _pytest.raises(AuditAppendOnlyError):
        db.commit()
    db.rollback()
    registro = db.execute(select(AuditLog)).scalars().first()
    db.delete(registro)
    with _pytest.raises(AuditAppendOnlyError):
        db.commit()
    db.rollback()


def test_sanitizacao_recursiva_por_allowlist():
    from app.audit import sanitize_details

    detalhes = {
        "campos": ["status"],
        "followup": {"motivo": "criado", "nome": "Fulano Real"},
        "nome": "Fulano Real",
        "profundo": {"telefone": "21999990000"},
    }
    limpo = sanitize_details(detalhes)
    assert limpo == {"campos": ["status"], "followup": {"motivo": "criado"}}


def test_auditoria_registra_acao_e_exige_gestor(client, auth):
    client.post(
        "/api/v1/pessoas",
        json={"nome_completo": "Pessoa Auditada"},
        headers=auth("operacional"),
    )
    denied = client.get("/api/v1/auditoria", headers=auth("operacional"))
    assert denied.status_code == 403
    resp = client.get("/api/v1/auditoria", headers=auth("gestor"))
    assert resp.status_code == 200
    acoes = [item["acao"] for item in resp.json()["itens"]]
    assert "pessoa.criada" in acoes
    # nenhum detalhe de auditoria contém nome
    for item in resp.json()["itens"]:
        if item["detalhes"]:
            assert "nome" not in item["detalhes"]
            assert "telefone" not in item["detalhes"]
