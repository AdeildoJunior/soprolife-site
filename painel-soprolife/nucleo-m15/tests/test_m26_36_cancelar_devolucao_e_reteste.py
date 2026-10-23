"""M26.36 — desfazer um "Retornar para laudadora" dado por engano, e o
reteste de controle que não remunera produção.

O INCIDENTE QUE PEDIU ISTO (reproduzido aqui com dados sintéticos)

Um exame domiciliar foi laudado, liberado, assinado por fora e entregue; o
paciente tinha acesso ao PDF. Dias depois a família questionou o resultado e
um SEGUNDO exame foi feito, em outro espirômetro, só para conferência e sem
cobrar nada. Entre as duas coisas, o operador clicou em "Retornar para
laudadora" no PRIMEIRO exame sem perceber o que a ação faz: nasceu um laudo
corretivo vazio na fila da médica, o exame voltou a parecer pendência e o
acesso do paciente foi revogado.

O que este arquivo prova, nesta ordem:

 1. desfazer a devolução restaura o estado legítimo anterior;
 2. o PDF assinado anterior continua intacto;
 3. o PDF técnico (exame) continua intacto;
 4. o acesso do paciente ao resultado volta a funcionar de ponta a ponta;
 5. um segundo exame do MESMO paciente é independente da reversão;
 6. nenhuma linha fiscal muda;
 7. nenhum valor financeiro muda;
 8. reteste sem cobrança não cria receita;
 9. reteste sem cobrança não soma repasse médico quando é laudado;
10. o paciente fica com os DOIS laudos concluídos no histórico.

Mais as recusas: quem não é admin não aciona, laudo que não é corretiva não
aceita, e uma corretiva já começada pela médica nunca é removida.

Tudo sintético: pacientes "TESTE APAGAR", médicas de teste, CRMs inventados,
PDFs gerados na hora. Nenhum dado real de paciente.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import date
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.db import get_db
from app.models import (
    RESULTADO_DISPONIVEL,
    RESULTADO_REVOGADO,
    AuditLog,
    ExternalSignedDocument,
    FinancialEntry,
    FiscalDocument,
    FiscalDocumentItem,
    PatientResultAccess,
    ReportAssignment,
    ReportAssignmentEvent,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.services import patient_results as prs
from app.services.medical_transfers import (
    eligible_report_count,
    parse_competence,
)


_DATA_EXAME_SINTETICA = date(2026, 8, 14)


def _soltar(db) -> None:
    """Fecha a transação de leitura do teste antes de chamar a API.

    A suíte usa SQLite, e a sessão do teste divide o arquivo com a do app.
    Uma leitura aberta aqui faz a escrita do endpoint falhar com "database
    is locked" — erro do arranjo do teste, não do código sob teste (mesma
    razão do `_acesso` da M26.4).
    """

    db.commit()
    db.expire_all()


def _load(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M29D = _load("test_m25_29d_fluxo_conclusao_assinatura.py")
_M264 = _load("test_m26_4_portal_resultados.py")

_minimal_pdf = _M29D._minimal_pdf
_concluir = _M29D._concluir
_caso_em_elaboracao = _M29D._caso_em_elaboracao
_montar_caso = _M264._montar_caso
_acesso = _M264._acesso
_NASCIMENTO = _M264.NASCIMENTO


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    """Laudos em piloto + portal ligado: o incidente envolve as duas pontas."""

    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_REPORTS_VALIDATION_BASE_URL",
        "https://painel-teste.soprolife.local/validar",
    )
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-36-secret-de-teste-0123456789abcdef0123"
    )
    monkeypatch.setenv("M15_PORTAL_ENABLED", "true")
    monkeypatch.setenv(
        "M15_PORTAL_TOKEN_KEY",
        "m26-36-chave-que-deriva-o-link-do-paciente-9876543210abcdef",
    )
    monkeypatch.setenv(
        "M15_PORTAL_SESSION_SECRET",
        "m26-36-segredo-do-cookie-do-portal-publico-fedcba9876543210",
    )
    monkeypatch.setenv(
        "M15_PORTAL_PUBLIC_BASE_URL", "https://soprolife.com.br/resultados"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def portal(engine):
    """Superfície PÚBLICA do paciente — outro app, outro cookie."""

    from app.portal.main import create_portal_app

    app = create_portal_app()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def _sessao():
        sessao = SessionLocal()
        try:
            yield sessao
        finally:
            sessao.close()

    app.dependency_overrides[get_db] = _sessao
    with TestClient(app, base_url="https://resultados-api.teste.local") as c:
        yield c


@pytest.fixture()
def caso(client, auth, db):
    """O primeiro exame: laudado, assinado por fora, com acesso do paciente."""

    return _montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente M26.36", suffix="36"
    )


def _devolver(client, auth, document_id, db=None, reason="technical_document_correction"):
    if db is not None:
        _soltar(db)
    resposta = client.post(
        f"/api/v1/laudos/{document_id}/retornar-para-correcao",
        json={"reason_code": reason},
        headers=auth("admin"),
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _cancelar(client, auth, document_id, papel="admin", headers=None, db=None):
    if db is not None:
        _soltar(db)
    return client.post(
        f"/api/v1/laudos/{document_id}/cancelar-devolucao",
        headers=headers if headers is not None else auth(papel),
    )


def _fotografia_financeira(db) -> dict:
    """Todas as linhas de dinheiro e de fiscal, comparáveis byte a byte."""

    def linhas(modelo, campos):
        return sorted(
            tuple(getattr(linha, campo) for campo in campos)
            for linha in db.execute(select(modelo)).scalars()
        )

    _soltar(db)
    return {
        "financeiro": linhas(
            FinancialEntry,
            ("id", "public_code", "tipo", "valor", "status",
             "data_competencia", "spirometry_exam_id", "updated_at"),
        ),
        "fiscal_documentos": linhas(
            FiscalDocument,
            ("id", "spirometry_exam_id", "state", "eligibility", "kind",
             "recipient_person_id", "updated_at"),
        ),
        "fiscal_itens": linhas(
            FiscalDocumentItem,
            ("id", "document_id", "spirometry_exam_id", "financial_entry_id",
             "amount", "position"),
        ),
    }


def _nota_emitida_sintetica(db, exame_id, *, criado_por):
    """Uma NFS-e já emitida, montada à mão, para provar que não é tocada.

    Direto no banco de propósito: o que precisa ser provado aqui é que o
    cancelamento da devolução não escreve em `fiscal_*`, e não o caminho de
    emissão (que tem a sua própria suíte).
    """

    receita = FinancialEntry(
        tipo="receita",
        categoria="Espirometria",
        descricao="TESTE APAGAR receita do exame",
        valor="250.00",
        status="Recebido",
        data_competencia=_DATA_EXAME_SINTETICA,
        data_recebimento=_DATA_EXAME_SINTETICA,
        forma_pagamento="Pix",
        spirometry_exam_id=exame_id,
        public_code="LAN-T00001",
    )
    db.add(receita)
    db.flush()
    documento = FiscalDocument(
        spirometry_exam_id=exame_id,
        environment="production",
        state="issued",
        eligibility="eligible",
        blocking_reasons=[],
        created_by=criado_por,
        idempotency_key="m26-36-nota-sintetica",
        idempotency_fingerprint="m26-36-nota-sintetica",
        kind="single",
    )
    db.add(documento)
    db.flush()
    db.add(
        FiscalDocumentItem(
            document_id=documento.id,
            environment="production",
            spirometry_exam_id=exame_id,
            financial_entry_id=receita.id,
            amount="250.00",
            position=1,
        )
    )
    db.commit()
    return documento


# ======================================================================
# 1-3. desfazer restaura o estado anterior, sem tocar em nada assinado
# ======================================================================


def test_cancelar_devolucao_restaura_o_estado_legitimo_anterior(
    client, auth, db, caso
):
    document_id = caso["document_id"]
    antes = db.get(ReportDocument, document_id)
    db.refresh(antes)
    estado_antes = {
        "status": antes.status,
        "signature_status": antes.signature_status,
        "current_version_id": antes.current_version_id,
        "validation_code": antes.validation_code,
        "released_at": antes.released_at,
        "released_physician_profile_id": antes.released_physician_profile_id,
        "signature_downloaded_at": antes.signature_downloaded_at,
        "updated_at": antes.updated_at,
    }
    versoes_antes = sorted(
        (v.id, v.kind, v.sha256, v.version_number)
        for v in db.execute(
            select(ReportDocumentVersion).where(
                ReportDocumentVersion.report_document_id == document_id
            )
        ).scalars()
    )

    corretiva = _devolver(client, auth, document_id, db)
    corretiva_id = corretiva["id"]

    # O clique colocou um laudo em branco na fila: é isto que o painel
    # mostrava como "Aguardando laudo".
    fila = client.get("/api/v1/laudos", headers=auth("operacional"))
    assert fila.status_code == 200, fila.text
    assert corretiva_id in {linha["document_id"] for linha in fila.json()}

    resposta = _cancelar(client, auth, corretiva_id, db=db)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["corretiva_cancelada"]["document_id"] == corretiva_id
    assert corpo["corretiva_cancelada"]["public_code"] == corretiva["public_code"]

    # A corretiva CONTINUA EXISTINDO — evidência clínica é append-only
    # (M24C). O que muda é que ela está declarada cancelada, com quem e
    # quando, e a atribuição da médica foi encerrada.
    db.expire_all()
    cancelada = db.get(ReportDocument, corretiva_id)
    assert cancelada is not None
    assert cancelada.devolucao_cancelada_em is not None
    assert cancelada.devolucao_cancelada_por_user_id is not None
    assert cancelada.status == "atribuido"  # o status nem precisa mentir

    # A versão continua lá, intacta.
    versoes_corretiva = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == corretiva_id
        )
    ).scalars().all()
    assert len(versoes_corretiva) == 1

    # A atribuição foi ENCERRADA, não apagada, e o encerramento virou evento.
    atribuicoes = db.execute(
        select(ReportAssignment).where(
            ReportAssignment.report_document_id == corretiva_id
        )
    ).scalars().all()
    assert len(atribuicoes) == 1
    assert atribuicoes[0].active is False
    assert atribuicoes[0].ended_at is not None
    # O evento do nascimento da atribuição continua lá, intocado: é
    # append-only e `assignment_id` é único, um evento por atribuição.
    eventos = [
        e.event_type
        for e in db.execute(
            select(ReportAssignmentEvent).where(
                ReportAssignmentEvent.report_document_id == corretiva_id
            )
        ).scalars()
    ]
    assert eventos == ["corrective_assigned"]

    # O laudo legítimo está EXATAMENTE como estava — inclusive `updated_at`.
    depois = db.get(ReportDocument, document_id)
    db.refresh(depois)
    assert {
        "status": depois.status,
        "signature_status": depois.signature_status,
        "current_version_id": depois.current_version_id,
        "validation_code": depois.validation_code,
        "released_at": depois.released_at,
        "released_physician_profile_id": depois.released_physician_profile_id,
        "signature_downloaded_at": depois.signature_downloaded_at,
        "updated_at": depois.updated_at,
    } == estado_antes

    # Nenhuma versão do predecessor foi criada, apagada ou reescrita.
    versoes_depois = sorted(
        (v.id, v.kind, v.sha256, v.version_number)
        for v in db.execute(
            select(ReportDocumentVersion).where(
                ReportDocumentVersion.report_document_id == document_id
            )
        ).scalars()
    )
    assert versoes_depois == versoes_antes

    # E a fila não tem mais nenhum laudo PENDENTE: o único documento do
    # exame é o concluído de antes. (Em produção ele também já estava
    # entregue, e por isso saía da fila ativa por inteiro — ver
    # `incluir_superados` em `list_report_documents_operational`.)
    fila = client.get("/api/v1/laudos", headers=auth("operacional"))
    assert fila.status_code == 200, fila.text
    linhas = fila.json()
    assert [linha["document_id"] for linha in linhas] == [document_id]
    assert linhas[0]["status"] == "liberado"

    # E a corretiva cancelada continua ACESSÍVEL no histórico — some da
    # fila de trabalho, não do sistema.
    historico = client.get(
        "/api/v1/laudos?somente_superados=true", headers=auth("operacional")
    )
    assert historico.status_code == 200, historico.text
    assert corretiva_id in {l["document_id"] for l in historico.json()}

    # A trilha guarda os TRÊS fatos em ordem: devolveu, foi engano, reverteu.
    acoes = [
        linha.acao
        for linha in db.execute(
            select(AuditLog).order_by(AuditLog.ts_utc)
        ).scalars()
    ]
    assert "laudo_corretivo_aberto" in acoes
    assert "laudo_devolvido_para_correcao" in acoes
    evento = db.execute(
        select(AuditLog).where(AuditLog.acao == "laudo_devolucao_cancelada")
    ).scalar_one()
    assert evento.entidade_id == document_id
    assert evento.detalhes["corrective_document_id"] == corretiva_id
    assert evento.detalhes["report_code"] == corretiva["public_code"]
    assert evento.detalhes["status"] == "liberado"
    assert (
        evento.detalhes["motivo"]
        == "reversao_administrativa_de_devolucao_equivocada"
    )


def test_pdf_assinado_e_exame_tecnico_continuam_intactos(
    client, auth, db, caso
):
    document_id = caso["document_id"]
    assinado_antes = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == document_id
        )
    ).scalar_one()
    db.refresh(assinado_antes)
    retrato = (
        assinado_antes.id,
        assinado_antes.sha256,
        assinado_antes.size_bytes,
        assinado_antes.status,
        assinado_antes.report_document_version_id,
    )

    _soltar(db)
    tecnico_antes = client.get(
        f"/api/v1/laudos/{document_id}/exame-tecnico/conteudo",
        headers=auth("operacional"),
    )
    assert tecnico_antes.status_code == 200, tecnico_antes.text
    assinado_bytes_antes = client.get(
        f"/api/v1/laudos/{document_id}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert assinado_bytes_antes.status_code == 200, assinado_bytes_antes.text

    corretiva = _devolver(client, auth, document_id, db)
    assert _cancelar(client, auth, corretiva["id"], db=db).status_code == 200

    _soltar(db)
    assinado_depois = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == document_id
        )
    ).scalar_one()
    assert (
        assinado_depois.id,
        assinado_depois.sha256,
        assinado_depois.size_bytes,
        assinado_depois.status,
        assinado_depois.report_document_version_id,
    ) == retrato

    # Os dois PDFs continuam servíveis, byte a byte iguais.
    _soltar(db)
    tecnico_depois = client.get(
        f"/api/v1/laudos/{document_id}/exame-tecnico/conteudo",
        headers=auth("operacional"),
    )
    assert tecnico_depois.status_code == 200, tecnico_depois.text
    assert tecnico_depois.content == tecnico_antes.content
    assinado_depois_bytes = client.get(
        f"/api/v1/laudos/{document_id}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert assinado_depois_bytes.status_code == 200
    assert assinado_depois_bytes.content == assinado_bytes_antes.content


# ======================================================================
# 4. o resultado online do paciente volta a funcionar
# ======================================================================


def test_acesso_do_paciente_volta_a_funcionar_de_ponta_a_ponta(
    client, auth, db, portal, caso
):
    document_id = caso["document_id"]
    acesso_antes = _acesso(db, document_id)
    assert acesso_antes is not None
    geracao_antes = acesso_antes.generation

    corretiva = _devolver(client, auth, document_id, db)
    revogado = _acesso(db, document_id)
    assert revogado.status == RESULTADO_REVOGADO

    assert _cancelar(client, auth, corretiva["id"], db=db).status_code == 200

    restaurado = _acesso(db, document_id)
    assert restaurado.id == acesso_antes.id  # a MESMA linha, nada recriado
    assert restaurado.status == RESULTADO_DISPONIVEL
    assert restaurado.revoked_at is None
    assert restaurado.revoked_motivo is None
    assert restaurado.revoked_by_user_id is None
    # Link NOVO: um token revogado nunca ressuscita (M26.4).
    assert restaurado.generation == geracao_antes + 1

    token = prs.derive_token(restaurado.id, restaurado.generation)
    entrada = portal.post(
        "/p/v1/acesso", json={"token": token, "nascimento": _NASCIMENTO}
    )
    assert entrada.status_code == 200, entrada.text


def test_revogacao_deliberada_nao_e_desfeita_de_carona(
    client, auth, db, caso
):
    """Se alguém revogou o acesso POR OUTRO MOTIVO, cancelar a devolução
    não o traz de volta — aquilo foi uma decisão, não um engano."""

    document_id = caso["document_id"]
    corretiva = _devolver(client, auth, document_id, db)

    acesso = _acesso(db, document_id)
    acesso.revoked_motivo = "Paciente pediu para fechar o link."
    db.add(acesso)
    db.commit()

    resposta = _cancelar(client, auth, corretiva["id"], db=db)
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["acesso_resultado_restaurado"] is None
    assert _acesso(db, document_id).status == RESULTADO_REVOGADO


# ======================================================================
# 5-7. segundo exame independente, fiscal e financeiro intocados
# ======================================================================


def test_segundo_exame_e_fiscal_e_financeiro_nao_sao_tocados(
    client, auth, db, caso
):
    document_id = caso["document_id"]
    pessoa_id = caso["pessoa"]["id"]
    exame_id = db.get(ReportDocument, document_id).spirometry_exam_id
    admin = db.execute(
        select(User).where(User.email == "admin@teste.local")
    ).scalar_one()
    _nota_emitida_sintetica(db, exame_id, criado_por=admin.id)
    _soltar(db)

    # O RETESTE: segundo exame do mesmo paciente, sem bloco financeiro.
    reteste = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": pessoa_id,
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-08-16",
                "status": "Realizado",
                "broncodilatador": False,
            },
        },
        headers=auth("operacional"),
    )
    assert reteste.status_code == 201, reteste.text
    exame_reteste = reteste.json()["espirometria"]
    laudo_reteste = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame_reteste["public_code"],
            "physician_profile_id": caso["profile"]["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-teste-m2636",
        },
        files={"file": ("TESTE-APAGAR-reteste.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert laudo_reteste.status_code == 201, laudo_reteste.text
    laudo_reteste = laudo_reteste.json()

    antes = _fotografia_financeira(db)

    corretiva = _devolver(client, auth, document_id, db)
    assert _cancelar(client, auth, corretiva["id"], db=db).status_code == 200

    # Fiscal e financeiro: ZERO escritas.
    assert _fotografia_financeira(db) == antes

    # O reteste é independente: continua aguardando laudo, com o seu PDF.
    db.expire_all()
    ainda_pendente = db.get(ReportDocument, laudo_reteste["id"])
    assert ainda_pendente is not None
    assert ainda_pendente.status == "atribuido"
    assert ainda_pendente.corrects_document_id is None
    _soltar(db)
    tecnico = client.get(
        f"/api/v1/laudos/{laudo_reteste['id']}/exame-tecnico/conteudo",
        headers=auth("operacional"),
    )
    assert tecnico.status_code == 200, tecnico.text

    # Nenhum exame e nenhum paciente desapareceu.
    exames = {e.public_code for e in db.execute(select(SpirometryExam)).scalars()}
    assert exame_reteste["public_code"] in exames
    assert len(exames) == 2


# ======================================================================
# 8-10. o reteste sem cobrança: sem receita, sem repasse, no histórico
# ======================================================================


def _montar_reteste(client, auth, db, caso, *, sem_cobranca: bool):
    _soltar(db)
    pessoa_id = caso["pessoa"]["id"]
    criado = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": pessoa_id,
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-08-16",
                "status": "Realizado",
                "broncodilatador": False,
            },
        },
        headers=auth("operacional"),
    )
    assert criado.status_code == 201, criado.text
    exame = criado.json()["espirometria"]

    if sem_cobranca:
        marcado = client.patch(
            f"/api/v1/espirometrias/{exame['id']}",
            json={"reteste_sem_cobranca": True},
            headers=auth("operacional"),
        )
        assert marcado.status_code == 200, marcado.text
        assert marcado.json()["reteste_sem_cobranca"] is True

    documento = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame["public_code"],
            "physician_profile_id": caso["profile"]["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-teste-m2636",
        },
        files={"file": ("TESTE-APAGAR-reteste.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert documento.status_code == 201, documento.text
    documento = documento.json()

    previa = client.post(
        f"/api/v1/laudos/{documento['id']}/laudo/previa",
        json={
            "conclusion_code": "NORMAL",
            "bronchodilator_code": "BD_NAO_REALIZADO",
        },
        headers=caso["doctor_auth"],
    )
    assert previa.status_code == 200, previa.text
    liberado = _concluir(
        client,
        {
            "document_id": documento["id"],
            "doctor_auth": caso["doctor_auth"],
            "previa": previa.json(),
        },
    )
    assert liberado["status"] == "liberado"
    return exame, documento


def test_reteste_sem_cobranca_nao_cria_receita_nem_soma_repasse(
    client, auth, db, caso
):
    profile_id = caso["profile"]["id"]
    # A competência do repasse é o mês LOCAL da LIBERAÇÃO (que é agora),
    # nunca a data do exame — ver o docstring de medical_transfers.
    competencia = parse_competence(None)

    # O primeiro laudo já conta como produção da competência.
    db.expire_all()
    contagem_inicial = eligible_report_count(db, profile_id, competencia)
    assert contagem_inicial == 1

    exame, _documento = _montar_reteste(client, auth, db, caso, sem_cobranca=True)

    # 8. Nenhuma receita nasceu do reteste — nem no cadastro, nem ao laudar.
    db.expire_all()
    receitas_do_reteste = db.execute(
        select(FinancialEntry).where(
            FinancialEntry.spirometry_exam_id == exame["id"]
        )
    ).scalars().all()
    assert receitas_do_reteste == []

    # 9. E ele não soma repasse, mesmo liberado na mesma competência.
    assert eligible_report_count(db, profile_id, competencia) == contagem_inicial

    # O detalhamento mostrado na tela de repasses conta o MESMO número.
    painel = client.get(
        "/api/v1/financeiro/repasses-medicos"
        f"?competencia={competencia.strftime('%Y-%m')}",
        headers=auth("admin"),
    )
    assert painel.status_code == 200, painel.text
    linhas = [
        linha
        for linha in painel.json()["medicas"]
        if linha["physician_profile_id"] == profile_id
    ]
    assert linhas, painel.text
    assert linhas[0]["quantidade_laudos_elegiveis"] == contagem_inicial


def test_sem_a_marca_o_reteste_contaria_como_repasse_novo(
    client, auth, db, caso
):
    """A contraprova: a exclusão vem da marca explícita, não do acaso."""

    profile_id = caso["profile"]["id"]
    competencia = parse_competence(None)
    db.expire_all()
    antes = eligible_report_count(db, profile_id, competencia)

    _montar_reteste(client, auth, db, caso, sem_cobranca=False)

    db.expire_all()
    assert eligible_report_count(db, profile_id, competencia) == antes + 1


def test_paciente_fica_com_os_dois_laudos_concluidos_no_historico(
    client, auth, db, caso
):
    """10. Depois de a médica laudar o reteste, os DOIS exames continuam no
    prontuário — o original entregue e o reteste concluído."""

    document_id = caso["document_id"]
    corretiva = _devolver(client, auth, document_id, db)
    assert _cancelar(client, auth, corretiva["id"], db=db).status_code == 200

    exame_reteste, documento_reteste = _montar_reteste(
        client, auth, db, caso, sem_cobranca=True
    )

    db.expire_all()
    pessoa_id = caso["pessoa"]["id"]
    exames = db.execute(
        select(SpirometryExam).where(SpirometryExam.person_id == pessoa_id)
    ).scalars().all()
    assert len(exames) == 2

    documentos = db.execute(
        select(ReportDocument)
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
        .where(SpirometryExam.person_id == pessoa_id)
    ).scalars().all()
    # Três linhas: os DOIS laudos de verdade e a corretiva cancelada, que
    # continua guardada como evidência do engano — nunca apagada.
    assert len(documentos) == 3
    vigentes = [d for d in documentos if d.devolucao_cancelada_em is None]
    canceladas = [d for d in documentos if d.devolucao_cancelada_em is not None]
    assert len(canceladas) == 1
    assert len(vigentes) == 2
    assert {doc.status for doc in vigentes} == {"liberado"}
    assert {doc.corrects_document_id for doc in vigentes} == {None}

    # E o reteste continua marcado como sem cobrança.
    reteste = db.get(SpirometryExam, exame_reteste["id"])
    assert reteste.reteste_sem_cobranca is True
    assert documento_reteste["spirometry_exam_id"] == reteste.id


# ======================================================================
# recusas
# ======================================================================


def test_medica_e_operacional_nao_cancelam_devolucao(client, auth, caso):
    corretiva = _devolver(client, auth, caso["document_id"])
    assert (
        _cancelar(
            client, auth, corretiva["id"], headers=caso["doctor_auth"]
        ).status_code
        == 403
    )
    assert _cancelar(client, auth, corretiva["id"], "operacional").status_code == 403


def test_laudo_que_nao_e_corretiva_nao_aceita_cancelamento(client, auth, caso):
    resposta = _cancelar(client, auth, caso["document_id"])
    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["erro"]["codigo"] == "laudo_nao_e_corretiva"


def test_corretiva_ja_comecada_pela_medica_nunca_e_removida(
    client, auth, db, caso
):
    corretiva = _devolver(client, auth, caso["document_id"])
    previa = client.post(
        f"/api/v1/laudos/{corretiva['id']}/laudo/previa",
        json={
            "conclusion_code": "NORMAL",
            "bronchodilator_code": "BD_NAO_REALIZADO",
        },
        headers=caso["doctor_auth"],
    )
    assert previa.status_code == 200, previa.text

    resposta = _cancelar(client, auth, corretiva["id"])
    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["erro"]["codigo"] in {
        "laudo_corretivo_em_andamento",
        "laudo_corretivo_com_conteudo",
    }
    db.expire_all()
    assert db.get(ReportDocument, corretiva["id"]) is not None


def test_segundo_clique_no_cancelamento_e_recusado(client, auth, caso):
    """Dois cliques no mesmo botão: o segundo diz que já foi cancelado."""

    corretiva = _devolver(client, auth, caso["document_id"])
    assert _cancelar(client, auth, corretiva["id"]).status_code == 200
    segunda = _cancelar(client, auth, corretiva["id"])
    assert segunda.status_code == 409, segunda.text
    assert segunda.json()["erro"]["codigo"] == "devolucao_ja_cancelada"


def test_depois_de_cancelar_uma_correcao_legitima_volta_a_ser_possivel(
    client, auth, caso
):
    """A trava de uma corretiva por laudo não fica presa ao engano.

    É o índice único PARCIAL da M26.36b que permite isto: a corretiva
    cancelada continua apontando para o predecessor, mas fora da trava."""

    primeira = _devolver(client, auth, caso["document_id"])
    assert _cancelar(client, auth, primeira["id"]).status_code == 200
    segunda = client.post(
        f"/api/v1/laudos/{caso['document_id']}/retornar-para-correcao",
        json={"reason_code": "clinical_correction"},
        headers=auth("admin"),
    )
    assert segunda.status_code == 201, segunda.text
    assert segunda.json()["id"] != primeira["id"]


def test_os_dois_arquivos_pdf_continuam_no_disco(
    client, auth, db, caso, tmp_path
):
    """A cópia técnica da corretiva NÃO é apagada: a linha da versão
    continua no banco (append-only), logo o arquivo tem de continuar lá —
    a reconciliação de laudos acusaria `database_row_missing_file`."""

    document_id = caso["document_id"]
    corretiva = _devolver(client, auth, document_id, db)

    _soltar(db)
    versao_corretiva = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == corretiva["id"]
        )
    ).scalar_one()
    versao_original = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == document_id,
            ReportDocumentVersion.kind == "original",
        )
    ).scalar_one()
    raiz = tmp_path / "reports"
    copia = raiz / versao_corretiva.storage_path
    original = raiz / versao_original.storage_path
    assert copia != original
    assert copia.exists() and original.exists()
    assert versao_corretiva.sha256 == versao_original.sha256

    resposta = _cancelar(client, auth, corretiva["id"], db=db)
    assert resposta.status_code == 200, resposta.text

    assert copia.exists()
    assert original.exists()
