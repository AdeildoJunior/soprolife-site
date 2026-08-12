"""M25.24 — ajuda contextual da área médica e encerramento histórico.

O que estes testes travam, em ordem de gravidade:

1. **Encerrar não é apagar.** Exame, PDF do equipamento, laudos, versões,
   hashes e auditoria continuam byte a byte onde estavam. Um encerramento que
   destruísse qualquer um deles seria destruição de prontuário.
2. **Encerrado some das filas de TRABALHO e só delas.** Fila da médica, fila
   administrativa, "recentes sem laudo", assinatura externa e fila de entrega.
   Continua achável na visão explícita de históricos e por código exato.
3. **Nenhum estado inventado.** O rótulo do filtro não pode afirmar
   ICP-Brasil, porque nenhum caminho deste sistema confere a cadeia.
4. **Reversível.** Um gestor reabre e o exame volta exatamente como estava.
5. **Idempotente.** Reprocessar o lote não cria segundo encerramento nem
   reescreve a autoria da decisão original.
6. **A ajuda não vaza.** Os textos são estáticos e funcionais — nenhum deles
   carrega nome de paciente, valor ou dado administrativo.

Todos os pacientes, médicos, CRMs e PDFs são sintéticos.
"""

from __future__ import annotations

import io
import pathlib
import re

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AuditLog,
    Person,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.schemas import ExamClosureReason
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)
from app.services.exam_closure import (
    CLOSURE_REASONS,
    CLOSURE_SHORT_LABELS,
    ExamClosureError,
    close_exam,
    is_closed,
    reopen_exam,
)

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()

RELEASE_CONFIRMATION = "ASSINAR E LIBERAR"


# ------------------------------------------------------------ utilidades


def _minimal_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    saida = io.BytesIO()
    writer.write(saida)
    return saida.getvalue()


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_REPORTS_VALIDATION_BASE_URL",
        "https://painel-teste.soprolife.local/validar",
    )
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m25-24-encerramento-historico-secret-de-teste-0123456789",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _physician(db, *, suffix: str) -> tuple[User, dict]:
    ensure_roles_exist(db)
    user = User(
        email=f"medica-m25-24-{suffix}@teste.local",
        nome=f"TESTE APAGAR Médica {suffix}",
        password_hash=hash_password("senha-medica-sintetica-m2524"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    return user, {
        "Authorization": f"Bearer {issue_token(user.id, user.password_hash)}"
    }


def _configure_profile(client, auth, user, *, crm, name):
    resposta = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json={
            "grant_physician_role": True,
            "professional_name": name,
            "crm_number": crm,
            "crm_state": "RJ",
            "rqe": "RQE-TESTE-58224",
            "verification_status": "verified",
            "verification_reference": "CRM-VERIF-TESTE-M2524",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["profile"]


def _criar_exame(client, auth, *, nome: str) -> tuple[dict, dict]:
    pessoa = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": nome},
        headers=auth("operacional"),
    )
    assert pessoa.status_code == 201, pessoa.text
    pessoa = pessoa.json()
    exame = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-07-14",
                "status": "Realizado",
                "broncodilatador": True,
            },
        },
        headers=auth("operacional"),
    )
    assert exame.status_code == 201, exame.text
    return pessoa, exame.json()["espirometria"]


def _anexar(client, auth, exame, profile) -> dict:
    resposta = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-teste-m2524",
        },
        files={"file": ("TESTE-APAGAR.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _concluir(client, doctor_auth, document_id) -> dict:
    previa = client.post(
        f"/api/v1/laudos/{document_id}/laudo/previa",
        json={
            "conclusion_code": "DVO_MODERADO",
            "bronchodilator_code": "RBD_POSITIVO",
        },
        headers=doctor_auth,
    )
    assert previa.status_code == 200, previa.text
    corpo = previa.json()
    liberado = client.post(
        f"/api/v1/laudos/{document_id}/assinar-e-liberar",
        json={
            "confirmacao": RELEASE_CONFIRMATION,
            "expected_version_id": corpo["preview_version_id"],
            "expected_text_sha256": corpo["final_text_sha256"],
        },
        headers=doctor_auth,
    )
    assert liberado.status_code == 200, liberado.text
    return liberado.json()


def _encerrar(client, auth, exam_code, *, motivo=None, observacao=None):
    return client.post(
        f"/api/v1/laudos/exames/{exam_code}/encerramento",
        json={
            "motivo": motivo or "laudo_externo_ja_entregue",
            "observacao": observacao or "Laudo entregue pela parceira em julho.",
        },
        headers=auth("operacional"),
    )


@pytest.fixture()
def medica(client, auth, db):
    user, doctor_auth = _physician(db, suffix="01")
    profile = _configure_profile(
        client, auth, user, crm="924001", name="TESTE APAGAR Médica 01"
    )
    return {"user": user, "auth": doctor_auth, "profile": profile}


@pytest.fixture()
def caso(client, auth, medica):
    """Um exame com laudo já concluído — o cenário dos cinco Pastore."""

    pessoa, exame = _criar_exame(
        client, auth, nome="TESTE APAGAR Paciente Historico"
    )
    documento = _anexar(client, auth, exame, medica["profile"])
    concluido = _concluir(client, medica["auth"], documento["id"])
    return {
        "pessoa": pessoa,
        "exame": exame,
        "document_id": documento["id"],
        "report_code": documento["public_code"],
        "concluido": concluido,
        "medica": medica,
    }


# =====================================================================
# 1. Encerrar NÃO apaga
# =====================================================================


def test_encerramento_preserva_exame_laudo_versoes_e_hashes(client, auth, db, caso):
    """A prova mais importante da missão: nada é destruído.

    Se este teste passar a falhar, alguém transformou "sair da fila" em
    "sumir do banco" — que é exatamente o que a missão proibiu.
    """

    exam_id = db.execute(
        select(SpirometryExam.id).where(
            SpirometryExam.public_code == caso["exame"]["public_code"]
        )
    ).scalar_one()
    antes_versoes = db.execute(
        select(ReportDocumentVersion.id, ReportDocumentVersion.sha256)
        .where(ReportDocumentVersion.report_document_id == caso["document_id"])
        .order_by(ReportDocumentVersion.id)
    ).all()
    antes_status = db.get(ReportDocument, caso["document_id"]).status
    assert antes_versoes, "cenário inválido: laudo sem versões"
    # Fecha a transação de leitura: em SQLite ela bloquearia a escrita que a
    # requisição seguinte precisa fazer.
    db.rollback()

    resposta = _encerrar(client, auth, caso["exame"]["public_code"])
    assert resposta.status_code == 200, resposta.text
    db.expire_all()

    # O exame continua existindo, com a mesma pessoa e a mesma data.
    exame = db.get(SpirometryExam, exam_id)
    assert exame is not None
    assert db.get(Person, exame.person_id) is not None
    # O laudo continua existindo, no MESMO estado clínico.
    documento = db.get(ReportDocument, caso["document_id"])
    assert documento is not None
    assert documento.status == antes_status
    # As versões e os hashes são os mesmos, uma a uma.
    depois_versoes = db.execute(
        select(ReportDocumentVersion.id, ReportDocumentVersion.sha256)
        .where(ReportDocumentVersion.report_document_id == caso["document_id"])
        .order_by(ReportDocumentVersion.id)
    ).all()
    assert depois_versoes == antes_versoes


def test_encerramento_nao_toca_no_status_clinico_do_exame(client, auth, db, caso):
    """`status` descreve o ATO; o encerramento descreve a FILA."""

    antes = db.execute(
        select(SpirometryExam.status).where(
            SpirometryExam.public_code == caso["exame"]["public_code"]
        )
    ).scalar_one()
    db.rollback()
    _encerrar(client, auth, caso["exame"]["public_code"])
    db.expire_all()
    depois = db.execute(
        select(SpirometryExam.status).where(
            SpirometryExam.public_code == caso["exame"]["public_code"]
        )
    ).scalar_one()
    assert depois == antes == "Realizado"


def test_encerramento_registra_quem_quando_e_por_que(client, auth, db, users, caso):
    resposta = _encerrar(
        client,
        auth,
        caso["exame"]["public_code"],
        observacao="Laudo entregue pela clínica parceira antes da plataforma.",
    )
    assert resposta.status_code == 200
    db.expire_all()
    exame = db.execute(
        select(SpirometryExam).where(
            SpirometryExam.public_code == caso["exame"]["public_code"]
        )
    ).scalar_one()
    assert exame.encerramento_motivo == "laudo_externo_ja_entregue"
    assert exame.encerrado_em is not None
    assert exame.encerrado_por_user_id == users["operacional"].id
    assert "clínica parceira" in exame.encerramento_observacao

    trilha = db.execute(
        select(AuditLog).where(AuditLog.acao == "exame_encerrado_operacionalmente")
    ).scalars().all()
    assert len(trilha) == 1
    assert trilha[0].entidade_id == exame.id


def test_auditoria_do_encerramento_nao_carrega_nome_de_paciente(
    client, auth, db, caso
):
    """A trilha ganhou chaves novas; nenhuma delas pode virar porta de nome."""

    _encerrar(client, auth, caso["exame"]["public_code"])
    trilha = db.execute(
        select(AuditLog).where(AuditLog.acao == "exame_encerrado_operacionalmente")
    ).scalar_one()
    serializado = repr(trilha.detalhes)
    assert "TESTE APAGAR Paciente" not in serializado
    # Só código institucional, motivo fechado e a observação operacional.
    assert set(trilha.detalhes) <= {"exam_code", "reason_code", "motivo"}


# =====================================================================
# 2. Encerrado sai das filas de TRABALHO — e só delas
# =====================================================================


def test_encerrado_sai_da_fila_da_medica_inclusive_em_todos(client, auth, caso):
    """Nem em "Todos": a médica não pode reencontrar o exame como trabalho."""

    doctor = caso["medica"]["auth"]
    antes = client.get("/api/v1/laudos/meus", headers=doctor).json()
    assert any(i["exam_code"] == caso["exame"]["public_code"] for i in antes)

    _encerrar(client, auth, caso["exame"]["public_code"])

    depois = client.get("/api/v1/laudos/meus", headers=doctor).json()
    assert all(i["exam_code"] != caso["exame"]["public_code"] for i in depois)
    # E em cada filtro de estado, um a um.
    for estado in (
        "atribuido",
        "em_elaboracao",
        "assinatura_pendente",
        "assinado",
        "liberado",
    ):
        filtrada = client.get(
            f"/api/v1/laudos/meus?status={estado}", headers=doctor
        ).json()
        assert all(
            i["exam_code"] != caso["exame"]["public_code"] for i in filtrada
        ), estado


def test_encerrado_sai_da_central_de_assinatura_externa(client, auth, caso):
    """O caso Mauro: laudo de teste não pode pedir assinatura com certificado."""

    doctor = caso["medica"]["auth"]
    antes = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes", headers=doctor
    ).json()
    assert antes["total"] == 1

    _encerrar(
        client,
        auth,
        caso["exame"]["public_code"],
        motivo="laudo_externo_e_teste_do_fluxo",
        observacao="Paciente já recebeu o laudo por fora; laudo daqui é teste.",
    )

    depois = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes", headers=doctor
    ).json()
    assert depois["total"] == 0
    assert depois["laudos"] == []


def test_encerrado_sai_da_fila_de_entrega_da_administracao(client, auth, caso):
    antes = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    ).json()
    assert any(i["exam_code"] == caso["exame"]["public_code"] for i in antes["itens"])

    _encerrar(client, auth, caso["exame"]["public_code"])

    depois = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    ).json()
    assert all(
        i["exam_code"] != caso["exame"]["public_code"] for i in depois["itens"]
    )


def test_encerrado_sai_de_espirometrias_recentes_sem_laudo(client, auth):
    """A lista que originou a missão: exames antigos cobrando laudo."""

    _pessoa, exame = _criar_exame(
        client, auth, nome="TESTE APAGAR Paciente Sem Laudo"
    )
    fila = client.get(
        "/api/v1/laudos/exames?somente_sem_laudo=true", headers=auth("operacional")
    ).json()
    assert any(i["exam_code"] == exame["public_code"] for i in fila)

    _encerrar(client, auth, exame["public_code"])

    fila = client.get(
        "/api/v1/laudos/exames?somente_sem_laudo=true", headers=auth("operacional")
    ).json()
    assert all(i["exam_code"] != exame["public_code"] for i in fila)


def test_fila_operacional_nao_mistura_encerrado_com_pendente(client, auth, caso):
    """"Todos" da administração não pode somar histórico com trabalho de hoje."""

    _encerrar(client, auth, caso["exame"]["public_code"])
    padrao = client.get("/api/v1/laudos", headers=auth("operacional")).json()
    assert all(i["exam_code"] != caso["exame"]["public_code"] for i in padrao)

    # Quem quiser o quadro completo pede por ele — e cada linha vem carimbada.
    completo = client.get(
        "/api/v1/laudos?incluir_encerrados=true", headers=auth("operacional")
    ).json()
    linha = next(
        i for i in completo if i["exam_code"] == caso["exame"]["public_code"]
    )
    assert linha["encerramento"] is not None
    assert linha["encerramento"]["motivo"] == "laudo_externo_ja_entregue"

    somente = client.get(
        "/api/v1/laudos?somente_encerrados=true", headers=auth("operacional")
    ).json()
    assert [i["exam_code"] for i in somente] == [caso["exame"]["public_code"]]


def test_encerrado_continua_localizavel(client, auth, caso):
    """Sair da fila não é sumir: histórico próprio + busca por código exato."""

    _encerrar(client, auth, caso["exame"]["public_code"])

    historicos = client.get(
        "/api/v1/laudos/exames/encerrados", headers=auth("operacional")
    )
    assert historicos.status_code == 200
    corpo = historicos.json()
    assert corpo["total"] == 1
    item = corpo["itens"][0]
    assert item["exam_code"] == caso["exame"]["public_code"]
    assert [l["report_code"] for l in item["laudos"]] == [caso["report_code"]]
    assert item["encerramento"]["motivo_label"] == CLOSURE_REASONS[
        "laudo_externo_ja_entregue"
    ]

    # E pela busca, quando quem procura pede explicitamente.
    achado = client.get(
        f"/api/v1/laudos/exames?q={caso['exame']['public_code']}"
        "&incluir_encerrados=true",
        headers=auth("operacional"),
    ).json()
    assert [i["exam_code"] for i in achado] == [caso["exame"]["public_code"]]


def test_exame_com_dois_laudos_conta_como_UM_historico(client, auth, db, caso):
    """O ESP-000019 real tem laudo original + corretivo.

    O `outerjoin` com `report_documents` multiplicava a linha do exame: a
    tela mostrava o mesmo paciente duas vezes e o contador dizia 19 para 18
    exames encerrados. É lista de EXAMES — um exame, uma linha, com os
    laudos agregados dentro.
    """

    # Segundo laudo no MESMO exame, como o corretivo de produção.
    corretivo = client.post(
        f"/api/v1/laudos/{caso['document_id']}/nova-versao-corretiva",
        json={"reason_code": "clinical_correction"},
        headers=caso["medica"]["auth"],
    )
    assert corretivo.status_code == 201, corretivo.text
    _encerrar(client, auth, caso["exame"]["public_code"])

    corpo = client.get(
        "/api/v1/laudos/exames/encerrados", headers=auth("operacional")
    ).json()
    assert corpo["total"] == 1
    assert len(corpo["itens"]) == 1
    # E os DOIS laudos continuam visíveis dentro da linha.
    codigos = [l["report_code"] for l in corpo["itens"][0]["laudos"]]
    assert len(codigos) == 2
    assert caso["report_code"] in codigos
    assert any(l["is_corrective"] for l in corpo["itens"][0]["laudos"])


# =====================================================================
# 3. Reversibilidade
# =====================================================================


def test_gestor_reabre_e_o_exame_volta_exatamente_como_estava(
    client, auth, db, caso
):
    doctor = caso["medica"]["auth"]
    antes = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes", headers=doctor
    ).json()

    _encerrar(client, auth, caso["exame"]["public_code"])
    reaberto = client.post(
        f"/api/v1/laudos/exames/{caso['exame']['public_code']}/reabertura",
        json={"observacao": "Reconferido: o laudo não havia sido entregue."},
        headers=auth("admin"),
    )
    assert reaberto.status_code == 200, reaberto.text
    assert reaberto.json()["alterado"] is True

    depois = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes", headers=doctor
    ).json()
    assert depois["total"] == antes["total"] == 1
    db.expire_all()
    exame = db.execute(
        select(SpirometryExam).where(
            SpirometryExam.public_code == caso["exame"]["public_code"]
        )
    ).scalar_one()
    assert not is_closed(exame)
    assert exame.encerrado_em is None
    assert exame.encerrado_por_user_id is None
    assert exame.encerramento_observacao is None


def test_reabrir_exige_papel_de_gestao(client, auth, caso):
    """Tirar da fila é rotina; devolver trabalho clínico é decisão de gestão."""

    _encerrar(client, auth, caso["exame"]["public_code"])
    negado = client.post(
        f"/api/v1/laudos/exames/{caso['exame']['public_code']}/reabertura",
        json={"observacao": "Tentativa sem papel de administração."},
        headers=auth("operacional"),
    )
    assert negado.status_code == 403


def test_reabertura_fica_na_trilha(client, auth, db, caso):
    _encerrar(client, auth, caso["exame"]["public_code"])
    client.post(
        f"/api/v1/laudos/exames/{caso['exame']['public_code']}/reabertura",
        json={"observacao": "Reconferido com a parceira."},
        headers=auth("admin"),
    )
    acoes = db.execute(
        select(AuditLog.acao).where(
            AuditLog.entidade == "spirometry_exams"
        ).order_by(AuditLog.id)
    ).scalars().all()
    assert acoes == [
        "exame_encerrado_operacionalmente",
        "exame_reaberto_para_laudo",
    ]


# =====================================================================
# 4. Idempotência
# =====================================================================


def test_reencerrar_nao_cria_segundo_encerramento_nem_reescreve_autoria(
    client, auth, db, caso
):
    """Reprocessar o lote por engano não pode alterar a decisão original."""

    primeira = _encerrar(client, auth, caso["exame"]["public_code"])
    assert primeira.json()["alterado"] is True
    db.expire_all()
    exame = db.execute(
        select(SpirometryExam).where(
            SpirometryExam.public_code == caso["exame"]["public_code"]
        )
    ).scalar_one()
    carimbo_original = exame.encerrado_em
    autor_original = exame.encerrado_por_user_id

    segunda = _encerrar(
        client,
        auth,
        caso["exame"]["public_code"],
        observacao="Texto diferente na segunda tentativa.",
    )
    assert segunda.status_code == 200
    assert segunda.json()["alterado"] is False

    db.expire_all()
    exame = db.execute(
        select(SpirometryExam).where(
            SpirometryExam.public_code == caso["exame"]["public_code"]
        )
    ).scalar_one()
    assert exame.encerrado_em == carimbo_original
    assert exame.encerrado_por_user_id == autor_original
    # E uma única entrada na trilha, não duas.
    total = db.execute(
        select(AuditLog).where(
            AuditLog.acao == "exame_encerrado_operacionalmente"
        )
    ).scalars().all()
    assert len(total) == 1


def test_encerrar_com_outro_motivo_e_recusado_em_vez_de_sobrescrever(
    client, auth, caso
):
    """Duas decisões diferentes precisam das duas na trilha, não de uma só."""

    _encerrar(client, auth, caso["exame"]["public_code"])
    conflito = _encerrar(
        client,
        auth,
        caso["exame"]["public_code"],
        motivo="atendimento_cancelado",
        observacao="Motivo diferente do já gravado.",
    )
    assert conflito.status_code == 409
    assert conflito.json()["erro"]["codigo"] == (
        "exame_ja_encerrado_com_outro_motivo"
    )


def test_reabrir_exame_ja_aberto_responde_sem_alterar(client, auth, caso):
    resposta = client.post(
        f"/api/v1/laudos/exames/{caso['exame']['public_code']}/reabertura",
        json={"observacao": "Reabertura de um exame que já está na fila."},
        headers=auth("admin"),
    )
    assert resposta.status_code == 200
    assert resposta.json()["alterado"] is False


# =====================================================================
# 5. Recusas explícitas
# =====================================================================


def test_motivo_fora_do_catalogo_e_recusado(client, auth, caso):
    resposta = client.post(
        f"/api/v1/laudos/exames/{caso['exame']['public_code']}/encerramento",
        json={"motivo": "porque_sim", "observacao": "Motivo inventado."},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 422


def test_encerramento_sem_observacao_e_recusado(client, auth, caso):
    """Sem o caso concreto escrito, o motivo fechado não explica nada."""

    resposta = client.post(
        f"/api/v1/laudos/exames/{caso['exame']['public_code']}/encerramento",
        json={"motivo": "laudo_externo_ja_entregue", "observacao": ""},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 422


def test_exame_inexistente_devolve_404(client, auth):
    resposta = _encerrar(client, auth, "ESP-999999")
    assert resposta.status_code == 404


def test_catalogo_de_motivos_do_servidor_bate_com_o_schema(client, auth):
    """Duas listas separadas divergem em silêncio. Aqui elas não podem."""

    resposta = client.get(
        "/api/v1/laudos/exames/motivos-encerramento", headers=auth("operacional")
    )
    assert resposta.status_code == 200
    do_servidor = {m["chave"] for m in resposta.json()["motivos"]}
    assert do_servidor == set(CLOSURE_REASONS)
    assert do_servidor == set(ExamClosureReason.__args__)
    assert do_servidor == set(CLOSURE_SHORT_LABELS)


def test_banco_recusa_encerramento_pela_metade(db):
    """A constraint é a última linha: nem por SQL direto passa meio registro."""

    from sqlalchemy.exc import IntegrityError

    exame = SpirometryExam(
        public_code="ESP-900001",
        person_id="00000000-0000-4000-8000-0000000000ff",
        status="Realizado",
        encerramento_motivo="laudo_externo_ja_entregue",
        # sem data, sem autor, sem observação
    )
    db.add(exame)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# =====================================================================
# 6. O serviço isolado
# =====================================================================


def test_close_exam_e_idempotente_no_servico(db):
    exame = SpirometryExam(public_code="ESP-900002", person_id="x", status="Realizado")
    assert close_exam(
        db,
        exam=exame,
        motivo="laudo_externo_ja_entregue",
        observacao="Primeira chamada.",
        user_id="u1",
    ) is True
    assert close_exam(
        db,
        exam=exame,
        motivo="laudo_externo_ja_entregue",
        observacao="Segunda chamada.",
        user_id="u2",
    ) is False
    assert exame.encerrado_por_user_id == "u1"
    assert exame.encerramento_observacao == "Primeira chamada."


def test_reopen_exam_e_idempotente_no_servico(db):
    exame = SpirometryExam(public_code="ESP-900003", person_id="x", status="Realizado")
    assert reopen_exam(db, exam=exame) is False
    close_exam(
        db,
        exam=exame,
        motivo="atendimento_cancelado",
        observacao="Cancelado.",
        user_id="u1",
    )
    assert reopen_exam(db, exam=exame) is True
    assert reopen_exam(db, exam=exame) is False


def test_servico_recusa_observacao_em_branco(db):
    exame = SpirometryExam(public_code="ESP-900004", person_id="x", status="Realizado")
    with pytest.raises(ExamClosureError):
        close_exam(
            db,
            exam=exame,
            motivo="laudo_externo_ja_entregue",
            observacao="   ",
            user_id="u1",
        )


# =====================================================================
# 7. Honestidade dos rótulos de estado
# =====================================================================


def test_o_estado_de_pdf_devolvido_nao_afirma_ICP_Brasil():
    """O retorno de PDF assinado NÃO prova cadeia ICP-Brasil.

    `verify_signed_pdf` → `validate_pades` confere a assinatura CMS contra a
    chave pública do PRÓPRIO certificado do signatário. Ninguém verifica que
    esse certificado foi emitido por uma AC da ICP-Brasil, nem CRL, nem
    OCSP, nem carimbo de tempo. Os rótulos "Assinados com ICP-Brasil" e
    "Assinatura ICP-Brasil validada" afirmavam exatamente a parte que não é
    feita.

    O ramo do VIDaaS/IntegraICP (M25.7) é OUTRA coisa e continua podendo
    afirmar ICP-Brasil: lá quem assina é o HSM da AC. Ele segue indisponível
    em produção e a própria tela diz isso.
    """

    # Os comentários EXPLICAM o rótulo antigo de propósito — é onde a razão
    # da mudança fica registrada. Quem não pode citá-lo é o código.
    codigo = "\n".join(
        linha for linha in WORKFLOW_JS.splitlines()
        if not linha.lstrip().startswith("//")
    )
    assert '"Assinados com ICP-Brasil"' not in codigo
    assert '"Assinado com ICP-Brasil"' not in codigo
    assert '"Assinatura ICP-Brasil validada"' not in codigo
    assert "Assinado — assinatura conferida" in codigo
    assert "Assinatura digital conferida" in codigo
    # E não pode cair na fórmula que o guard do M24A barra em toda a tela,
    # sob pena de a liberação institucional voltar a parecer assinatura.
    assert not re.search(r"assinad[oa] digitalmente", codigo, re.IGNORECASE)

    # E o rótulo do estado `assinado` — o que a fila mostra — não pode
    # voltar a citar ICP-Brasil por descuido de um refactor.
    rotulos = codigo.split("function statusLabel(", 1)[1].split("\n  }", 1)[0]
    assert "ICP-Brasil" not in rotulos


def test_a_explicacao_do_estado_assinado_diz_o_que_nao_foi_conferido():
    assert "NÃO verifica a cadeia" in WORKFLOW_JS
    assert "revogação do certificado" in WORKFLOW_JS


# =====================================================================
# 8. A ajuda contextual
# =====================================================================


def test_ajuda_nao_depende_de_title_nem_so_de_hover():
    """`title` não abre por toque nem por teclado. O componente é real."""

    assert "function helpTip(" in WORKFLOW_JS
    assert 'role="tooltip"' in WORKFLOW_JS
    assert "aria-describedby" in WORKFLOW_JS
    assert 'aria-expanded="false"' in WORKFLOW_JS
    # Hover, foco, toque e Escape — as quatro entradas.
    assert '"mouseover"' in WORKFLOW_JS
    assert '"focusin"' in WORKFLOW_JS
    assert "data-help-toggle" in WORKFLOW_JS
    assert 'event.key !== "Escape"' in WORKFLOW_JS
    # E o CSS não pode abrir a bolha só por :hover — se abrisse, o iPhone
    # ficaria sem ajuda nenhuma.
    assert ".report-help-tip.is-open" in WORKFLOW_CSS
    assert ".report-help-bubble[hidden]" in WORKFLOW_CSS


def test_o_icone_de_ajuda_e_um_botao_focavel_com_alvo_de_toque():
    assert ".report-help-toggle {" in WORKFLOW_CSS
    assert ".report-help-toggle:focus-visible" in WORKFLOW_CSS
    bloco = WORKFLOW_CSS.split(".report-help-toggle {", 1)[1].split("}", 1)[0]
    assert "width: 28px" in bloco and "height: 28px" in bloco


def test_estado_aberto_nao_depende_apenas_de_cor():
    """Quem não distingue os tons ainda precisa ver qual "?" está ativo."""

    bloco = WORKFLOW_CSS.split(
        ".report-help-tip.is-open .report-help-toggle > span {", 1
    )[1].split("}", 1)[0]
    assert "background:" in bloco


def test_toque_no_ajuda_nao_dispara_a_acao_de_baixo(client):
    """O "?" fica dentro de linhas clicáveis; ele tem de interromper antes."""

    trecho = WORKFLOW_JS.split("function handleClick(", 1)[1][:3000]
    assert "data-help-toggle" in trecho
    assert "event.stopPropagation()" in trecho
    # E vem ANTES do tratamento da caixa de seleção do lote e de qualquer
    # outra ação: o "?" mora dentro de linhas e botões clicáveis.
    assert trecho.index("data-help-toggle") < trecho.index(
        "data-report-batch-pick"
    )


def test_todo_texto_de_ajuda_e_estatico_e_funcional():
    """Nenhuma bolha pode carregar dado de paciente, valor ou negócio.

    Os textos são literais dentro de um catálogo constante: não há
    interpolação de estado em nenhum deles, e é isso que torna o vazamento
    impossível por construção, e não por revisão de texto.
    """

    catalogo = WORKFLOW_JS.split("const HELP = {", 1)[1].split(
        "\n  };", 1
    )[0]
    assert "${" not in catalogo, "texto de ajuda com interpolação de estado"
    for proibido in ("patient", "full_name", "valor", "R$", "state."):
        assert proibido not in catalogo, proibido

    status_help = WORKFLOW_JS.split("const STATUS_HELP = {", 1)[1].split(
        "\n  };", 1
    )[0]
    assert "${" not in status_help
    assert "state." not in status_help


def test_cada_estado_do_filtro_tem_explicacao_propria():
    status_help = WORKFLOW_JS.split("const STATUS_HELP = {", 1)[1].split(
        "\n  };", 1
    )[0]
    for chave in (
        "atribuido",
        "em_elaboracao",
        "assinatura_pendente",
        "assinado",
        "liberado",
    ):
        assert f"{chave}:" in status_help, chave
    # A frase contextual é sempre visível, não escondida atrás do "?".
    assert 'class="report-status-explainer"' in WORKFLOW_JS
    assert ".report-status-explainer {" in WORKFLOW_CSS


def test_o_icone_de_ajuda_do_status_fica_fora_do_label():
    """Dentro do <label>, cada toque no "?" abriria o seletor junto."""

    trecho = WORKFLOW_JS.split('<div class="report-compact-field-wrap">', 1)[1]
    trecho = trecho.split("</div>", 1)[0]
    assert "</label>" in trecho
    assert trecho.index("</label>") < trecho.index('helpTip("status-filtro")')


def test_como_funciona_tem_os_seis_passos_e_nasce_recolhido_depois(client):
    assert "function renderHowItWorks(" in WORKFLOW_JS
    assert "HOW_IT_WORKS_STEPS" in WORKFLOW_JS
    passos = WORKFLOW_JS.split("const HOW_IT_WORKS_STEPS = [", 1)[1].split(
        "\n  ];", 1
    )[0]
    assert passos.count("[\"") == 6, "o fluxo conceitual tem exatamente 6 passos"
    # A médica NÃO envia o resultado ao paciente: ela responde pelo ato
    # médico e pela assinatura; a entrega é operação da empresa.
    assert "A SoproLife cuida da etapa administrativa de entrega" in WORKFLOW_JS
    assert "howItWorksSeen()" in WORKFLOW_JS


def test_como_funciona_nasce_em_largura_inteira_no_shell():
    """M25.21 — bloco novo no shell nunca volta a ser coluna."""

    shell = WORKFLOW_JS.split(
        "function renderPhysicianWorkspace() {", 1
    )[1].split("\n  }", 1)[0]
    assert "renderHowItWorks()" in shell
    assert shell.index("renderHowItWorks()") < shell.index(
        "report-physician-summary"
    )


def test_ajuda_cobre_os_controles_que_a_medica_precisa_entender():
    for chave in (
        "assinatura-externa",
        "assinatura-contador",
        "assinatura-selecionar-todos",
        "assinatura-baixar",
        "assinatura-enviar",
        "meus-laudos",
        "status-filtro",
        "exame-mir",
        "laudo-soprolife",
        "adendo",
        "documento-corretivo",
        "motivo-correcao",
    ):
        assert f'helpTip("{chave}")' in WORKFLOW_JS, chave


def test_a_ajuda_do_exame_mir_diz_que_ele_nunca_e_substituido():
    catalogo = WORKFLOW_JS.split("const HELP = {", 1)[1].split("\n  };", 1)[0]
    assert "nunca é alterado" in catalogo
    assert "Cria uma versão complementar" in catalogo  # adendo
    assert "não é apagado" in catalogo  # documento corretivo


# =====================================================================
# 9. A tela administrativa de históricos
# =====================================================================


def test_a_administracao_tem_onde_ver_e_reabrir():
    assert "function renderClosedExams(" in WORKFLOW_JS
    assert "/laudos/exames/encerrados" in WORKFLOW_JS
    assert "data-reopen-exam" in WORKFLOW_JS
    assert "data-closure-open" in WORKFLOW_JS
    assert ".report-closed-catalog {" in WORKFLOW_CSS
    # O botão de reabrir só aparece para quem consegue executá-lo.
    trecho = WORKFLOW_JS.split("function renderClosedExams(", 1)[1].split(
        "\n  }", 1
    )[0]
    assert 'can("admin")' in trecho


def test_a_tela_diz_que_nada_foi_apagado():
    """Sem esta frase, "encerrar" e "excluir" são a mesma coisa para quem usa."""

    assert "Nada é apagado" in WORKFLOW_JS
    assert "nada foi apagado" in WORKFLOW_JS.lower()


# =====================================================================
# 10. A CLI — dry-run e correção com evidência
# =====================================================================


def _cli(monkeypatch, engine, argv):
    """Roda a CLI contra o MESMO banco do teste, capturando o stdout."""

    import contextlib
    import json as _json

    from app import cli, db as db_module

    monkeypatch.setenv("M15_DATABASE_URL", str(engine.url))
    get_settings.cache_clear()
    # `app.db` guarda engine e sessionmaker em globais de MÓDULO. Sem zerar
    # os dois, a primeira chamada da CLI no processo prende o banco e todas
    # as seguintes conversam com o banco de outro teste — a falha aparece
    # como "exame não encontrado" num exame que existe.
    db_module._engine = None
    db_module._SessionLocal = None
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        codigo = cli.main(argv)
    saida = buffer.getvalue().strip()
    return codigo, (_json.loads(saida) if saida else None)


def test_cli_de_encerramento_e_dry_run_por_padrao(
    monkeypatch, engine, db, client, auth, users
):
    """O dry-run é o que produz a fotografia do lote ANTES de qualquer escrita."""

    _pessoa, exame = _criar_exame(client, auth, nome="TESTE APAGAR Lote Dry Run")
    db.rollback()

    codigo, saida = _cli(monkeypatch, engine, [
        "encerrar-exame-historico",
        "--exame", exame["public_code"],
        "--motivo", "laudo_externo_ja_entregue",
        "--observacao", "Laudo entregue por fora antes da plataforma.",
        "--usuario", users["operacional"].email,
    ])
    assert codigo == 0
    assert saida["dry_run"] is True
    assert saida["alvos"][0]["exam_code"] == exame["public_code"]
    assert saida["alvos"][0]["ja_encerrado"] is False
    # E o exame continua ABERTO: dry-run não escreve.
    db.expire_all()
    atual = db.execute(
        select(SpirometryExam).where(
            SpirometryExam.public_code == exame["public_code"]
        )
    ).scalar_one()
    assert not is_closed(atual)


def test_cli_de_encerramento_e_idempotente(
    monkeypatch, engine, db, client, auth, users
):
    _pessoa, exame = _criar_exame(client, auth, nome="TESTE APAGAR Lote Repetido")
    db.rollback()
    argv = [
        "encerrar-exame-historico",
        "--exame", exame["public_code"],
        "--motivo", "laudo_externo_ja_entregue",
        "--observacao", "Laudo entregue por fora antes da plataforma.",
        "--usuario", users["operacional"].email,
        "--executar",
    ]
    _codigo, primeira = _cli(monkeypatch, engine, argv)
    assert primeira["encerrados"] == [exame["public_code"]]
    _codigo, segunda = _cli(monkeypatch, engine, argv)
    assert segunda["encerrados"] == []
    assert segunda["ja_estavam_encerrados"] == [exame["public_code"]]


def test_cli_corrige_broncodilatador_so_quando_diverge(
    monkeypatch, engine, db, client, auth, users
):
    """`--para true` num exame que já é `true` não escreve nem audita.

    É a garantia de "corrigir BD já true não gera alteração desnecessária":
    reprocessar os cinco Pastore não pode carimbar auditoria em três exames
    que já estavam certos.
    """

    _pessoa, ja_true = _criar_exame(client, auth, nome="TESTE APAGAR BD Correto")
    nulo = SpirometryExam(
        public_code="ESP-900010",
        person_id=db.execute(select(Person.id)).scalars().first(),
        data_exame=None,
        status="Realizado",
        broncodilatador=None,
    )
    db.add(nulo)
    db.commit()
    db.rollback()

    _codigo, saida = _cli(monkeypatch, engine, [
        "corrigir-broncodilatador",
        "--exame", ja_true["public_code"],
        "--exame", "ESP-900010",
        "--para", "true",
        "--evidencia", "Extrato Pastore fornecido pelo gestor em 11/08/2026",
        "--usuario", users["operacional"].email,
        "--executar",
    ])
    assert saida["corrigidos"] == ["ESP-900010"]
    assert saida["ja_estavam_corretos"] == [ja_true["public_code"]]

    db.expire_all()
    assert db.execute(
        select(SpirometryExam.broncodilatador).where(
            SpirometryExam.public_code == "ESP-900010"
        )
    ).scalar_one() is True
    # UMA entrada de auditoria, e ela carrega a evidência.
    trilha = db.execute(
        select(AuditLog).where(
            AuditLog.acao
            == "espirometria.broncodilatador_corrigido_por_evidencia"
        )
    ).scalars().all()
    assert len(trilha) == 1
    assert "Extrato Pastore" in trilha[0].detalhes["motivo"]


def test_o_envelope_da_lista_de_encerrados_e_declarado():
    """M25.21 — envelope adivinhado já cegou duas filas por uma etapa inteira."""

    envelopes = WORKFLOW_JS.split("const PAYLOAD_ENVELOPES = {", 1)[1].split(
        "\n  };", 1
    )[0]
    assert 'closedExams: "itens"' in envelopes
    assert 'closureReasons: "motivos"' in envelopes
