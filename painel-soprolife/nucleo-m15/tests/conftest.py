"""Fixtures de teste: banco SQLite isolado por teste + API com usuários."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import Base, build_engine, get_db
from app.main import create_app
from app.models import User
from app.security import (
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
    login_rate_limiter,
)

# telefone sintético NÃO DISCÁVEL (prefixo local 0000 não atribuído no Brasil)
SYNTH_PHONE = "(21) 0000-9001"
SYNTH_PHONE_NORM = "552100009001"


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    login_rate_limiter._failures.clear()
    yield
    login_rate_limiter._failures.clear()


@pytest.fixture()
def engine(tmp_path):
    eng = build_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    yield session
    session.close()


def _make_user(session, email: str, role: str) -> User:
    ensure_roles_exist(session)
    user = User(email=email, nome=f"Teste {role}", password_hash=hash_password("senha-teste-123"))
    user.roles.append(get_role(session, role))
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def users(db):
    return {
        "admin": _make_user(db, "admin@teste.local", "admin"),
        "gestor": _make_user(db, "gestor@teste.local", "gestor"),
        "operacional": _make_user(db, "oper@teste.local", "operacional"),
        "leitura": _make_user(db, "leitura@teste.local", "leitura"),
    }


@pytest.fixture()
def client(engine, users):
    app = create_app()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def tokens(users):
    return {
        name: issue_token(user.id, user.password_hash) for name, user in users.items()
    }


@pytest.fixture()
def auth(tokens):
    def headers(role: str = "admin") -> dict:
        return {"Authorization": f"Bearer {tokens[role]}"}

    return headers


@pytest.fixture()
def person(client, auth):
    resp = client.post(
        "/api/v1/pessoas",
        json={
            "nome_completo": "Pessoa Teste 001",
            "contatos": [{"tipo": "whatsapp", "valor": SYNTH_PHONE, "principal": True}],
            "consentimento_whatsapp": "concedido",
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
