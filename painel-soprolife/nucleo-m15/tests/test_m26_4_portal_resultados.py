"""M26.4 — o portal de resultados do paciente, provado ponto a ponto.

Vinte e cinco perguntas, e nenhuma delas é sobre "o código roda". São sobre
o que o sistema **recusa**: um token chutado, uma data de nascimento errada,
uma prévia assinada, um PDF devolvido sem assinar, um cookie do painel
administrativo usado como atalho, o laudo de outra pessoa.

O caso feliz aparece uma vez, no fim, como prova de ponta a ponta: a médica
devolve o PDF assinado, o acesso nasce sozinho, o QR nasce, a mensagem
nasce, o paciente informa a data de nascimento e baixa exatamente os dois
arquivos que são dele — com SHA conferido nos dois sentidos.

Tudo sintético: pacientes "TESTE APAGAR", médicas de teste, CRMs inventados,
telefones no prefixo 0000 (não atribuído no Brasil, nunca discável) e PDFs
gerados na hora.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import pathlib
import re
import subprocess

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db
from app.models import (
    ASSINADO_ACEITO,
    ASSINADO_RECUSADO,
    RESULTADO_ACESSADO,
    RESULTADO_DISPONIVEL,
    RESULTADO_ENVIADO,
    RESULTADO_MAX_TENTATIVAS,
    RESULTADO_REVOGADO,
    AuditLog,
    ExternalSignedDocument,
    PatientResultAccess,
    PatientResultSession,
    Person,
    ReportDocumentVersion,
)
from app.services import patient_results as prs
from app.services import qrcode_svg as qr

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
REPO_ROOT = PANEL_ROOT.parent
PORTAL_HTML = REPO_ROOT / "resultados" / "index.html"
ROBOTS = REPO_ROOT / "robots.txt"
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()


def _carregar(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M29D = _carregar("test_m25_29d_fluxo_conclusao_assinatura.py")
_assinar_por_fora = _M29D._assinar_por_fora
_caso_em_elaboracao = _M29D._caso_em_elaboracao
_concluir = _M29D._concluir
_minimal_pdf = _M29D._minimal_pdf

NASCIMENTO = "1978-04-09"
NASCIMENTO_BR = "09/04/1978"
# Prefixo local 0000 não é atribuído no Brasil: sintético e não discável.
TELEFONE = "(21) 0000-9042"


# ------------------------------------------------------------- ambiente


@pytest.fixture(autouse=True)
def portal_ligado(monkeypatch, tmp_path):
    """Laudos em piloto + portal ligado, com os DOIS segredos separados."""

    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_REPORTS_VALIDATION_BASE_URL",
        "https://painel-teste.soprolife.local/validar",
    )
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-4-painel-administrativo-secret-de-teste-0123456789"
    )
    monkeypatch.setenv("M15_PORTAL_ENABLED", "true")
    monkeypatch.setenv(
        "M15_PORTAL_TOKEN_KEY",
        "m26-4-chave-que-deriva-o-link-do-paciente-9876543210abcdef",
    )
    monkeypatch.setenv(
        "M15_PORTAL_SESSION_SECRET",
        "m26-4-segredo-do-cookie-do-portal-publico-fedcba9876543210",
    )
    monkeypatch.setenv(
        "M15_PORTAL_PUBLIC_BASE_URL", "https://soprolife.com.br/resultados"
    )
    get_settings.cache_clear()
    from app.portal.security import limitador

    limitador.limpar()
    yield
    limitador.limpar()
    get_settings.cache_clear()


@pytest.fixture()
def portal(engine):
    """Cliente da superfície PÚBLICA. Outro app, outro cookie, outro segredo."""

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


# ------------------------------------------------------------ fixtures


def _nascer(client, auth, pessoa_id, *, nascimento=NASCIMENTO, telefone=TELEFONE):
    corpo = {"data_nascimento": nascimento}
    resposta = client.patch(
        f"/api/v1/pessoas/{pessoa_id}", json=corpo, headers=auth("operacional")
    )
    assert resposta.status_code == 200, resposta.text
    if telefone:
        contato = client.post(
            f"/api/v1/pessoas/{pessoa_id}/contatos",
            json={"tipo": "whatsapp", "valor": telefone, "principal": True},
            headers=auth("operacional"),
        )
        assert contato.status_code in (200, 201), contato.text


def _assinar_e_devolver(client, caso) -> bytes:
    """A médica conclui, baixa o final, assina por fora e devolve."""

    _concluir(client, caso)
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert baixado.status_code == 200, baixado.text
    assinado = _assinar_por_fora(baixado.content)
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("TESTE APAGAR - assinado.pdf", assinado, "application/pdf")},
        headers=caso["doctor_auth"],
    )
    assert envio.status_code == 200, envio.text
    return assinado


def _montar_caso(client, auth, db, *, nome, suffix, nascimento=NASCIMENTO,
                 telefone=TELEFONE):
    caso = _caso_em_elaboracao(client, auth, db, nome_paciente=nome, suffix=suffix)
    _nascer(client, auth, caso["pessoa"]["id"], nascimento=nascimento,
            telefone=telefone)
    caso["assinado_bytes"] = _assinar_e_devolver(client, caso)
    return caso


@pytest.fixture()
def caso_a(client, auth, db):
    return _montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente Alfa", suffix="41"
    )


@pytest.fixture()
def caso_b(client, auth, db):
    return _montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente Beta", suffix="42",
        nascimento="1965-11-23",
    )


def _acesso(db, document_id) -> PatientResultAccess | None:
    """Lê o acesso e SOLTA o banco antes de devolver.

    A suíte usa SQLite, e a sessão do teste divide o arquivo com a do painel
    e a do portal. Uma transação de leitura aberta aqui trava a escrita do
    portal com "database is locked" — um erro que parece do código sob teste
    e é do arranjo do teste. `expire_on_commit=False` deixa os atributos
    carregados depois do commit, então o objeto continua utilizável.
    """

    db.commit()
    db.expire_all()
    acesso = db.execute(
        select(PatientResultAccess).where(
            PatientResultAccess.report_document_id == document_id
        )
    ).scalar_one_or_none()
    db.commit()
    return acesso


def _token(db, document_id) -> str:
    acesso = _acesso(db, document_id)
    assert acesso is not None
    return prs.derive_token(acesso.id, acesso.generation)


def _entrar(portal_client, token, nascimento=NASCIMENTO):
    return portal_client.post(
        "/p/v1/acesso", json={"token": token, "nascimento": nascimento}
    )


# ======================================================================
# 1-4 — o gatilho: quando nasce, e sobretudo quando NÃO nasce
# ======================================================================


def test_01_recebido_assinado_cria_acesso(client, auth, db, caso_a):
    """O caminho normal. A médica não fez nada além do que já fazia."""

    assinado = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == caso_a["document_id"]
        )
    ).scalar_one()
    assert assinado.status == ASSINADO_ACEITO

    acesso = _acesso(db, caso_a["document_id"])
    assert acesso is not None
    assert acesso.status == RESULTADO_DISPONIVEL
    # Amarrado à versão ASSINADA vigente, e não à versão de origem.
    assert acesso.signed_document_id == assinado.id
    assert acesso.report_document_version_id == assinado.report_document_version_id
    assert acesso.report_document_version_id != assinado.source_version_id
    assert acesso.person_id == caso_a["pessoa"]["id"]
    # Nasceu do gatilho, e não de um clique: o campo diz isso.
    assert acesso.created_by_user_id is not None  # a médica que enviou
    assert acesso.first_access_at is None and acesso.sent_at is None


def test_02_recusado_nao_cria_acesso(client, auth, db):
    """Guarda documental recusa ⇒ nenhum acesso, nenhum link, nada."""

    caso = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Recusa", suffix="43"
    )
    _nascer(client, auth, caso["pessoa"]["id"])
    _concluir(client, caso)
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    # Devolvido IDÊNTICO ao final: nenhuma estrutura de assinatura dentro.
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("devolvido.pdf", baixado.content, "application/pdf")},
        headers=caso["doctor_auth"],
    )
    assert envio.status_code == 200, envio.text
    assert envio.json()["aceitos"] == 0
    assinado = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == caso["document_id"]
        )
    ).scalar_one_or_none()
    assert assinado is None or assinado.status == ASSINADO_RECUSADO
    assert _acesso(db, caso["document_id"]) is None


def test_03_previa_assinada_nao_cria_acesso(client, auth, db):
    """Prévia assinada é incidente de fluxo — e nunca vira link de paciente."""

    caso = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Previa", suffix="44"
    )
    _nascer(client, auth, caso["pessoa"]["id"])
    previa = client.get(
        f"/api/v1/laudos/{caso['document_id']}/versoes/"
        f"{caso['previa']['preview_version_id']}/conteudo?modo=download",
        headers=caso["doctor_auth"],
    )
    assert previa.status_code == 200, previa.text
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": (
                "previa-assinada.pdf",
                _assinar_por_fora(previa.content),
                "application/pdf",
            )
        },
        headers=caso["doctor_auth"],
    )
    assert envio.status_code == 200, envio.text
    assert envio.json()["aceitos"] == 0
    assert _acesso(db, caso["document_id"]) is None


def test_04_pdf_sem_assinatura_nao_cria_acesso(client, auth, db):
    """Sem `/ByteRange` e `/Sig` não existe entrega, mesmo sendo o PDF certo.

    É a diferença entre "o arquivo é o laudo final" e "o arquivo foi
    assinado". A M25.29H pagou caro para separar as duas coisas.
    """

    caso = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Sem Assinatura", suffix="45"
    )
    _nascer(client, auth, caso["pessoa"]["id"])
    _concluir(client, caso)
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    # Reescreve o PDF (assinador que reconstrói) SEM anexar assinatura.
    escritor = PdfWriter()
    for pagina in PdfReader(io.BytesIO(baixado.content)).pages:
        escritor.add_page(pagina)
    saida = io.BytesIO()
    escritor.write(saida)
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("reescrito.pdf", saida.getvalue(), "application/pdf")},
        headers=caso["doctor_auth"],
    )
    assert envio.status_code == 200, envio.text
    assert envio.json()["aceitos"] == 0
    assert _acesso(db, caso["document_id"]) is None


# ======================================================================
# 5 — o token
# ======================================================================


def test_05_token_tem_entropia_suficiente_e_nao_e_sequencial(db, caso_a):
    acesso = _acesso(db, caso_a["document_id"])
    token = prs.derive_token(acesso.id, acesso.generation)

    # base64url sem padding de 32 bytes = 43 caracteres = 256 bits.
    assert len(token) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    import base64

    bruto = base64.urlsafe_b64decode(token + "=")
    assert len(bruto) == 32

    # Nada de sequencial: ids vizinhos e gerações vizinhas produzem tokens
    # sem relação visível. A distância de Hamming fica perto da metade dos
    # bits, que é o esperado de uma saída pseudoaleatória.
    outro = prs.derive_token(acesso.id, acesso.generation + 1)
    assert outro != token
    a = base64.urlsafe_b64decode(token + "=")
    b = base64.urlsafe_b64decode(outro + "=")
    distancia = sum(bin(x ^ y).count("1") for x, y in zip(a, b))
    assert 80 < distancia < 176  # 256 bits: metade ± folga generosa

    # O banco NÃO guarda o segredo, só o hash.
    assert acesso.token_sha256 == hashlib.sha256(token.encode()).hexdigest()
    assert token not in acesso.token_sha256


def test_05b_token_nunca_aparece_na_trilha_de_auditoria(db, caso_a):
    token = _token(db, caso_a["document_id"])
    linhas = db.execute(select(AuditLog)).scalars().all()
    bruto = "".join(str(linha.detalhes) for linha in linhas)
    assert token not in bruto
    assert "#t=" not in bruto
    assert "soprolife.com.br/resultados" not in bruto


# ======================================================================
# 6-9 — os dois fatores, e o que o portal NÃO conta
# ======================================================================


def test_06_token_errado_nao_revela_paciente(portal, caso_a):
    resposta = _entrar(portal, "T" * 43)
    assert resposta.status_code == 401
    corpo = resposta.text
    assert "Alfa" not in corpo and "TESTE APAGAR" not in corpo
    assert resposta.json()["erro"]["codigo"] == "acesso_invalido"


def test_07_nascimento_errado_nao_revela_paciente(portal, db, caso_a):
    token = _token(db, caso_a["document_id"])
    resposta = _entrar(portal, token, nascimento="1990-01-01")
    assert resposta.status_code == 401
    assert "Alfa" not in resposta.text
    # A MESMA mensagem do token inexistente: nada aqui funciona como oráculo.
    inexistente = _entrar(portal, "T" * 43)
    assert resposta.json()["erro"]["mensagem"] == inexistente.json()["erro"]["mensagem"]
    assert resposta.json()["erro"]["codigo"] == inexistente.json()["erro"]["codigo"]


def test_08_rate_limiting_por_acesso_e_por_origem(portal, db, caso_a):
    token = _token(db, caso_a["document_id"])
    for _ in range(RESULTADO_MAX_TENTATIVAS):
        assert _entrar(portal, token, nascimento="1990-01-01").status_code == 401
    # Estourou a janela: agora nem a data CERTA passa, e a recusa é outra.
    bloqueado = _entrar(portal, token)
    assert bloqueado.status_code == 429
    assert bloqueado.json()["erro"]["codigo"] == "muitas_tentativas"

    acesso = _acesso(db, caso_a["document_id"])
    assert acesso.locked_until is not None
    # O contador vive no BANCO: reiniciar o processo público não zera nada.
    assert prs.is_locked(acesso)


def test_08b_varredura_de_token_inexistente_tambem_e_freada(portal):
    from app.portal.security import limitador

    codigos = set()
    for i in range(limitador.maximo + 3):
        codigos.add(_entrar(portal, f"{i:043d}").status_code)
    assert 429 in codigos


def test_09_nascimento_correto_autentica(portal, db, caso_a):
    token = _token(db, caso_a["document_id"])
    resposta = _entrar(portal, token)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["paciente"]["nome"] == "TESTE APAGAR Paciente Alfa"
    assert corpo["exame"]["tipo"] == "Espirometria"
    assert corpo["status"] == "Resultado disponível"
    assert "converse com seu médico" in corpo["orientacao"]
    assert {d["chave"] for d in corpo["documentos"]} == {
        "laudo-assinado", "exame-tecnico"
    }
    # Nenhum identificador interno atravessa a fronteira.
    texto = resposta.text
    for proibido in ("LAU-", "ESP-", "PES-", "sha256", "document_id", "person_id"):
        assert proibido not in texto

    acesso = _acesso(db, caso_a["document_id"])
    assert acesso.status == RESULTADO_ACESSADO
    assert acesso.first_access_at is not None
    assert acesso.failed_attempts == 0


# ======================================================================
# 10-13 — os documentos, e o isolamento entre pacientes
# ======================================================================


def test_10_laudo_retorna_o_sha_do_assinado_correto(portal, db, caso_a):
    _entrar(portal, _token(db, caso_a["document_id"]))
    baixado = portal.get("/p/v1/documentos/laudo-assinado")
    assert baixado.status_code == 200, baixado.text
    assert baixado.headers["content-type"] == "application/pdf"

    assinado = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == caso_a["document_id"]
        )
    ).scalar_one()
    assert hashlib.sha256(baixado.content).hexdigest() == assinado.sha256
    # E é o arquivo que a médica devolveu, byte a byte.
    assert baixado.content == caso_a["assinado_bytes"]


def test_11_tecnico_retorna_o_sha_do_exame_correto(portal, db, caso_a):
    _entrar(portal, _token(db, caso_a["document_id"]))
    baixado = portal.get("/p/v1/documentos/exame-tecnico")
    assert baixado.status_code == 200, baixado.text
    original = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == caso_a["document_id"],
            ReportDocumentVersion.kind == "original",
        )
    ).scalar_one()
    assert hashlib.sha256(baixado.content).hexdigest() == original.sha256
    assert baixado.content == caso_a["mir_bytes"]


def test_12_paciente_a_nunca_baixa_documento_de_b(portal, db, caso_a, caso_b):
    _entrar(portal, _token(db, caso_a["document_id"]))
    laudo_a = portal.get("/p/v1/documentos/laudo-assinado").content
    tecnico_a = portal.get("/p/v1/documentos/exame-tecnico").content

    # O laudo assinado carrega nome e códigos do paciente dentro do PDF, e
    # por isso os bytes de A e B são naturalmente diferentes.
    assert laudo_a == caso_a["assinado_bytes"]
    assert laudo_a != caso_b["assinado_bytes"]

    # O PDF técnico sintético é o MESMO arquivo em branco nos dois casos, de
    # propósito: comparar bytes aqui não provaria nada. O que se prova é o
    # VÍNCULO — a versão servida pertence ao laudo de A, e não é a linha de
    # B — que é exatamente o que impede a troca de arquivo entre pacientes.
    acesso_a = _acesso(db, caso_a["document_id"])
    acesso_b = _acesso(db, caso_b["document_id"])
    versao_a = prs.technical_version(db, acesso_a)
    versao_b = prs.technical_version(db, acesso_b)
    id_a, id_b = versao_a.id, versao_b.id
    doc_a, sha_a = versao_a.report_document_id, versao_a.sha256
    db.commit()  # solta o SQLite antes de voltar a escrever pelo portal
    assert id_a != id_b
    assert doc_a == caso_a["document_id"]
    assert hashlib.sha256(tecnico_a).hexdigest() == sha_a

    # O nome do arquivo também é o de A, e nunca o de B.
    cabecalho = portal.get("/p/v1/documentos/laudo-assinado").headers[
        "content-disposition"
    ]
    assert "Beta" not in cabecalho


def test_13_duas_pessoas_simultaneas_nao_cruzam_documento(
    engine, db, caso_a, caso_b
):
    """Dois navegadores ao mesmo tempo, alternando chamadas."""

    from app.portal.main import create_portal_app

    def _novo_cliente():
        app = create_portal_app()
        Sessao = sessionmaker(bind=engine, expire_on_commit=False)

        def _sessao():
            s = Sessao()
            try:
                yield s
            finally:
                s.close()

        app.dependency_overrides[get_db] = _sessao
        return TestClient(app, base_url="https://resultados-api.teste.local")

    with _novo_cliente() as pa, _novo_cliente() as pb:
        assert _entrar(pa, _token(db, caso_a["document_id"])).status_code == 200
        assert _entrar(
            pb, _token(db, caso_b["document_id"]), nascimento="1965-11-23"
        ).status_code == 200
        # Intercalado de propósito: a=1, b=1, a=2, b=2.
        a1 = pa.get("/p/v1/documentos/laudo-assinado").content
        b1 = pb.get("/p/v1/documentos/laudo-assinado").content
        a2 = pa.get("/p/v1/documentos/exame-tecnico").content
        b2 = pb.get("/p/v1/documentos/exame-tecnico").content

    assert a1 == caso_a["assinado_bytes"] and b1 == caso_b["assinado_bytes"]
    assert a2 == caso_a["mir_bytes"] and b2 == caso_b["mir_bytes"]
    # Os laudos assinados são distintos; as MIRs sintéticas são idênticas por
    # construção, então o cruzamento se prova pelo vínculo de versão.
    assert a1 != b1
    assert (
        prs.technical_version(db, _acesso(db, caso_a["document_id"])).id
        != prs.technical_version(db, _acesso(db, caso_b["document_id"])).id
    )


# ======================================================================
# 14-16 — revogação, regeneração e substituição do PDF assinado
# ======================================================================


def test_14_revogacao_invalida_acesso_e_derruba_sessao(portal, client, auth, db, caso_a):
    token = _token(db, caso_a["document_id"])
    assert _entrar(portal, token).status_code == 200
    assert portal.get("/p/v1/documentos/laudo-assinado").status_code == 200

    resposta = client.post(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado/revogar",
        json={"motivo": "Solicitação do paciente (teste)"},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["status"] == RESULTADO_REVOGADO

    # A sessão que já estava aberta morre junto: o PDF não abre mais.
    assert portal.get("/p/v1/documentos/laudo-assinado").status_code == 401
    assert portal.get("/p/v1/documentos").status_code == 401
    # E o link antigo também não abre.
    assert _entrar(portal, token).status_code == 410


def test_15_regeneracao_invalida_o_token_antigo(portal, client, auth, db, caso_a):
    antigo = _token(db, caso_a["document_id"])
    resposta = client.post(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado",
        json={"regenerar": True},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    novo_link = resposta.json()["link"]

    novo = _token(db, caso_a["document_id"])
    assert novo != antigo
    assert novo in novo_link
    assert _entrar(portal, antigo).status_code == 401
    assert _entrar(portal, novo).status_code == 200


def test_16_novo_pdf_assinado_reaponta_o_acesso(db, caso_a):
    """Substituir a versão assinada NÃO troca o link já entregue.

    O paciente pode estar com o link no WhatsApp desde ontem. O que muda é
    o arquivo que aquele link entrega — que passa a ser a versão vigente.
    """

    acesso_antes = _acesso(db, caso_a["document_id"])
    token_antes = prs.derive_token(acesso_antes.id, acesso_antes.generation)
    versao_antes = acesso_antes.report_document_version_id

    anterior = db.get(ExternalSignedDocument, acesso_antes.signed_document_id)
    nova_versao = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == caso_a["document_id"],
            ReportDocumentVersion.kind == "original",
        )
    ).scalar_one()
    substituto = ExternalSignedDocument(
        report_document_id=anterior.report_document_id,
        report_document_version_id=nova_versao.id,
        source_version_id=anterior.source_version_id,
        source_sha256=anterior.source_sha256,
        batch_id=anterior.batch_id,
        physician_profile_id=anterior.physician_profile_id,
        uploader_user_id=anterior.uploader_user_id,
        sha256=nova_versao.sha256,
        size_bytes=nova_versao.size_bytes,
        received_filename="substituto.pdf",
        match_method=anterior.match_method,
        status=ASSINADO_ACEITO,
    )
    db.add(substituto)
    db.flush()

    prs.ensure_access(db, substituto)
    db.commit()

    depois = _acesso(db, caso_a["document_id"])
    assert depois.id == acesso_antes.id
    assert depois.report_document_version_id == nova_versao.id != versao_antes
    assert depois.signed_document_id == substituto.id
    # MESMO link: o que estava no WhatsApp continua valendo.
    assert prs.derive_token(depois.id, depois.generation) == token_antes


def test_16b_acesso_revogado_nao_ressuscita_com_pdf_novo(client, auth, db, caso_a):
    """Revogar foi um ato de alguém. Um arquivo novo não o desfaz sozinho."""

    client.post(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado/revogar",
        json={"motivo": "Teste de não-ressurreição"},
        headers=auth("operacional"),
    )
    assinado = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == caso_a["document_id"]
        )
    ).scalar_one()
    prs.ensure_access(db, assinado)
    db.commit()
    assert _acesso(db, caso_a["document_id"]).status == RESULTADO_REVOGADO


# ======================================================================
# 17-18, 23 — a fronteira entre a superfície pública e o Command Center
# ======================================================================


def test_17_portal_nao_aceita_cookie_administrativo_como_atalho(
    portal, client, db, caso_a
):
    """O cookie do painel é assinado com OUTRO segredo. Aqui ele não fecha."""

    login = client.post(
        "/api/v1/auth/token",
        json={"email": "oper@teste.local", "password": "senha-teste-123"},
    )
    assert login.status_code == 200, login.text
    settings = get_settings()
    cookie_admin = login.cookies.get(settings.session_cookie_name)
    assert cookie_admin, "o painel precisa ter emitido cookie de sessão"

    portal.cookies.set(settings.portal_cookie_name, cookie_admin)
    assert portal.get("/p/v1/documentos").status_code == 401
    assert portal.get("/p/v1/documentos/laudo-assinado").status_code == 401

    # E o contrário: o cookie do portal não vale no painel.
    portal.cookies.clear()
    _entrar(portal, _token(db, caso_a["document_id"]))
    cookie_portal = portal.cookies.get(settings.portal_cookie_name)
    assert cookie_portal
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, cookie_portal)
    assert client.get("/api/v1/laudos").status_code == 401


def test_18_e_23_superficie_publica_tem_exatamente_estas_rotas(portal):
    """O congelamento da superfície. Uma rota nova aqui QUEBRA o teste."""

    from app.portal.main import create_portal_app

    rotas = {
        (r.path, tuple(sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS"))))
        for r in create_portal_app().routes
        if hasattr(r, "methods")
    }
    assert rotas == {
        ("/p/v1/health", ("GET",)),
        ("/p/v1/acesso", ("POST",)),
        ("/p/v1/documentos", ("GET",)),
        ("/p/v1/sair", ("POST",)),
        ("/p/v1/documentos/laudo-assinado", ("GET",)),
        ("/p/v1/documentos/exame-tecnico", ("GET",)),
    }
    # Nem /api/v1, nem /docs, nem /openapi.json.
    caminhos = {p for p, _ in rotas}
    assert not any(c.startswith("/api") for c in caminhos)
    for fechado in ("/docs", "/redoc", "/openapi.json", "/api/v1/laudos",
                    "/api/v1/pessoas", "/api/v1/financeiro", "/api/v1/crm"):
        assert portal.get(fechado).status_code == 404


def test_18b_command_center_continua_recusando_bind_publico():
    """A trava que mantém o painel atrás do Tailscale não foi afrouxada."""

    from app.config import Settings

    Settings.model_config["env_file"] = None
    forte = "m26-4-secret-de-teste-com-tamanho-e-variedade-0123456789"
    with pytest.raises(ValueError, match="loopback"):
        Settings(env="prod", auth_secret=forte, api_host="0.0.0.0")
    # E o portal também: quem termina em HTTPS é o proxy, não o processo.
    with pytest.raises(ValueError, match="loopback"):
        Settings(env="prod", auth_secret=forte, portal_api_host="0.0.0.0")


def test_18c_os_dois_segredos_nao_podem_ser_o_mesmo():
    from app.config import Settings

    Settings.model_config["env_file"] = None
    igual = "m26-4-secret-de-teste-com-tamanho-e-variedade-0123456789"
    settings = Settings(auth_secret=igual, portal_session_secret=igual)
    with pytest.raises(ValueError, match="assinaturas distintas"):
        settings.resolved_portal_session_secret()


# ======================================================================
# 19 — o QR Code
# ======================================================================


def _ler_qr(matriz) -> str:
    """Leitor independente: desfaz máscara, percorre o zigue-zague e monta.

    Não usa nada do caminho de ESCRITA além do mapa de módulos de função —
    a geometria em si é conferida contra o `qrencode` no teste seguinte.
    O que este leitor prova é que os dados sobrevivem à intercalação de
    blocos, ao mascaramento e à colocação: é ali que moram os erros.
    """

    n = len(matriz)
    versao = (n - 17) // 4
    # A máscara sai do próprio bloco de formato, e não do gerador.
    bits_formato = 0
    for i in range(6):
        bits_formato |= matriz[i][8] << i
    bits_formato |= matriz[7][8] << 6
    bits_formato |= matriz[8][8] << 7
    bits_formato |= matriz[8][7] << 8
    for i in range(9, 15):
        bits_formato |= matriz[8][14 - i] << i
    mascara = ((bits_formato ^ 0x5412) >> 10) & 0b111

    molde = qr._Matriz(versao)
    qr._desenhar_funcoes(molde)

    bits: list[int] = []
    coluna = n - 1
    subindo = True
    while coluna > 0:
        if coluna == 6:
            coluna -= 1
        alcance = range(n - 1, -1, -1) if subindo else range(n)
        for linha in alcance:
            for delta in (0, 1):
                c = coluna - delta
                if molde.reservado[linha][c]:
                    continue
                bits.append(qr._mascarar(matriz[linha][c], linha, c, mascara))
        coluna -= 2
        subindo = not subindo

    codewords = [
        int("".join(str(b) for b in bits[i:i + 8]), 2)
        for i in range(0, len(bits) - len(bits) % 8, 8)
    ]
    _total, _ec, tamanhos = qr._VERSOES_M[versao]
    blocos: list[list[int]] = [[] for _ in tamanhos]
    cursor = 0
    for i in range(max(tamanhos)):
        for indice, tamanho in enumerate(tamanhos):
            if i < tamanho:
                blocos[indice].append(codewords[cursor])
                cursor += 1
    dados = b"".join(bytes(bloco) for bloco in blocos)

    fluxo = "".join(f"{byte:08b}" for byte in dados)
    assert fluxo[:4] == "0100", "modo byte"
    largura = 8 if versao < 10 else 16
    quantidade = int(fluxo[4:4 + largura], 2)
    inicio = 4 + largura
    corpo = bytes(
        int(fluxo[inicio + i * 8:inicio + i * 8 + 8], 2)
        for i in range(quantidade)
    )
    return corpo.decode("utf-8")


def token_do_link(link: str) -> str:
    return link.split("#t=", 1)[1]


def test_19_qr_contem_apenas_a_url_segura(client, auth, db, caso_a):
    resposta = client.get(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    link = corpo["link"]

    # O QR carrega EXATAMENTE o link, e nada mais.
    assert _ler_qr(qr.encode(link).modulos) == link
    assert link.startswith("https://soprolife.com.br/resultados/#t=")
    # E o link não carrega identidade nenhuma.
    for proibido in ("LAU-", "ESP-", "PES-", "TESTE", "Alfa", "1978", "cpf"):
        assert proibido not in link
    assert caso_a["document_id"] not in link
    assert caso_a["pessoa"]["id"] not in link

    # O QR aponta para a PÁGINA. Nunca direto para um PDF.
    assert "/documentos/" not in link and ".pdf" not in link
    svg = corpo["qr_svg"]
    assert svg.startswith("<svg ") and "</svg>" in svg
    # O SVG é só geometria: sem script, sem referência externa e — o que
    # importa aqui — sem o link em texto legível dentro do arquivo.
    assert "<script" not in svg and "<image" not in svg and "xlink" not in svg
    assert "soprolife.com.br" not in svg and "#t=" not in svg
    assert token_do_link(link) not in svg


@pytest.mark.skipif(
    subprocess.run(["which", "qrencode"], capture_output=True).returncode != 0,
    reason="qrencode não instalado neste ambiente",
)
def test_19b_qr_identico_ao_qrencode_do_sistema():
    """Prova externa da geometria: matriz por matriz, contra outra implementação.

    Versões 2 a 10, que é onde o link do portal cai (versão 5) e onde as duas
    implementações concordam também na escolha de máscara.
    """

    casos = [
        "c" * 26, "d" * 42, "e" * 62, "f" * 84, "g" * 106,
        "h" * 122, "i" * 152, "j" * 180, "k" * 213,
        "https://soprolife.com.br/resultados/#t=" + "a" * 43,
        "teste ação ç 001",
    ]
    for texto in casos:
        meu = qr.encode(texto)
        n = meu.tamanho
        saida = subprocess.run(
            ["qrencode", "-8", "-l", "M", "-m", "0", "-t", "ASCII",
             "-v", str(meu.versao), "-o", "-"],
            input=texto.encode(), capture_output=True, check=True,
        ).stdout.decode()
        linhas = [l for l in saida.split("\n") if l.strip()]
        referencia = tuple(
            tuple(1 if l.ljust(n * 2)[i * 2] == "#" else 0 for i in range(n))
            for l in linhas
        )
        assert referencia == meu.modulos, f"divergência em {len(texto)} bytes"


# ======================================================================
# 20-22 — WhatsApp manual
# ======================================================================


def test_20_ausencia_de_telefone_nao_quebra(client, auth, db):
    caso = _montar_caso(
        client, auth, db, nome="TESTE APAGAR Sem Telefone", suffix="46",
        telefone=None,
    )
    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}/acesso-resultado",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["whatsapp"] == {
        "disponivel": False, "motivo": "Telefone não cadastrado"
    }
    # Copiar link e QR continuam de pé — é isso que "não quebra" significa.
    assert corpo["link"].startswith("https://soprolife.com.br/resultados/#t=")
    assert corpo["qr_svg"].startswith("<svg ")


def test_21_whatsapp_manual_gera_mensagem_correta(client, auth, db, caso_a):
    from urllib.parse import parse_qs, urlsplit

    resposta = client.get(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado",
        headers=auth("operacional"),
    )
    corpo = resposta.json()
    zap = corpo["whatsapp"]
    assert zap["disponivel"] is True

    partes = urlsplit(zap["url"])
    assert partes.scheme == "https" and partes.netloc == "wa.me"
    assert partes.path.strip("/").isdigit()
    texto = parse_qs(partes.query)["text"][0]
    assert texto == corpo["mensagem"]

    assert texto.startswith("Olá, TESTE.")  # primeiro nome do cadastro
    assert "Seu resultado de espirometria realizado pela SoproLife" in texto
    assert corpo["link"] in texto
    assert "confirmar sua data de nascimento" in texto
    assert texto.rstrip().endswith("SoproLife Diagnósticos e Soluções em Saúde")


def test_22_nenhuma_informacao_clinica_vai_na_mensagem(client, auth, db, caso_a):
    corpo = client.get(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado",
        headers=auth("operacional"),
    ).json()
    texto = corpo["mensagem"]
    proibidos = [
        "DVO", "obstru", "distúrbio", "broncodilatador", "CVF", "VEF",
        "normal", "alterado", "LAU-", "ESP-", "PES-", "CPF", "sha256",
        "1978", "09/04",
    ]
    for termo in proibidos:
        assert termo.lower() not in texto.lower(), termo


def test_21b_envio_registra_o_que_de_fato_aconteceu(client, auth, db, caso_a):
    """`sent_at` diz que o operador abriu o envio — nunca que chegou."""

    resposta = client.post(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado/enviado",
        json={"canal": "whatsapp_manual"},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["status"] == RESULTADO_ENVIADO
    acesso = _acesso(db, caso_a["document_id"])
    assert acesso.sent_at is not None
    assert acesso.first_access_at is None  # ninguém abriu ainda


# ======================================================================
# 24 — cabeçalhos, noindex, cache
# ======================================================================


def test_24_cabecalhos_de_seguranca_em_json_e_em_pdf(portal, db, caso_a):
    entrada = _entrar(portal, _token(db, caso_a["document_id"]))
    pdf = portal.get("/p/v1/documentos/laudo-assinado")
    for resposta in (entrada, pdf):
        h = resposta.headers
        assert "no-store" in h["cache-control"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "noindex" in h["x-robots-tag"] and "nofollow" in h["x-robots-tag"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]

    cookie = entrada.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie.replace("SameSite=Strict", "SameSite=strict")
    assert "Path=/p/v1" in cookie


def test_24b_pagina_publica_e_robots_declaram_noindex():
    html = PORTAL_HTML.read_text()
    assert 'name="robots"' in html
    assert "noindex" in html and "nofollow" in html and "noarchive" in html
    assert 'name="referrer" content="no-referrer"' in html
    # Nenhum analytics, nenhum pixel, nenhuma fonte externa, nenhum CDN.
    for proibido in ("googletagmanager", "google-analytics", "gtag(",
                     "connect.facebook", "fbq(", "fonts.googleapis",
                     "cdn.jsdelivr", "cdnjs", "unpkg"):
        assert proibido not in html, proibido
    # A CSP lista UMA origem de rede, e é a API do portal.
    assert "connect-src https://resultados-api.soprolife.com.br;" in html
    assert "Disallow: /resultados/" in ROBOTS.read_text()


def test_24c_o_token_nao_viaja_em_querystring():
    """O segredo vive no FRAGMENTO — que o navegador não manda ao servidor."""

    html = PORTAL_HTML.read_text()
    assert "location.hash" in html
    assert "?t=" not in html
    assert "?token=" not in html
    link = prs.public_url("Z" * 43)
    from urllib.parse import urlsplit

    partes = urlsplit(link)
    assert partes.query == ""
    assert partes.fragment.startswith("t=")


# ======================================================================
# 25 — a página no celular
# ======================================================================


def test_25_pagina_publica_e_responsiva_e_de_alvo_grande():
    html = PORTAL_HTML.read_text()
    assert 'name="viewport"' in html and "width=device-width" in html
    assert "max-width: 560px" in html          # coluna de leitura no celular
    assert "@media (max-width: 380px)" in html  # ajuste de tela pequena
    # Botões de download com alvo confortável de toque (>= 48px é o mínimo
    # recomendado; aqui são 58 e 54).
    assert "min-height: 58px" in html and "min-height: 54px" in html
    assert 'inputmode="numeric"' in html        # teclado numérico no celular
    assert "100dvh" in html                     # barra de endereço do iOS


def test_25b_painel_administrativo_tem_o_bloco_de_resultado():
    assert "data-result-open" in WORKFLOW_JS
    assert "data-result-whatsapp" in WORKFLOW_JS
    assert "data-result-copy" in WORKFLOW_JS
    assert "data-result-revoke" in WORKFLOW_JS
    assert "data-result-create" in WORKFLOW_JS
    assert "Resultado online:" in WORKFLOW_JS
    assert ".report-result-panel" in WORKFLOW_CSS
    assert "@media (max-width: 640px)" in WORKFLOW_CSS


# ======================================================================
# Extras que a missão exige explicitamente
# ======================================================================


def test_fila_administrativa_mostra_estado_sem_expor_o_link(client, auth, db, caso_a):
    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    )
    assert fila.status_code == 200, fila.text
    token = _token(db, caso_a["document_id"])
    assert token not in fila.text
    assert "qr_svg" not in fila.text
    linha = next(
        i for i in fila.json()["itens"] if i["document_id"] == caso_a["document_id"]
    )
    assert linha["resultado"]["existe"] is True
    assert linha["resultado"]["status_rotulo"] == "Disponível"
    # O estado clínico continua exatamente o que era.
    assert linha["estado"] == "pronto_para_entrega"


def test_expiracao_da_a_instrucao_certa(portal, db, caso_a):
    from datetime import timedelta

    acesso = _acesso(db, caso_a["document_id"])
    token = prs.derive_token(acesso.id, acesso.generation)
    acesso.expires_at = prs.agora() - timedelta(days=1)
    db.commit()
    resposta = _entrar(portal, token)
    assert resposta.status_code == 410
    assert "Este acesso expirou" in resposta.json()["erro"]["mensagem"]
    assert "SoproLife" in resposta.json()["erro"]["mensagem"]


def test_validade_padrao_e_de_noventa_dias(db, caso_a):
    acesso = _acesso(db, caso_a["document_id"])
    dias = (
        prs._como_utc(acesso.expires_at) - prs._como_utc(acesso.created_at)
    ).days
    assert dias == 90


def test_portal_desligado_nao_cria_acesso_nem_responde(
    monkeypatch, client, auth, db, portal
):
    """Fail-closed: um deploy de código não abre o portal por efeito colateral."""

    monkeypatch.setenv("M15_PORTAL_ENABLED", "false")
    get_settings.cache_clear()
    caso = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Desligado", suffix="47"
    )
    _nascer(client, auth, caso["pessoa"]["id"])
    _assinar_e_devolver(client, caso)
    assert _acesso(db, caso["document_id"]) is None
    assert portal.post(
        "/p/v1/acesso", json={"token": "x" * 43, "nascimento": NASCIMENTO}
    ).status_code == 503


def test_apenas_papel_operacional_administra_o_acesso(client, auth, caso_a):
    for papel in ("leitura",):
        resposta = client.get(
            f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado",
            headers=auth(papel),
        )
        assert resposta.status_code == 403
    sem_credencial = client.get(
        f"/api/v1/laudos/{caso_a['document_id']}/acesso-resultado"
    )
    assert sem_credencial.status_code == 401


def test_historico_so_ganha_acesso_com_clique_explicito(client, auth, db):
    """Automação vale daqui para frente. Nada de disparo em massa."""

    caso = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Historico", suffix="48"
    )
    _nascer(client, auth, caso["pessoa"]["id"])
    _concluir(client, caso)
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    # Simula o histórico: o assinado existe, mas o acesso não nasceu com ele.
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": (
                "assinado.pdf",
                _assinar_por_fora(baixado.content),
                "application/pdf",
            )
        },
        headers=caso["doctor_auth"],
    )
    assert envio.status_code == 200
    acesso = _acesso(db, caso["document_id"])
    db.delete(
        db.execute(
            select(PatientResultAccess).where(PatientResultAccess.id == acesso.id)
        ).scalar_one()
    )
    db.commit()
    assert _acesso(db, caso["document_id"]) is None

    criado = client.post(
        f"/api/v1/laudos/{caso['document_id']}/acesso-resultado",
        json={"regenerar": False},
        headers=auth("operacional"),
    )
    assert criado.status_code == 200, criado.text
    assert criado.json()["link"].startswith("https://soprolife.com.br/resultados/#t=")
    assert _acesso(db, caso["document_id"]) is not None


def test_sair_encerra_a_sessao_sem_matar_o_link(portal, db, caso_a):
    token = _token(db, caso_a["document_id"])
    assert _entrar(portal, token).status_code == 200
    assert portal.post("/p/v1/sair").status_code == 200
    assert portal.get("/p/v1/documentos").status_code == 401
    # O link continua válido: sair é do aparelho, não do acesso.
    assert _entrar(portal, token).status_code == 200


def test_sessao_do_paciente_guarda_hash_e_nao_o_segredo(portal, db, caso_a):
    _entrar(portal, _token(db, caso_a["document_id"]))
    bruto = portal.cookies.get(get_settings().portal_cookie_name)
    segredo = bruto.split(".")[1]
    sessao = db.execute(select(PatientResultSession)).scalars().one()
    assert sessao.token_hash == hashlib.sha256(segredo.encode()).hexdigest()
    assert segredo not in sessao.token_hash


# ======================================================================
# A PROVA FINAL — os dez passos, numa história só
# ======================================================================


def test_prova_final_da_medica_ao_paciente(client, auth, db, portal, capsys):
    """1) a médica envia o PDF assinado … 10) o admin vê "Acessado".

    Este teste existe para ser LIDO. Cada passo abaixo é uma afirmação da
    missão M26.4, na ordem em que ela pediu, com dados 100% sintéticos.
    """

    passos: list[str] = []

    # 1 e 2 — a médica conclui, assina por fora e devolve; o sistema aceita.
    caso = _montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente Prova", suffix="49"
    )
    assinado = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == caso["document_id"]
        )
    ).scalar_one()
    assinado_status, assinado_sha = assinado.status, assinado.sha256
    assinado_versao = assinado.report_document_version_id
    original_sha = db.execute(
        select(ReportDocumentVersion.sha256).where(
            ReportDocumentVersion.report_document_id == caso["document_id"],
            ReportDocumentVersion.kind == "original",
        )
    ).scalar_one()
    db.commit()  # o SQLite da suíte é compartilhado; não segurar transação
    assert assinado_status == ASSINADO_ACEITO
    passos.append("1-2 PDF assinado recebido e aceito pelas guardas M25.29H")

    # 3 — o acesso nasceu SOZINHO, amarrado à versão assinada vigente.
    acesso = _acesso(db, caso["document_id"])
    assert acesso is not None and acesso.status == RESULTADO_DISPONIVEL
    assert acesso.report_document_version_id == assinado_versao
    passos.append("3 acesso criado automaticamente, sem ação extra da médica")

    # 4 e 5 — QR e mensagem de WhatsApp nascem prontos, no painel privado.
    painel = client.get(
        f"/api/v1/laudos/{caso['document_id']}/acesso-resultado",
        headers=auth("operacional"),
    ).json()
    assert painel["qr_svg"].startswith("<svg ")
    assert _ler_qr(qr.encode(painel["link"]).modulos) == painel["link"]
    assert painel["whatsapp"]["disponivel"] is True
    assert painel["link"] in painel["mensagem"]
    passos.append("4-5 QR e mensagem de WhatsApp prontos, sem dado clínico")

    # 6 e 7 — o paciente abre o link e informa a data de nascimento.
    token = painel["link"].split("#t=")[1]
    entrada = _entrar(portal, token)
    assert entrada.status_code == 200
    corpo = entrada.json()
    passos.append("6-7 paciente abriu o link e confirmou a data de nascimento")

    # 8 — vê exatamente o laudo assinado e o exame técnico.
    assert corpo["paciente"]["nome"] == "TESTE APAGAR Paciente Prova"
    assert [d["chave"] for d in corpo["documentos"]] == [
        "laudo-assinado", "exame-tecnico"
    ]
    assert all(d["disponivel"] for d in corpo["documentos"])
    passos.append("8 dois documentos oferecidos, e só eles")

    # 9 — o SHA do que sai bate com o que entrou.
    laudo = portal.get("/p/v1/documentos/laudo-assinado")
    tecnico = portal.get("/p/v1/documentos/exame-tecnico")
    assert hashlib.sha256(laudo.content).hexdigest() == assinado_sha
    assert laudo.content == caso["assinado_bytes"]
    assert hashlib.sha256(tecnico.content).hexdigest() == original_sha
    passos.append("9 SHA do laudo e do técnico conferem nos dois sentidos")

    # 10 — o painel vê "Acessado", com data e hora.
    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    ).json()
    linha = next(
        i for i in fila["itens"] if i["document_id"] == caso["document_id"]
    )
    assert linha["resultado"]["status_rotulo"] == "Acessado"
    assert linha["resultado"]["primeiro_acesso_em"] is not None
    assert linha["resultado"]["downloads"] == 2
    passos.append("10 painel mostra Acessado, com data/hora e 2 downloads")

    with capsys.disabled():
        print("\n  PROVA FINAL M26.4")
        for passo in passos:
            print(f"    ✓ {passo}")


def test_todo_identificador_cabe_no_postgres():
    """O SQLite aceita nome de constraint de qualquer tamanho. O Postgres não.

    A M26.4 quase entregou uma migração que rodava perfeita na suíte e
    explodia no deploy: a convenção do projeto
    (`fk_<tabela>_<coluna>_<tabela_referida>`) gerava
    `fk_patient_result_accesses_report_document_version_id_report_document_versions`
    — 78 caracteres, contra o limite de 63 do PostgreSQL.

    Este teste compila o `CREATE TABLE` de TODAS as tabelas com o dialeto do
    PostgreSQL, que é exatamente onde o erro aparece. Ele vale para o mapa
    inteiro, e não só para as tabelas desta etapa: a próxima tabela com nome
    longo falha aqui, na suíte, e não no meio de uma janela de implantação.
    """

    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app.db import Base

    dialeto = postgresql.dialect()
    problemas: list[tuple[str, str]] = []
    for tabela in Base.metadata.sorted_tables:
        try:
            str(CreateTable(tabela).compile(dialect=dialeto))
        except Exception as erro:  # noqa: BLE001 - o teste É sobre a exceção
            problemas.append((tabela.name, str(erro)))
        for indice in tabela.indexes:
            try:
                str(CreateIndex(indice).compile(dialect=dialeto))
            except Exception as erro:  # noqa: BLE001
                problemas.append((f"{tabela.name}/{indice.name}", str(erro)))
    assert not problemas, problemas


def test_fora_do_prefixo_publico_e_sempre_o_mesmo_404_com_cabecalhos(portal):
    """Descoberto em produção: `/api/v1/laudos` respondia DIFERENTE.

    O `install_error_handling`, compartilhado com o Command Center, responde
    cedo — antes das rotas — a qualquer caminho começado em `/api/v1/laudos`.
    No portal isso devolvia `503 relatorios_desabilitados`, e sem nenhum dos
    cabeçalhos de segurança, porque a camada que os aplica era a mais INTERNA
    e nunca chegava a rodar.

    Duas coisas erradas numa só resposta: um caminho que responde diferente
    dos outros conta a quem varre que esta máquina tem a ver com um sistema
    de laudos, e um cabeçalho de segurança que depende do caminho feliz não é
    garantia.
    """

    corpos = set()
    for caminho in (
        "/api/v1/laudos", "/api/v1/laudos/qualquer", "/api/v1/pessoas",
        "/api/v1/financeiro", "/docs", "/openapi.json", "/", "/qualquer-coisa",
        "/p/v1", "/p/v1x/health",
    ):
        resposta = portal.get(caminho)
        assert resposta.status_code == 404, caminho
        corpos.add(resposta.text)
        cabecalhos = resposta.headers
        assert "no-store" in cabecalhos["cache-control"], caminho
        assert "noindex" in cabecalhos["x-robots-tag"], caminho
        assert cabecalhos["x-frame-options"] == "DENY", caminho
        assert "default-src 'none'" in cabecalhos["content-security-policy"]
        for vazamento in ("laudo", "relatorio", "desabilitado", "soprolife_m15"):
            assert vazamento not in resposta.text.lower(), (caminho, vazamento)
    # Todos exatamente iguais: não há caminho que se comporte de forma
    # distinta e sirva de sonda.
    assert len(corpos) == 1


def test_a_camada_de_cabecalhos_e_a_mais_externa():
    """Congela a ORDEM, que é o que a correção acima realmente mudou.

    No Starlette o middleware adicionado por ÚLTIMO é o mais EXTERNO. Se
    alguém acrescentar um `add_middleware` depois da fronteira pública, ela
    deixa de envolver tudo — e o defeito volta calado.
    """

    from app.portal.main import create_portal_app

    nomes = [m.cls.__name__ for m in create_portal_app().user_middleware]
    # A lista já vem na ordem de fora para dentro.
    assert nomes[0] == "BaseHTTPMiddleware"  # fronteira_publica
    assert "CORSMiddleware" in nomes
    assert nomes.index("CORSMiddleware") == len(nomes) - 1
