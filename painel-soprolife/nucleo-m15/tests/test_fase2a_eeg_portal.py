"""PDF final EEG: fronteira pública, segregação clínica e revogação reais."""
from datetime import timedelta
from urllib.parse import urlsplit, parse_qs
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db
from app.models import EegResultAccess, EegResultSession, PatientResultAccess, AuditLog
from app.services import patient_results as prs
from .test_fase2a_eeg_operacao import _pdf, _pessoa_sopro, _vincular, _exame, _status, storage, _preparar_laudador

API = "/api/v1/eeg"


@pytest.fixture(autouse=True)
def portal_config(storage, monkeypatch):
    for k, v in {
        "M15_PORTAL_ENABLED": "true",
        "M15_PORTAL_TOKEN_KEY": "eeg-token-synthetic-key-0123456789abcdef",
        "M15_PORTAL_SESSION_SECRET": "eeg-session-synthetic-key-fedcba9876543210",
        "M15_PORTAL_PUBLIC_BASE_URL": "https://site.test/resultados",
        "M15_PORTAL_EEG_PUBLIC_BASE_URL": "https://portal.test/p/v1/eeg",
        "M15_PORTAL_COOKIE_SECURE": "false",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    from app.portal.security import limitador
    limitador.limpar()
    yield
    limitador.limpar()
    get_settings.cache_clear()


@pytest.fixture()
def portal(engine):
    from app.portal.main import create_portal_app
    app = create_portal_app()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    def db_session():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db_session
    with TestClient(app) as client:
        yield client


def ready(client, auth, *, release=True, patient_name="Paciente Sintetico Portal EEG"):
    person = client.post(API + "/pessoas", json={
        "nome_completo": patient_name, "data_nascimento": "1991-04-11"
    }, headers=auth()).json()
    exam = _exame(client, auth, person["id"], agendado_para="2026-11-20T10:00:00Z")
    realized = client.patch(f"{API}/exames/{exam['id']}/status", json={
        "status": "realizado", "ocorrido_em": "2026-11-21T01:15:00Z"
    }, headers=auth())
    assert realized.status_code == 200
    sent = client.post(f"{API}/exames/{exam['id']}/envio-laudo", json={
        "destino_tipo": "medico", "destino_nome": "Laudador Sintetico", "metodo": "outro"
    }, headers=auth())
    assert sent.status_code == 201
    _preparar_laudador(client, auth, exam)
    received = client.post(f"{API}/exames/{exam['id']}/laudo",
        data={"assinatura_recebida":"true", "confirmacao_codigo":exam["public_code"]},
        files={"arquivo": ("laudo.pdf", _pdf(), "application/pdf")}, headers=auth())
    assert received.status_code == 201, received.text
    exam = received.json()
    if release:
        _status(client, auth, exam["id"], "laudo_disponivel")
    return exam


def link(client, auth, exam, renovar=False):
    res = client.post(f"{API}/laudos/{exam['laudo']['id']}/acesso-resultado",
                      params={"renovar": renovar}, headers=auth())
    assert res.status_code == 200, res.text
    return parse_qs(urlsplit(res.json()["url"]).fragment)["t"][0]


def download(portal):
    data = portal.get("/p/v1/documentos")
    if data.status_code != 200:
        return data
    return portal.get(data.json()["documentos"][0]["caminho"])


def login(portal, token, birth="1991-04-11"):
    return portal.post("/p/v1/acesso", json={"token": token, "nascimento": birth})


def test_pdf_final_com_identidade_nascimento_sem_tracado(client, auth, portal, db):
    exam = ready(client, auth)
    token = link(client, auth, exam)
    assert token.startswith("eeg_")
    assert login(portal, token, "1991-04-12").status_code == 401
    response = login(portal, token)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["exame"]["tipo"] == "EEG"
    assert data["exame"]["data"] == "2026-11-20"
    assert "T" not in data["exame"]["data"]
    assert portal.get("/p/v1/documentos/laudo-assinado").status_code == 404
    assert [d["chave"] for d in data["documentos"]] == ["laudo-final-eeg"]
    assert "storage" not in response.text and "referencia_externa" not in response.text
    pdf = download(portal)
    assert pdf.status_code == 200 and pdf.content == _pdf()
    assert "no-store" in pdf.headers["cache-control"]
    assert portal.get("/p/v1/documentos/exame-tecnico").status_code == 404
    assert db.execute(select(PatientResultAccess)).scalars().all() == []
    row = db.execute(select(EegResultAccess)).scalar_one()
    assert row.token_sha256 == prs.token_digest(token)
    assert row.download_count == 1
    assert token not in json.dumps([a.detalhes for a in db.execute(select(AuditLog)).scalars()])
    db.rollback()


def test_renovar_revogar_e_substituir_invalidam_sessao(client, auth, portal):
    exam = ready(client, auth)
    old = link(client, auth, exam)
    assert login(portal, old).status_code == 200
    new = link(client, auth, exam, renovar=True)
    assert new != old
    assert portal.get("/p/v1/documentos").status_code == 401
    assert login(portal, old).status_code == 401
    assert login(portal, new).status_code == 200
    replacement = client.post(f"{API}/exames/{exam['id']}/laudo",
        data={"assinatura_recebida":"true", "confirmacao_codigo":exam["public_code"]},
        files={"arquivo": ("novo.pdf", _pdf(2), "application/pdf")}, headers=auth())
    assert replacement.status_code == 201, replacement.text
    assert download(portal).status_code == 401
    assert login(portal, new).status_code != 200
    _status(client, auth, exam["id"], "laudo_disponivel")
    current = link(client, auth, replacement.json())
    assert login(portal, current).status_code == 200
    res = client.post(f"{API}/laudos/{replacement.json()['laudo']['id']}/acesso-resultado/revogar", headers=auth())
    assert res.status_code == 200
    assert download(portal).status_code == 401


def test_sem_publicacao_sem_token_sem_sessao_recusa(client, auth, portal):
    exam = ready(client, auth, release=False)
    res = client.post(f"{API}/laudos/{exam['laudo']['id']}/acesso-resultado", headers=auth())
    assert res.status_code == 409
    assert download(portal).status_code == 401
    assert login(portal, "eeg_inventado").status_code == 401


@pytest.mark.parametrize("role", ["operacional", "gestor", "leitura", "medico"])
def test_todas_rotas_eeg_exigem_admin_inclusive_novas(client, auth, role, users, db):
    from .conftest import _make_user
    from app.security import issue_token
    if role == "medico":
        user = _make_user(db, "medico-eeg-negado@teste.local", "medico")
        headers = {"Authorization": "Bearer " + issue_token(user.id, user.password_hash)}
    else:
        headers = auth(role)
    from app.main import create_app
    for route in create_app().routes:
        if "/eeg/" not in getattr(route, "path", ""):
            continue
        path = route.path.replace("{exam_id}", "inexistente").replace("{documento_id}", "inexistente")
        for method in route.methods:
            response = client.request(method, path, headers=headers, json={})
            assert response.status_code == 403, (method, path, response.text)


def test_pagina_eeg_publica_sem_dados_e_sem_afirmar_assinatura(portal):
    r = portal.get("/p/v1/eeg/")
    assert r.status_code == 200
    assert "BAIXAR LAUDO PDF FINAL" in r.text
    assert 'id="linkTecnico"' not in r.text
    assert "BAIXAR LAUDO ASSINADO" not in r.text
    assert "no-store" in r.headers["cache-control"]
    assert "script-src 'self'" in r.headers["content-security-policy"]


def test_expiracao_e_bloqueio_persistentes(client, auth, portal, db):
    exam = ready(client, auth)
    token = link(client, auth, exam)
    for _ in range(5):
        assert login(portal, token, "1991-04-12").status_code in (401, 429)
    assert login(portal, token).status_code == 429
    new = link(client, auth, exam, renovar=True)
    assert login(portal, new).status_code == 200
    access = db.execute(select(EegResultAccess)).scalar_one()
    access.expires_at = prs.agora() - timedelta(seconds=1)
    db.commit()
    assert download(portal).status_code == 401


def test_link_nao_entrega_pdf_de_outra_identidade(client, auth, portal, db):
    exam = ready(client, auth)
    token = link(client, auth, exam)
    assert login(portal, token).status_code == 200
    other = _pessoa_sopro(client, auth, "Outra Pessoa Sintetica Portal")
    access = db.execute(select(EegResultAccess)).scalar_one()
    access.person_id = other["id"]
    db.commit()
    assert download(portal).status_code == 401
    assert login(portal, token).status_code == 401


def test_resolvedor_sopro_nao_consulta_codigos_eeg(client, auth):
    exam = ready(client, auth)
    for code in (exam["public_code"], exam["laudo"]["public_code"]):
        r = client.get("/api/v1/crm/codigos/resolver", params={"codigo": code}, headers=auth("operacional"))
        assert r.status_code == 200 and r.json()["encontrado"] is False


def test_download_vinculado_ao_acesso_da_pagina_entre_abas(client, auth, portal):
    first = ready(client, auth)
    one = login(portal, link(client, auth, first)).json()["documentos"][0]["caminho"]
    second = ready(client, auth, patient_name="Outro Paciente Sintetico EEG")
    two = login(portal, link(client, auth, second)).json()["documentos"][0]["caminho"]
    assert one != two
    assert portal.get(one).status_code == 401
    assert portal.get(two).status_code == 200
