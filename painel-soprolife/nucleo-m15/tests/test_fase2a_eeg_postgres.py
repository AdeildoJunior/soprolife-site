"""Prova com grants reais em PostgreSQL descartável; nunca banco operacional."""
import os
from uuid import uuid4
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.db import get_db
from .test_fase2a_eeg_portal import ready, link, login, download, portal_config, storage

URL = os.environ.get("M15_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL descartável não configurado")


@pytest.fixture()
def engine():
    url = make_url(URL)
    assert url.host in {"127.0.0.1", "localhost"} and "test" in url.database
    eng = create_engine(url)
    yield eng
    eng.dispose()


def test_portal_eeg_com_papel_restrito_real(client, auth, engine):
    from app.portal.main import create_portal_app
    role = "fase2a_portal_" + uuid4().hex[:8]
    with engine.begin() as conn:
        conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'synthetic-restricted-test'"))
        conn.execute(text(f"GRANT CONNECT ON DATABASE {engine.url.database} TO {role}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        conn.execute(text(f"GRANT SELECT (id, nome_completo, data_nascimento, arquivado) ON people TO {role}"))
        conn.execute(text(f"GRANT INSERT (ts_utc, request_id, user_id, acao, entidade, entidade_id, detalhes), SELECT (id) ON audit_logs TO {role}"))
        conn.execute(text(f"GRANT USAGE ON SEQUENCE audit_logs_id_seq TO {role}"))
        sql = (Path(__file__).parents[1] / "scripts/sql/m26-4-portal-db-role.sql").read_text()
        sql = sql.split("-- BEGIN FASE2A_EEG_PORTAL_GRANTS", 1)[1]
        sql = "\n".join(l for l in sql.splitlines() if not l.startswith("\\"))
        conn.exec_driver_sql(sql.replace("soprolife_portal", role))
    restricted = create_engine(engine.url.set(username=role, password="synthetic-restricted-test"))
    app = create_portal_app()
    factory = sessionmaker(bind=restricted, expire_on_commit=False)
    def session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = session
    try:
        exam = ready(client, auth)
        token = link(client, auth, exam)
        with TestClient(app) as portal:
            r = login(portal, token)
            assert r.status_code == 200, r.text
            pdf = download(portal)
            assert pdf.status_code == 200, pdf.text[:100]
            assert portal.get("/p/v1/documentos/exame-tecnico").status_code == 404
        for statement in ["SELECT * FROM users", "SELECT cpf FROM people",
                          "SELECT * FROM eeg_technical_files",
                          "UPDATE eeg_report_documents SET status='vigente'"]:
            with restricted.connect() as conn:
                with pytest.raises(Exception, match="permission denied"):
                    conn.execute(text(statement))
    finally:
        restricted.dispose()
