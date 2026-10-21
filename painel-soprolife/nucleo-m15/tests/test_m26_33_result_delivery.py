"""M26.33 — terceiro autorizado a receber resultado, sem virar paciente.

Cenário sintético que espelha a operação real que criou esta estrutura: dois
pacientes adultos, atendidos no mesmo dia, pagos por uma terceira pessoa que
é também quem recebe os dois resultados. Tudo aqui é sintético — nomes,
CPFs (verificadores fechados, mas de ninguém) e telefones não discáveis.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.models import (
    AuditLog,
    Consultation,
    Person,
    PersonRelationship,
    ResultDeliveryAuthorization,
    SpirometryExam,
)

API = "/api/v1"

# Telefones sintéticos NÃO DISCÁVEIS (prefixo local 0000, não atribuído).
TEL_PACIENTE_A = "(21) 0000-9101"
TEL_PACIENTE_B = "(21) 0000-9102"
TEL_TERCEIRO = "(21) 0000-9103"

# CPFs sintéticos com dígitos verificadores válidos e sem dígito repetido.
CPF_PACIENTE_A = "19166499712"
CPF_PACIENTE_B = "76266314800"
CPF_TERCEIRO = "35744207813"


def _pessoa(client, auth, nome, cpf, telefone, **extra):
    resp = client.post(
        f"{API}/pessoas",
        json={
            "nome_completo": nome,
            "cpf": cpf,
            "contatos": [{"tipo": "whatsapp", "valor": telefone, "principal": True}],
            "consentimento_whatsapp": "concedido",
            **extra,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def casal(client, auth):
    """Dois pacientes e a terceira pessoa que paga e recebe os resultados."""
    return {
        "paciente_a": _pessoa(
            client, auth, "Primeira Pessoa Sintetica", CPF_PACIENTE_A,
            TEL_PACIENTE_A, data_nascimento="1940-11-30", sexo="feminino",
        ),
        "paciente_b": _pessoa(
            client, auth, "Segunda Pessoa Sintetica", CPF_PACIENTE_B,
            TEL_PACIENTE_B, data_nascimento="1939-02-28", sexo="masculino",
        ),
        "terceiro": _pessoa(
            client, auth, "Terceira Pessoa Sintetica", CPF_TERCEIRO, TEL_TERCEIRO,
        ),
    }


def _autorizar(client, auth, paciente_id, destinatario_id, tipo="daughter", **extra):
    return client.post(
        f"{API}/pessoas/{paciente_id}/destinatarios-resultado",
        json={
            "recipient_person_id": destinatario_id,
            "relationship_type": tipo,
            **extra,
        },
        headers=auth("operacional"),
    )


# ── a autorização em si ───────────────────────────────────────────────────


def test_autoriza_destinatario_e_lista(client, auth, casal):
    resp = _autorizar(
        client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"]
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["patient_person_id"] == casal["paciente_a"]["id"]
    assert body["recipient_person_id"] == casal["terceiro"]["id"]
    assert body["relationship_type"] == "daughter"
    assert body["active"] is True
    assert body["replay"] is False
    assert body["created_at"] and body["registered_by_user_id"]

    listagem = client.get(
        f"{API}/pessoas/{casal['paciente_a']['id']}/destinatarios-resultado",
        headers=auth("leitura"),
    )
    assert listagem.status_code == 200
    itens = listagem.json()["items"]
    assert [i["id"] for i in itens] == [body["id"]]


def test_replay_identico_devolve_a_mesma_linha(client, auth, casal, db):
    primeiro = _autorizar(
        client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"]
    )
    segundo = _autorizar(
        client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"]
    )
    assert segundo.status_code == 201, segundo.text
    assert segundo.json()["id"] == primeiro.json()["id"]
    assert segundo.json()["replay"] is True
    assert db.query(ResultDeliveryAuthorization).count() == 1


def test_replay_divergente_e_recusado(client, auth, casal):
    _autorizar(client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"])
    conflito = _autorizar(
        client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"],
        tipo="spouse",
    )
    assert conflito.status_code == 409
    assert conflito.json()["erro"]["mensagem"]["codigo"] == "authorization_payload_conflict"


def test_paciente_nao_autoriza_a_si_mesmo(client, auth, casal):
    resp = _autorizar(
        client, auth, casal["paciente_a"]["id"], casal["paciente_a"]["id"]
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "patient_and_recipient_must_differ"


def test_destinatario_inexistente_e_404(client, auth, casal):
    resp = _autorizar(
        client, auth, casal["paciente_a"]["id"],
        "00000000-0000-0000-0000-000000000000",
    )
    assert resp.status_code == 404
    assert resp.json()["erro"]["mensagem"]["codigo"] == "recipient_person_not_found"


def test_relacao_de_tutela_nao_cabe_nesta_rota(client, auth, casal):
    """`legal_guardian` é recusado pelo CONTRATO, não por convenção."""
    resp = _autorizar(
        client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"],
        tipo="legal_guardian",
    )
    assert resp.status_code == 422


def test_leitura_nao_autoriza(client, auth, casal):
    resp = client.post(
        f"{API}/pessoas/{casal['paciente_a']['id']}/destinatarios-resultado",
        json={
            "recipient_person_id": casal["terceiro"]["id"],
            "relationship_type": "daughter",
        },
        headers=auth("leitura"),
    )
    assert resp.status_code == 403


def test_desativar_e_idempotente(client, auth, casal):
    criado = _autorizar(
        client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"]
    ).json()
    url = (
        f"{API}/pessoas/{casal['paciente_a']['id']}"
        f"/destinatarios-resultado/{criado['id']}/desativar"
    )
    corpo = {"reason_code": "authorization_withdrawn"}
    primeiro = client.post(url, json=corpo, headers=auth("operacional"))
    assert primeiro.status_code == 200
    assert primeiro.json()["active"] is False
    segundo = client.post(url, json=corpo, headers=auth("operacional"))
    assert segundo.status_code == 200
    assert segundo.json()["active"] is False

    ativos = client.get(
        f"{API}/pessoas/{casal['paciente_a']['id']}/destinatarios-resultado"
        "?active=true",
        headers=auth("leitura"),
    )
    assert ativos.json()["items"] == []


# ── o que a estrutura NÃO faz ─────────────────────────────────────────────


def test_destinatario_nao_vira_paciente_nem_responsavel(client, auth, casal, db):
    """O fato central: autorizar entrega não cria atendimento nem tutela."""
    for paciente in ("paciente_a", "paciente_b"):
        assert _autorizar(
            client, auth, casal[paciente]["id"], casal["terceiro"]["id"]
        ).status_code == 201

    terceiro_id = casal["terceiro"]["id"]
    assert db.query(SpirometryExam).filter_by(person_id=terceiro_id).count() == 0
    assert db.query(Consultation).filter_by(person_id=terceiro_id).count() == 0
    # A tabela menor–responsável fica intocada: nenhuma tutela foi afirmada.
    assert db.query(PersonRelationship).count() == 0
    # E o destinatário continua uma pessoa corporativa comum, não arquivada.
    terceiro = db.get(Person, terceiro_id)
    assert terceiro is not None and terceiro.arquivado is False


def test_auditoria_registra_a_relacao_e_nunca_a_identidade(
    client, auth, casal, db
):
    _autorizar(client, auth, casal["paciente_a"]["id"], casal["terceiro"]["id"])
    linhas = (
        db.query(AuditLog)
        .filter(AuditLog.acao == "pessoa.destinatario_resultado_autorizado")
        .all()
    )
    assert len(linhas) == 1
    registro = linhas[0]
    assert registro.entidade == "result_delivery_authorizations"
    payload = str(registro.detalhes or {})
    assert "daughter" in payload
    # Nenhum identificador pessoal cabe na trilha: só IDs técnicos.
    for sigiloso in (
        CPF_TERCEIRO, CPF_PACIENTE_A, "0000-9103", "552100009103",
        "Terceira Pessoa Sintetica",
    ):
        assert sigiloso not in payload


# ── o cenário econômico completo, sem duplicar receita ────────────────────


def test_dois_exames_domiciliares_pagos_por_terceiro_somam_quinhentos(
    client, auth, casal, db
):
    atendimentos = {}
    for chave, nome_tel in (("paciente_a", TEL_PACIENTE_A), ("paciente_b", TEL_PACIENTE_B)):
        resp = client.post(
            f"{API}/atendimentos",
            json={
                "person_id": casal[chave]["id"],
                "tipo": "espirometria_soprolife",
                "espirometria": {
                    "data_exame": "2026-07-20",
                    "status": "Realizado",
                    "broncodilatador": True,
                    "modalidade": "residencial",
                    "municipio_atendimento_ibge": "3304557",
                },
                "financeiro": {
                    "espirometria": {
                        "valor": "250.00",
                        "status": "Recebido",
                        "data_competencia": "2026-07-20",
                        "data_recebimento": "2026-07-20",
                        "forma_pagamento": "Pix",
                    }
                },
            },
            headers=auth("operacional"),
        )
        assert resp.status_code == 201, resp.text
        atendimentos[chave] = resp.json()

    # E o terceiro é o destinatário autorizado dos DOIS resultados. Toda a
    # escrita acontece antes de qualquer leitura pela sessão da fixture: duas
    # conexões SQLite na mesma transação travariam o banco (ver
    # test_db_sqlite_concurrency).
    terceiro_id = casal["terceiro"]["id"]
    for chave in ("paciente_a", "paciente_b"):
        assert _autorizar(
            client, auth, casal[chave]["id"], terceiro_id
        ).status_code == 201

    # Duas receitas de 250, uma por exame — e nada além delas.
    lancamentos = [
        lan
        for atendimento in atendimentos.values()
        for lan in atendimento["lancamentos"]
    ]
    assert len(lancamentos) == 2
    assert sorted(str(lan["valor"]) for lan in lancamentos) == ["250.00", "250.00"]

    from decimal import Decimal

    from app.models import FinancialEntry

    receitas = db.query(FinancialEntry).filter_by(tipo="receita").all()
    assert len(receitas) == 2
    assert sum(r.valor for r in receitas) == Decimal("500.00")
    assert {r.forma_pagamento for r in receitas} == {"Pix"}
    assert {r.status for r in receitas} == {"Recebido"}
    # Cada receita pendurada no SEU exame, e os dois exames são de pacientes
    # distintos: é isso que impede uma nota de R$500 em nome de um deles.
    assert {r.spirometry_exam_id for r in receitas} == {
        atendimentos["paciente_a"]["espirometria"]["id"],
        atendimentos["paciente_b"]["espirometria"]["id"],
    }

    # O terceiro que pagou não tem exame nem receita em seu nome.
    assert db.query(SpirometryExam).filter_by(person_id=terceiro_id).count() == 0
    assert (
        db.query(ResultDeliveryAuthorization)
        .filter_by(recipient_person_id=terceiro_id, active=True)
        .count()
        == 2
    )


# ── migration ─────────────────────────────────────────────────────────────


def test_migration_aditiva_reversivel_e_recusa_apagar_autorizacao(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m26-33.db"
    root = Path(__file__).parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["a1c7e5d9b204"]
    assert script.get_revision("a1c7e5d9b204").down_revision == "e2a0b6c719af"

    command.upgrade(cfg, "e2a0b6c719af")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert "result_delivery_authorizations" not in inspector.get_table_names()
    antes = {
        t: [c["name"] for c in inspect(engine).get_columns(t)]
        for t in inspect(engine).get_table_names()
    }

    command.upgrade(cfg, "head")
    command.check(cfg)
    depois = {
        t: [c["name"] for c in inspect(engine).get_columns(t)] for t in antes
    }
    # Aditiva de verdade: nenhuma tabela preexistente mudou de forma.
    assert antes == depois
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM result_delivery_authorizations")
        ).scalar() == 0

    # Vazia, desce e sobe de novo sem drama.
    command.downgrade(cfg, "e2a0b6c719af")
    command.upgrade(cfg, "head")
    command.check(cfg)

    # Com uma autorização dentro, o downgrade se recusa a apagá-la.
    from uuid import uuid4

    from sqlalchemy.orm import Session

    from app.models import User
    from app.security import hash_password

    paciente, destinatario = str(uuid4()), str(uuid4())
    with Session(engine) as db:
        db.add(
            Person(
                id=paciente,
                public_code="PES-900101",
                nome_completo="Paciente Sintetico Migration",
                nome_normalizado="paciente sintetico migration",
            )
        )
        db.add(
            Person(
                id=destinatario,
                public_code="PES-900102",
                nome_completo="Destinatario Sintetico Migration",
                nome_normalizado="destinatario sintetico migration",
            )
        )
        usuario = User(
            email="migration@teste.local",
            nome="Teste Migration",
            password_hash=hash_password("senha-teste-123"),
        )
        db.add(usuario)
        db.commit()
        db.add(
            ResultDeliveryAuthorization(
                patient_person_id=paciente,
                recipient_person_id=destinatario,
                relationship_type="daughter",
                registered_by_user_id=usuario.id,
            )
        )
        db.commit()

    with pytest.raises(RuntimeError, match="Downgrade recusado"):
        command.downgrade(cfg, "e2a0b6c719af")
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM result_delivery_authorizations")
        ).scalar() == 1
    engine.dispose()
