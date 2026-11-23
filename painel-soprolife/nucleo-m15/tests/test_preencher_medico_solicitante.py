"""Preenchimento operacional: só age com identificação inequívoca."""

import pytest

from app.models import Person, ReportDocument, SpirometryExam, User
from app.normalize import normalize_name
from app.schemas import MedicoSolicitante
from scripts.preencher_medico_solicitante import preencher
from tests.test_m25_2_native_report import _make_case, reports_enabled  # noqa: F401

MEDICO = MedicoSolicitante(
    medico_solicitante_nome="Dra. Teste Ção", medico_solicitante_crm="12345-6"
)


def _caso(client, auth, db, nome, suffix):
    pessoa = client.post(
        "/api/v1/pessoas", json={"nome_completo": nome}, headers=auth("operacional")
    )
    assert pessoa.status_code == 201, pessoa.text
    corpo = pessoa.json()
    return _make_case(client, auth, db, corpo.get("pessoa", corpo), suffix=suffix)


def test_preenche_so_os_dois_e_aborta_se_ambiguo(client, auth, db):
    a = _caso(client, auth, db, "Aurora Teste Sintetico", "701")
    b = _caso(client, auth, db, "Bento Teste Sintetico", "702")
    c = _caso(client, auth, db, "Caio Outro", "703")
    operador = db.query(User).first()
    nomes = ["Aurora", "Bento"]
    preencher(db, nomes, MEDICO, None, False)
    assert db.get(SpirometryExam, a["exam"]["id"]).medico_solicitante_nome is None
    preencher(db, nomes, MEDICO, operador, True)
    preencher(db, nomes, MEDICO, operador, True)  # idempotente
    for x, esperado in ((a, "Dra. Teste Ção"), (b, "Dra. Teste Ção"), (c, None)):
        assert db.get(SpirometryExam, x["exam"]["id"]).medico_solicitante_nome == esperado
        assert db.get(ReportDocument, x["document"]["id"]).status == "atribuido"
    with pytest.raises(SystemExit, match="sobrenome"):
        preencher(db, ["Aurora", "Caio"], MEDICO, operador, True)
    with pytest.raises(SystemExit, match="0 exames"):
        preencher(db, ["Aurora", "Ninguem"], MEDICO, operador, True)
    pessoa = db.get(Person, db.get(SpirometryExam, c["exam"]["id"]).person_id)
    pessoa.nome_completo = "Aurora Outro"
    pessoa.nome_normalizado = normalize_name(pessoa.nome_completo)
    db.commit()
    with pytest.raises(SystemExit, match="2 exames"):
        preencher(db, nomes, MEDICO, operador, True)
