"""Seed sintético/institucional e endpoint de importação dry-run."""

import pathlib

from sqlalchemy import func, select

from app.models import Followup, Partner, PartnerContact, PartnerUnit, Person
from app.seed import seed_demo, seed_institutional

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


def test_seed_demo_idempotente(db):
    first = seed_demo(db)
    db.commit()
    assert first["criado"]["pessoas"] == 5
    assert first["criado"]["followups"] >= 3
    total_people = db.execute(select(func.count()).select_from(Person)).scalar_one()
    seed_demo(db)
    db.commit()
    assert db.execute(select(func.count()).select_from(Person)).scalar_one() == total_people
    # pessoa 'não contatar' do seed não tem follow-up
    p5 = db.execute(select(Person).where(
        Person.nome_completo == "Paciente Demo 005"
    )).scalar_one()
    fups = db.execute(select(Followup).where(Followup.person_id == p5.id)).scalars().all()
    assert fups == []


def test_seed_demo_marcado_como_sintetico(db):
    seed_demo(db)
    db.commit()
    for person in db.execute(select(Person)).scalars():
        assert person.legacy_source == "seed_demo"
        assert "SINTÉTICO" in (person.observacao or "")


def test_seed_institucional_idempotente_sem_inventar_dados(db):
    spec = {
        "parceiros": [{
            "nome": "Clínica Institucional Teste",
            "tipo": "clinica",
            "status_parceria": "ativa",
            "unidades": [{"nome": "Unidade Bairro Teste"}],
            "contatos": [{"nome": "Contato Diretor Teste",
                          "cargo": "Diretor Médico", "principal": True,
                          "unidade": "Unidade Bairro Teste"}],
        }]
    }
    first = seed_institutional(db, spec)
    db.commit()
    assert first["resultados"][0]["status"] == "criado"
    second = seed_institutional(db, spec)
    db.commit()
    assert second["resultados"][0]["status"] == "ja_existia"
    assert db.execute(select(func.count()).select_from(Partner)).scalar_one() == 1
    assert db.execute(select(func.count()).select_from(PartnerUnit)).scalar_one() == 1
    contato = db.execute(select(PartnerContact)).scalar_one()
    # nada inventado: telefone e e-mail ficam nulos
    assert contato.telefone is None
    assert contato.email is None
    assert contato.cargo == "Diretor Médico"
    assert contato.principal is True


def test_import_dry_run_via_api_exige_admin(client, auth):
    files = {"file": ("leads.csv", (FIXTURES / "leads.csv").read_bytes(), "text/csv")}
    denied = client.post(
        "/api/v1/importacoes/dry-run",
        data={"source_type": "leads"},
        files=files,
        headers=auth("gestor"),
    )
    assert denied.status_code == 403


def test_import_dry_run_via_api(client, auth):
    files = {"file": ("leads.csv", (FIXTURES / "leads.csv").read_bytes(), "text/csv")}
    resp = client.post(
        "/api/v1/importacoes/dry-run",
        data={"source_type": "leads"},
        files=files,
        headers=auth("admin"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "dry_run"
    assert body["total"] == 7
    assert body["aviso"].startswith("DRY-RUN")
    # dry-run não cria pessoas
    lista = client.get("/api/v1/pessoas", headers=auth("leitura")).json()
    assert lista["total"] == 0


def test_import_source_type_invalido(client, auth):
    files = {"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}
    resp = client.post(
        "/api/v1/importacoes/dry-run",
        data={"source_type": "planilha_qualquer"},
        files=files,
        headers=auth("admin"),
    )
    assert resp.status_code == 422
