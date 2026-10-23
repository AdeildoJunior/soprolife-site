"""M26.36 no banco de VERDADE — os gatilhos que o SQLite não tem.

POR QUE ESTE ARQUIVO EXISTE

A primeira implementação do cancelamento APAGAVA o documento corretivo e as
linhas que nasceram com ele. Os 14 testes em SQLite passaram e a coisa só
quebrou em produção, com 500: `report_assignments`,
`report_assignment_events` e `report_document_versions` são append-only por
gatilho (M24C), e `create_all` em SQLite não cria gatilho nenhum.

Este arquivo fecha essa lacuna. Ele não testa a API: testa o CONTRATO ENTRE
O DESENHO E O BANCO — que as mutações que o endpoint faz são aceitas, e que
as que a primeira versão fazia são recusadas. Sem ele, a mesma classe de
erro volta na próxima vez que alguém mexer aqui.

Pula sem `M15_TEST_POSTGRES_URL`, como o resto do harness.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_nfse_migrations import config, postgres_url  # noqa: F401


IMUTAVEL = "M24C append-only"


@pytest.fixture()
def banco(postgres_url):  # noqa: F811 — fixture do harness
    """Banco descartável na revisão de produção, com todos os gatilhos."""

    cfg = config(postgres_url)
    command.upgrade(cfg, "head")
    engine = create_engine(postgres_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _semear(conn) -> dict:
    """Um predecessor liberado e a corretiva que o clique equivocado cria.

    SQL cru de propósito: o que se testa aqui é o banco, não o ORM.
    Identificadores sintéticos, nenhum dado real.
    """

    ids = {k: str(uuid4()) for k in (
        "user", "verificador", "pessoa", "exame", "perfil", "pred", "corr",
        "v_pred", "v_corr", "atrib_pred", "atrib_corr", "ev_pred", "ev_corr",
    )}
    conn.execute(text(
        "INSERT INTO users (id, nome, email, password_hash, ativo, "
        "created_at, updated_at) VALUES (:id, 'Operador Sintetico', "
        ":email, 'x', true, now(), now())"),
        {"id": ids["user"], "email": f"m2636-{ids['user'][:8]}@teste.invalid"})
    # Quem verifica não pode ser a própria médica (`verification_not_self`).
    conn.execute(text(
        "INSERT INTO users (id, nome, email, password_hash, ativo, "
        "created_at, updated_at) VALUES (:id, 'Admin Sintetico', "
        ":email, 'x', true, now(), now())"),
        {"id": ids["verificador"],
         "email": f"m2636v-{ids['verificador'][:8]}@teste.invalid"})
    conn.execute(text(
        "INSERT INTO people (id, public_code, nome_completo, nome_normalizado, "
        "status, nao_contatar, arquivado, created_at, updated_at) VALUES "
        "(:id, :code, 'Paciente Sintetico', 'paciente sintetico', 'ativo', "
        "false, false, now(), now())"),
        {"id": ids["pessoa"], "code": f"PES-X{ids['pessoa'][:5]}"})
    conn.execute(text(
        "INSERT INTO spirometry_exams (id, public_code, person_id, status, "
        "data_exame, data_exame_dia_assumido, modalidade, created_at, "
        "updated_at) VALUES (:id, :code, :pessoa, 'Realizado', "
        "'2026-10-20', false, 'residencial', now(), now())"),
        {"id": ids["exame"], "code": f"ESP-X{ids['exame'][:5]}",
         "pessoa": ids["pessoa"]})
    # O gatilho `m24c_validate_active_assignment` exige médica ATIVA,
    # VERIFICADA e com o papel explícito — a mesma regra que impede atribuir
    # laudo a quem não é médico. O vínculo de papel entra junto.
    conn.execute(text(
        "INSERT INTO user_roles (user_id, role_id) SELECT :user, id FROM "
        "roles WHERE name = 'medico'"), {"user": ids["user"]})
    conn.execute(text(
        "INSERT INTO physician_profiles (id, user_id, professional_name, "
        "crm_number, crm_state, active, verification_status, verified_at, "
        "verified_by_user_id, verification_reference, created_at, "
        "updated_at) VALUES "
        "(:id, :user, 'Medica Sintetica', :crm, 'RJ', true, 'verified', "
        "now(), :verificador, 'OFICIO-SINTETICO-M2636', now(), now())"),
        {"id": ids["perfil"], "user": ids["user"],
         "verificador": ids["verificador"],
         "crm": str(abs(hash(ids["perfil"])) % 10**6).zfill(6)})

    for chave, codigo, corrige in (
        ("pred", "LAU-X0001", None),
        ("corr", "LAU-X0002", ids["pred"]),
    ):
        conn.execute(text(
            "INSERT INTO report_documents (id, public_code, "
            "spirometry_exam_id, status, created_by_user_id, "
            "corrects_document_id, correction_reason_code, created_at, "
            "updated_at) VALUES (:id, :code, :exame, 'atribuido', :user, "
            ":corrige, :motivo, now(), now())"),
            {"id": ids[chave], "code": f"{codigo}{ids[chave][:3]}",
             "exame": ids["exame"], "user": ids["user"], "corrige": corrige,
             "motivo": "technical_document_correction" if corrige else None})
        versao = ids["v_pred" if chave == "pred" else "v_corr"]
        conn.execute(text(
            "INSERT INTO report_document_versions (id, report_document_id, "
            "kind, version_number, storage_path, sha256, size_bytes, "
            "page_count, mime_type, created_by_user_id, created_at) VALUES "
            "(:id, :doc, 'original', 1, :path, :sha, 100, 1, "
            "'application/pdf', :user, now())"),
            {"id": versao, "doc": ids[chave],
             "path": f"laudos/{ids['exame']}/{ids[chave]}/{versao}.pdf",
             "sha": (versao.replace("-", "") * 2)[:64],
             "user": ids["user"]})
        atrib = ids["atrib_pred" if chave == "pred" else "atrib_corr"]
        conn.execute(text(
            "INSERT INTO report_assignments (id, report_document_id, "
            "physician_profile_id, active, assigned_at, assigned_by_user_id, "
            "reason_code) VALUES (:id, :doc, :perfil, true, now(), :user, "
            ":motivo)"),
            {"id": atrib, "doc": ids[chave], "perfil": ids["perfil"],
             "user": ids["user"],
             "motivo": "corrective_document" if corrige
                       else "initial_assignment"})
        conn.execute(text(
            "INSERT INTO report_assignment_events (id, report_document_id, "
            "assignment_id, event_type, physician_profile_id, reason_code, "
            "performed_by_user_id, created_at) VALUES (:id, :doc, :atrib, "
            ":tipo, :perfil, :motivo, :user, now())"),
            {"id": ids["ev_pred" if chave == "pred" else "ev_corr"],
             "doc": ids[chave], "atrib": atrib,
             "tipo": "corrective_assigned" if corrige else "assigned",
             "perfil": ids["perfil"],
             "motivo": "corrective_document" if corrige
                       else "initial_assignment",
             "user": ids["user"]})
    return ids


# ======================================================================
# 1. o que o banco RECUSA — a razão de o desenho ter mudado
# ======================================================================


@pytest.mark.parametrize("tabela, coluna", [
    ("report_assignment_events", "report_document_id"),
    ("report_assignments", "report_document_id"),
    ("report_document_versions", "report_document_id"),
])
def test_o_banco_recusa_apagar_a_evidencia_da_corretiva(banco, tabela, coluna):
    """A primeira versão da M26.36 fazia exatamente estes três DELETEs."""

    with banco.begin() as conn:
        ids = _semear(conn)
    with banco.connect() as conn:
        with pytest.raises(DBAPIError, match=IMUTAVEL):
            conn.execute(
                text(f"DELETE FROM {tabela} WHERE {coluna} = :doc"),
                {"doc": ids["corr"]},
            )


def test_o_banco_recusa_reescrever_a_atribuicao(banco):
    with banco.begin() as conn:
        ids = _semear(conn)
    with banco.connect() as conn:
        with pytest.raises(DBAPIError, match=IMUTAVEL):
            conn.execute(
                text("UPDATE report_assignments SET reason_code = "
                     "'initial_assignment' WHERE id = :a"),
                {"a": ids["atrib_corr"]},
            )


# ======================================================================
# 2. o que o banco ACEITA — exatamente o que o endpoint faz
# ======================================================================


def test_o_banco_aceita_encerrar_a_atribuicao_e_marcar_o_cancelamento(banco):
    """As duas únicas escritas do cancelamento, na forma em que ele as faz."""

    with banco.begin() as conn:
        ids = _semear(conn)

    with banco.begin() as conn:
        # (a) encerrar a atribuição: active true->false COM ended_at — a
        # única mutação que `m24c_guard_assignment_history` permite.
        conn.execute(
            text("UPDATE report_assignments SET active = false, "
                 "ended_at = now() WHERE id = :a"),
            {"a": ids["atrib_corr"]},
        )
        # (b) declarar o cancelamento no documento.
        conn.execute(
            text("UPDATE report_documents SET devolucao_cancelada_em = now(), "
                 "devolucao_cancelada_por_user_id = :u WHERE id = :d"),
            {"u": ids["user"], "d": ids["corr"]},
        )

    with banco.connect() as conn:
        assert conn.execute(
            text("SELECT active, ended_at IS NOT NULL FROM report_assignments "
                 "WHERE id = :a"), {"a": ids["atrib_corr"]}).one() == (
            False, True)
        assert conn.execute(
            text("SELECT devolucao_cancelada_em IS NOT NULL FROM "
                 "report_documents WHERE id = :d"), {"d": ids["corr"]}
        ).scalar() is True
        # A evidência continua inteira: versão e evento no lugar.
        assert conn.execute(
            text("SELECT count(*) FROM report_document_versions WHERE "
                 "report_document_id = :d"), {"d": ids["corr"]}).scalar() == 1
        assert conn.execute(
            text("SELECT count(*) FROM report_assignment_events WHERE "
                 "report_document_id = :d"), {"d": ids["corr"]}).scalar() == 1


def test_encerrar_a_atribuicao_sem_ended_at_e_recusado(banco):
    """O gatilho exige a evidência completa, não só o `active = false`."""

    with banco.begin() as conn:
        ids = _semear(conn)
    with banco.connect() as conn:
        with pytest.raises(DBAPIError, match=IMUTAVEL):
            conn.execute(
                text("UPDATE report_assignments SET active = false "
                     "WHERE id = :a"),
                {"a": ids["atrib_corr"]},
            )


# ======================================================================
# 3. o índice único parcial
# ======================================================================


def test_duas_corretivas_vivas_do_mesmo_laudo_sao_recusadas(banco):
    with banco.begin() as conn:
        ids = _semear(conn)
    with banco.connect() as conn:
        with pytest.raises(DBAPIError, match="uq_report_documents_corretiva_viva"):
            with conn.begin():
                conn.execute(text(
                    "INSERT INTO report_documents (id, public_code, "
                    "spirometry_exam_id, status, created_by_user_id, "
                    "corrects_document_id, created_at, updated_at) VALUES "
                    "(:id, :code, :exame, 'atribuido', :user, :pred, now(), "
                    "now())"),
                    {"id": str(uuid4()), "code": "LAU-XDUP01",
                     "exame": ids["exame"], "user": ids["user"],
                     "pred": ids["pred"]})


def test_depois_de_cancelar_uma_corretiva_nova_e_aceita(banco):
    """A vaga é liberada: o engano não tranca a correção legítima."""

    with banco.begin() as conn:
        ids = _semear(conn)
    with banco.begin() as conn:
        conn.execute(
            text("UPDATE report_documents SET devolucao_cancelada_em = now(), "
                 "devolucao_cancelada_por_user_id = :u WHERE id = :d"),
            {"u": ids["user"], "d": ids["corr"]},
        )
    novo = str(uuid4())
    with banco.begin() as conn:
        conn.execute(text(
            "INSERT INTO report_documents (id, public_code, "
            "spirometry_exam_id, status, created_by_user_id, "
            "corrects_document_id, created_at, updated_at) VALUES "
            "(:id, :code, :exame, 'atribuido', :user, :pred, now(), now())"),
            {"id": novo, "code": "LAU-XNEW01", "exame": ids["exame"],
             "user": ids["user"], "pred": ids["pred"]})
    with banco.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM report_documents WHERE "
            "corrects_document_id = :p"), {"p": ids["pred"]}).scalar() == 2


# ======================================================================
# 4. os dois CHECKs da evidência
# ======================================================================


def test_cancelamento_sem_autor_e_recusado(banco):
    with banco.begin() as conn:
        ids = _semear(conn)
    with banco.connect() as conn:
        with pytest.raises(DBAPIError, match="devolucao_cancelada_com_evidencia"):
            conn.execute(
                text("UPDATE report_documents SET devolucao_cancelada_em = "
                     "now() WHERE id = :d"), {"d": ids["corr"]})


def test_laudo_que_nao_e_corretiva_nao_pode_ser_cancelado_no_banco(banco):
    with banco.begin() as conn:
        ids = _semear(conn)
    with banco.connect() as conn:
        with pytest.raises(
            DBAPIError, match="devolucao_cancelada_so_em_corretiva"
        ):
            conn.execute(
                text("UPDATE report_documents SET devolucao_cancelada_em = "
                     "now(), devolucao_cancelada_por_user_id = :u "
                     "WHERE id = :d"),
                {"u": ids["user"], "d": ids["pred"]})
