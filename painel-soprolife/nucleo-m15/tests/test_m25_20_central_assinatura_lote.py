"""M25.20 — central de assinatura externa em lote.

A médica continua laudando um a um. O que vira lote é o trabalho
burocrático depois da conclusão: baixar os PDFs, assinar fora, devolver.

O que estes testes travam, em ordem de gravidade:

1. **Nunca associar por semelhança de nome.** Um arquivo renomeado
   "Maria Souza.pdf" não pode ser anexado ao laudo de nenhuma Maria. O
   pareamento é por metadado carimbado, código LAU impresso ou código de
   verificação — e por nada mais.
2. **Receber não é validar.** Nenhum caminho de upload declara assinatura
   ICP-Brasil verificada, porque nenhuma cadeia é conferida aqui.
3. **Médico A nunca alcança laudo de médico B**, nem para baixar nem para
   devolver.
4. **A versão original é preservada**: o assinado que volta é versão nova.

Todos os pacientes, médicos, CRMs e PDFs são sintéticos.
"""

from __future__ import annotations

import hashlib
import io
import pathlib
import zipfile

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    ASSINADO_EM_CONFERENCIA,
    ASSINADO_ENTREGUE,
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
    ASSINADO_VALIDADO_EXTERNAMENTE,
    PAREAMENTO_CODIGO_LAUDO,
    PAREAMENTO_CODIGO_VALIDACAO,
    PAREAMENTO_METADADO,
    AuditLog,
    ExternalSignedDocument,
    Person,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)
from app.services.pdf_validation import InvalidPdfError, validate_pdf_bytes
from app.services.signature_batch import (
    MAX_ARQUIVOS_POR_LOTE,
    build_batch_zip,
    extract_signed_pdfs,
    read_markers_from_metadata,
    signing_filename,
    stamp_signing_metadata,
    BatchFile,
)

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()

RELEASE_CONFIRMATION = "ASSINAR E LIBERAR"
VALIDACAO_CONFIRMACAO = "Confirmo a conferência externa"


# ------------------------------------------------------------ utilidades


def _minimal_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    saida = io.BytesIO()
    writer.write(saida)
    return saida.getvalue()


def _pdf_com_texto(texto: str) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    for indice, linha in enumerate(texto.splitlines()):
        c.drawString(72, 760 - indice * 16, linha)
    c.save()
    return buffer.getvalue()


def _assinar_por_fora(pdf: bytes) -> bytes:
    """Simula a assinatura externa: incremental update que ANEXA bytes.

    É assim que um assinador PAdES real trabalha — ele não reescreve o
    documento, acrescenta uma revisão. O que importa aqui é que o carimbo de
    metadados sobreviva, que é a premissa do pareamento.
    """

    return pdf + b"\n% revisao incremental de assinatura (sintetica)\n"


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
        "m25-20-central-assinatura-lote-secret-teste-0123456789",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _physician(db, *, suffix: str) -> tuple[User, dict]:
    ensure_roles_exist(db)
    user = User(
        email=f"medica-m25-20-{suffix}@teste.local",
        nome=f"TESTE APAGAR Médica {suffix}",
        password_hash=hash_password("senha-medica-sintetica-m2520"),
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
            "verification_reference": "CRM-VERIF-TESTE-M2520",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["profile"]


def _criar_pessoa(client, auth, *, nome: str) -> dict:
    resposta = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": nome},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _criar_exame(client, auth, pessoa) -> dict:
    resposta = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-08-05",
                "status": "Realizado",
                "broncodilatador": True,
            },
        },
        headers=auth("operacional"),
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["espirometria"]


def _anexar(client, auth, exame, profile) -> dict:
    resposta = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-teste-m2520",
        },
        files={"file": ("TESTE-APAGAR.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def _concluir(client, doctor_auth, document_id) -> dict:
    """Leva o laudo até "concluído" — o estado em que a central o pega."""

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


def _caso(client, auth, db, *, nome_paciente, suffix, doctor=None):
    if doctor is None:
        user, doctor_auth = _physician(db, suffix=suffix)
        profile = _configure_profile(
            client,
            auth,
            user,
            crm=f"9{suffix}",
            name=f"TESTE APAGAR Médica {suffix}",
        )
        doctor = {"user": user, "auth": doctor_auth, "profile": profile}
    pessoa = _criar_pessoa(client, auth, nome=nome_paciente)
    exame = _criar_exame(client, auth, pessoa)
    documento = _anexar(client, auth, exame, doctor["profile"])
    concluido = _concluir(client, doctor["auth"], documento["id"])
    return {
        "doctor": doctor,
        "pessoa": pessoa,
        "exame": exame,
        "document_id": documento["id"],
        "report_code": documento["public_code"],
        "concluido": concluido,
    }


@pytest.fixture()
def caso(client, auth, db):
    return _caso(
        client, auth, db, nome_paciente="TESTE APAGAR Antonio Lopes", suffix="01"
    )


def _baixar_versao_concluida(client, caso_) -> bytes:
    """Os bytes exatos do laudo concluído, como a médica os recebe."""

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso_["document_id"]]},
        headers=caso_["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.content


# =====================================================================
# 1. O carimbo e o pareamento
# =====================================================================


def test_laudo_concluido_sai_carimbado_com_a_identificacao(client, caso):
    """O carimbo é o que permite reconhecer o arquivo quando ele voltar."""

    pdf = _baixar_versao_concluida(client, caso)
    marcadores = read_markers_from_metadata(pdf)
    assert marcadores.report_code == caso["report_code"]
    assert marcadores.validation_code == caso["concluido"]["validation_code"]
    assert marcadores.version_number is not None
    assert marcadores.source_sha256 is not None


def test_carimbo_sobrevive_a_assinatura_incremental():
    """Assinar ANEXA uma revisão; o carimbo tem de continuar legível."""

    base = _pdf_com_texto("Laudo sintetico")
    carimbado = stamp_signing_metadata(
        base,
        document_code="LAU-000777",
        validation_code="ABCDEFGHJKMN",
        version_number=3,
        physician_name="TESTE APAGAR Médica",
        crm="CRM-RJ 90001",
    )
    assinado = _assinar_por_fora(carimbado)
    marcadores = read_markers_from_metadata(assinado)
    assert marcadores.report_code == "LAU-000777"
    assert marcadores.validation_code == "ABCDEFGHJKMN"
    assert marcadores.version_number == 3


def test_carimbo_nao_altera_o_texto_impresso_do_laudo():
    """Carimbar mexe em metadado, nunca no que está escrito no papel."""

    base = _pdf_com_texto("Laudo LAU-000777\nCodigo ABCDEFGHJKMN")
    antes = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(base)).pages)
    carimbado = stamp_signing_metadata(
        base,
        document_code="LAU-000777",
        validation_code="ABCDEFGHJKMN",
        version_number=1,
        physician_name="TESTE APAGAR Médica",
        crm="CRM-RJ 90001",
    )
    depois = "".join(
        p.extract_text() or "" for p in PdfReader(io.BytesIO(carimbado)).pages
    )
    assert antes == depois


def test_pareamento_pelo_metadado(client, caso):
    """Caminho normal: o arquivo volta e é reconhecido pelo carimbo."""

    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("qualquer-nome.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"]["identificados"] == 1
    assert corpo["identificados"][0]["pareado_por"] == PAREAMENTO_METADADO
    assert corpo["identificados"][0]["report_code"] == caso["report_code"]


def test_pareamento_retroativo_pelo_codigo_lau_impresso(client, caso, db):
    """Laudo anterior à M25.20 não tem carimbo — mas tem o LAU impresso.

    Este é o caso de compatibilidade do §9: o arquivo devolvido não traz
    nenhum metadado da SoproLife, e mesmo assim precisa ser reconhecido.
    """

    sem_carimbo = _pdf_com_texto(
        f"Laudo de espirometria\n{caso['report_code']}\nSoproLife"
    )
    assert read_markers_from_metadata(sem_carimbo).empty

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("documento-final.pdf", sem_carimbo, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"]["identificados"] == 1
    assert corpo["identificados"][0]["pareado_por"] == PAREAMENTO_CODIGO_LAUDO


def test_pareamento_retroativo_pelo_codigo_de_verificacao(client, caso):
    """Sem LAU legível, o código de verificação impresso ainda identifica."""

    codigo = caso["concluido"]["validation_code"]
    sem_lau = _pdf_com_texto(f"Documento assinado\nVerificacao: {codigo}")
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("scan.pdf", sem_lau, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"]["identificados"] == 1
    assert corpo["identificados"][0]["pareado_por"] == PAREAMENTO_CODIGO_VALIDACAO


def test_nome_de_arquivo_sozinho_nunca_identifica(client, caso):
    """O TESTE CENTRAL desta missão.

    Um PDF sem nenhum código, cujo NOME é exatamente o nome da paciente e o
    formato que o sistema gera, não pode ser associado a nada. Associar aqui
    significaria anexar um documento assinado ao prontuário errado.
    """

    nome_exato = signing_filename(
        caso["pessoa"]["nome_completo"], fallback_code=caso["report_code"]
    )
    anonimo = _pdf_com_texto("Documento sem nenhum codigo identificador")

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": (nome_exato, anonimo, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"]["identificados"] == 0
    assert corpo["resumo"]["com_problema"] == 1
    assert corpo["arquivos"][0]["resultado"] == "nao_identificado"
    # E nada foi gravado.
    assert corpo["identificados"] == []


def test_codigo_de_outro_laudo_nao_pareia_com_o_paciente_errado(
    client, auth, db, caso
):
    """Código de um laudo real, mas de OUTRA paciente da mesma médica.

    O pareamento tem de seguir o código, não a proximidade — e o arquivo
    precisa cair no laudo cujo código ele carrega.
    """

    outro = _caso(
        client,
        auth,
        db,
        nome_paciente="TESTE APAGAR Maria de Souza",
        suffix="01b",
        doctor=caso["doctor"],
    )
    pdf = _pdf_com_texto(f"Laudo {outro['report_code']}")
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        # Nome sugere a PRIMEIRA paciente; o código é da SEGUNDA.
        files={
            "arquivos": (
                "TESTE APAGAR Antonio Lopes - Assinado.pdf",
                pdf,
                "application/pdf",
            )
        },
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    identificados = resposta.json()["identificados"]
    assert len(identificados) == 1
    # Seguiu o CÓDIGO, não o nome do arquivo.
    assert identificados[0]["report_code"] == outro["report_code"]


# =====================================================================
# 2. Download: 1 vira PDF, 2+ viram ZIP
# =====================================================================


def test_um_documento_sai_como_pdf_com_nome_humano(client, caso):
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "application/pdf"
    disposicao = resposta.headers["content-disposition"]
    assert "TESTE APAGAR Antonio Lopes - Para assinatura.pdf" in disposicao
    assert resposta.headers["cache-control"] == "private, no-store"


def test_dois_ou_mais_saem_num_zip_plano(client, auth, db, caso):
    segundo = _caso(
        client,
        auth,
        db,
        nome_paciente="TESTE APAGAR Maria de Souza",
        suffix="02",
        doctor=caso["doctor"],
    )
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"], segundo["document_id"]]},
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resposta.content)) as pacote:
        nomes = pacote.namelist()
    assert sorted(nomes) == sorted([
        "TESTE APAGAR Antonio Lopes - Para assinatura.pdf",
        "TESTE APAGAR Maria de Souza - Para assinatura.pdf",
    ])
    # Estrutura PLANA: nenhuma subpasta, nada além dos laudos.
    assert all("/" not in nome for nome in nomes)


def test_zip_contem_exatamente_os_selecionados(client, auth, db, caso):
    """Um terceiro laudo existe e NÃO foi escolhido — não pode entrar."""

    segundo = _caso(
        client, auth, db, nome_paciente="TESTE APAGAR Maria de Souza",
        suffix="03a", doctor=caso["doctor"],
    )
    _caso(
        client, auth, db, nome_paciente="TESTE APAGAR Carlos Santos",
        suffix="03b", doctor=caso["doctor"],
    )
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"], segundo["document_id"]]},
        headers=caso["doctor"]["auth"],
    )
    with zipfile.ZipFile(io.BytesIO(resposta.content)) as pacote:
        nomes = pacote.namelist()
    assert len(nomes) == 2
    assert not any("Carlos" in nome for nome in nomes)


def test_zip_nao_leva_mir_previa_nem_versao_antiga(client, caso):
    """Só o laudo concluído. MIR e prévia ficam de fora do pacote."""

    segundo_pdf = _pdf_com_texto("x")  # apenas para forçar 2 itens
    del segundo_pdf
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor"]["auth"],
    )
    # Um documento sai como PDF; o conteúdo é o laudo, não a MIR em branco.
    texto = "".join(
        p.extract_text() or ""
        for p in PdfReader(io.BytesIO(resposta.content)).pages
    )
    assert caso["report_code"] in texto


def test_nomes_unicode_sao_preservados_no_zip():
    """Acento e cedilha atravessam o ZIP intactos."""

    arquivos = [
        BatchFile(
            document_code="LAU-000001",
            patient_name="Conceição Ramalhão Júnior",
            pdf=_minimal_pdf(),
        ),
        BatchFile(
            document_code="LAU-000002",
            patient_name="Ana Álvares D'Ávila",
            pdf=_minimal_pdf(),
        ),
    ]
    with zipfile.ZipFile(io.BytesIO(build_batch_zip(arquivos))) as pacote:
        nomes = pacote.namelist()
    assert "Conceição Ramalhão Júnior - Para assinatura.pdf" in nomes
    assert "Ana Álvares D'Ávila - Para assinatura.pdf" in nomes


def test_homonimos_nao_se_sobrescrevem_no_zip():
    """Duas pacientes com o mesmo nome geram dois arquivos, não um."""

    arquivos = [
        BatchFile(
            document_code="LAU-000001",
            patient_name="Maria da Silva",
            pdf=_minimal_pdf(),
        ),
        BatchFile(
            document_code="LAU-000002",
            patient_name="Maria da Silva",
            pdf=_minimal_pdf(pages=2),
        ),
    ]
    with zipfile.ZipFile(io.BytesIO(build_batch_zip(arquivos))) as pacote:
        nomes = pacote.namelist()
    assert len(nomes) == 2
    assert len(set(nomes)) == 2
    assert any("LAU-000002" in nome for nome in nomes)


# =====================================================================
# 3. Isolamento entre médicos e cadastro de teste
# =====================================================================


def test_medica_b_nao_baixa_laudo_da_medica_a(client, auth, db, caso):
    outra_user, outra_auth = _physician(db, suffix="04")
    _configure_profile(
        client, auth, outra_user, crm="94004", name="TESTE APAGAR Médica B"
    )
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=outra_auth,
    )
    assert resposta.status_code == 409
    assert resposta.json()["erro"]["codigo"] == "lote_vazio"


def test_medica_b_nao_ve_pendencia_da_medica_a(client, auth, db, caso):
    outra_user, outra_auth = _physician(db, suffix="05")
    _configure_profile(
        client, auth, outra_user, crm="95005", name="TESTE APAGAR Médica B"
    )
    resposta = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes", headers=outra_auth
    )
    assert resposta.status_code == 200
    assert resposta.json()["total"] == 0


def test_arquivo_assinado_de_laudo_de_outra_medica_nao_e_pareado(
    client, auth, db, caso
):
    """Médica B devolve um arquivo cujo código é de um laudo da médica A."""

    outra_user, outra_auth = _physician(db, suffix="06")
    _configure_profile(
        client, auth, outra_user, crm="96006", name="TESTE APAGAR Médica B"
    )
    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("assinado.pdf", assinado, "application/pdf")},
        headers=outra_auth,
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"]["identificados"] == 0
    assert corpo["arquivos"][0]["resultado"] == "nao_identificado"


def test_cadastro_arquivado_nao_entra_na_central(client, auth, db, caso):
    """Cenário de teste arquivado nunca vai para assinatura com certificado."""

    from datetime import datetime, timezone

    pessoa = db.get(Person, caso["pessoa"]["id"])
    pessoa.arquivado = True
    pessoa.arquivado_em = datetime.now(timezone.utc)
    pessoa.arquivado_motivo = "cadastro sintético de teste"
    db.commit()

    resposta = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes",
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["total"] == 0


# =====================================================================
# 4. Upload: ZIP, zip slip, zip bomb, tipos
# =====================================================================


def test_upload_de_zip_com_pdfs_assinados(client, auth, db, caso):
    segundo = _caso(
        client, auth, db, nome_paciente="TESTE APAGAR Maria de Souza",
        suffix="07", doctor=caso["doctor"],
    )
    a = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    b = _assinar_por_fora(_baixar_versao_concluida(client, segundo))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pacote:
        pacote.writestr("assinados/Antonio - Assinado.pdf", a)
        pacote.writestr("assinados/Maria - Assinado.pdf", b)

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": ("assinados.zip", buffer.getvalue(), "application/zip")
        },
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["resumo"]["identificados"] == 2


def test_zip_slip_nao_escapa_de_diretorio():
    """Um membro com ../.. é reduzido ao nome final, nunca a um caminho."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pacote:
        pacote.writestr("../../../../etc/cron.d/malicioso.pdf", _minimal_pdf())
    relatorio = extract_signed_pdfs([("x.zip", buffer.getvalue())])
    assert len(relatorio.files) == 1
    assert relatorio.files[0].filename == "malicioso.pdf"
    assert "/" not in relatorio.files[0].filename
    assert ".." not in relatorio.files[0].filename


def test_zip_aninhado_e_recusado():
    interno = io.BytesIO()
    with zipfile.ZipFile(interno, "w") as pacote:
        pacote.writestr("a.pdf", _minimal_pdf())

    externo = io.BytesIO()
    with zipfile.ZipFile(externo, "w") as pacote:
        pacote.writestr("aninhado.pdf", interno.getvalue())

    relatorio = extract_signed_pdfs([("x.zip", externo.getvalue())])
    assert relatorio.files == []
    assert any("ZIP dentro de ZIP" in motivo for _n, motivo in relatorio.rejected)


def test_zip_bomb_e_recusado_pela_taxa_de_compressao():
    """Um membro que declara 50 MB vindos de poucos bytes não é aberto."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        pacote.writestr("bomba.pdf", b"\x00" * (50 * 1024 * 1024))
    relatorio = extract_signed_pdfs([("bomba.zip", buffer.getvalue())])
    assert relatorio.files == []
    assert relatorio.rejected


def test_limite_de_arquivos_por_lote():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pacote:
        for indice in range(MAX_ARQUIVOS_POR_LOTE + 5):
            pacote.writestr(f"{indice}.pdf", _minimal_pdf())
    relatorio = extract_signed_pdfs([("muitos.zip", buffer.getvalue())])
    assert relatorio.files == []
    assert relatorio.rejected


def test_arquivo_que_nao_e_pdf_e_recusado(client, caso):
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": ("planilha.xlsx", b"PK\x03\x04not-a-zip-really", "x")
        },
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"]["identificados"] == 0
    assert corpo["arquivos"][0]["resultado"] == "recusado"


def test_executavel_dentro_do_zip_e_recusado():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pacote:
        pacote.writestr("malware.exe", b"MZ\x90\x00binario")
        pacote.writestr("valido.pdf", _minimal_pdf())
    relatorio = extract_signed_pdfs([("misto.zip", buffer.getvalue())])
    assert [f.filename for f in relatorio.files] == ["valido.pdf"]
    assert any("malware.exe" == nome for nome, _m in relatorio.rejected)


def test_diretorios_do_zip_sao_ignorados():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pacote:
        pacote.writestr("pasta/", b"")
        pacote.writestr("pasta/a.pdf", _minimal_pdf())
    relatorio = extract_signed_pdfs([("x.zip", buffer.getvalue())])
    assert [f.filename for f in relatorio.files] == ["a.pdf"]


# =====================================================================
# 5. Conferência, confirmação e idempotência
# =====================================================================


def test_conferencia_nao_grava_antes_da_confirmacao(client, auth, caso, db):
    """Depois do upload o documento existe, mas ainda EM CONFERÊNCIA.

    E, principalmente, a administração ainda NÃO o vê como recebido: ela não
    deve começar a trabalhar num arquivo que a médica pode descartar na tela
    seguinte.
    """

    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == ASSINADO_EM_CONFERENCIA
    assert registro.confirmed_at is None

    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    )
    assert fila.status_code == 200, fila.text
    linha = next(
        item for item in fila.json()["itens"]
        if item["document_id"] == caso["document_id"]
    )
    assert linha["estado"] == "aguardando_assinatura"


def test_confirmacao_unica_move_o_lote_inteiro(client, auth, db, caso):
    segundo = _caso(
        client, auth, db, nome_paciente="TESTE APAGAR Maria de Souza",
        suffix="08", doctor=caso["doctor"],
    )
    a = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    b = _assinar_por_fora(_baixar_versao_concluida(client, segundo))

    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files=[
            ("arquivos", ("a.pdf", a, "application/pdf")),
            ("arquivos", ("b.pdf", b, "application/pdf")),
        ],
        headers=caso["doctor"]["auth"],
    )
    assert envio.status_code == 200, envio.text
    corpo = envio.json()
    assert corpo["resumo"]["identificados"] == 2

    confirmacao = client.post(
        "/api/v1/laudos/assinatura-externa/confirmar",
        json={
            "batch_id": corpo["batch_id"],
            "signed_document_ids": [
                item["signed_document_id"] for item in corpo["identificados"]
            ],
        },
        headers=caso["doctor"]["auth"],
    )
    assert confirmacao.status_code == 200, confirmacao.text
    assert confirmacao.json()["confirmados"] == 2

    registros = db.execute(select(ExternalSignedDocument)).scalars().all()
    assert {r.status for r in registros} == {
        ASSINADO_RECEBIDO_VALIDACAO_PENDENTE
    }


def test_reenvio_do_mesmo_arquivo_e_idempotente(client, caso, db):
    """Mandar o lote duas vezes não cria duas versões do mesmo documento."""

    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    primeiro = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    corpo = primeiro.json()
    client.post(
        "/api/v1/laudos/assinatura-externa/confirmar",
        json={
            "batch_id": corpo["batch_id"],
            "signed_document_ids": [
                corpo["identificados"][0]["signed_document_id"]
            ],
        },
        headers=caso["doctor"]["auth"],
    )

    segundo = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a-de-novo.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    assert segundo.status_code == 200, segundo.text
    assert segundo.json()["arquivos"][0]["resultado"] == "ja_recebido"

    total = db.execute(select(ExternalSignedDocument)).scalars().all()
    assert len(total) == 1


def test_falha_de_gravacao_nao_leva_o_lote_junto(client, auth, db, caso):
    """Um arquivo que falha AO GRAVAR não pode arrastar o resto do lote.

    A gravação de cada arquivo roda numa transação que faz rollback da
    SESSÃO inteira quando falha. Se o registro do lote existisse apenas em
    `flush()`, o primeiro arquivo hostil o apagaria — e o arquivo válido
    seguinte tentaria gravar apontando para um lote inexistente.

    O hostil aqui é realista: carrega o código do laudo (então PAREIA) e
    JavaScript embutido (então é recusado pelo validador de PDF).
    """

    segundo = _caso(
        client, auth, db, nome_paciente="TESTE APAGAR Maria de Souza",
        suffix="09", doctor=caso["doctor"],
    )
    bom = _assinar_por_fora(_baixar_versao_concluida(client, segundo))

    # PDF com o código do PRIMEIRO laudo e conteúdo ativo.
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(_minimal_pdf())))
    writer.add_metadata({"/SoproLifeReportCode": caso["report_code"]})
    writer.add_js("app.alert('x');")
    hostil = io.BytesIO()
    writer.write(hostil)

    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files=[
            # O hostil vem PRIMEIRO, de propósito.
            ("arquivos", ("hostil.pdf", hostil.getvalue(), "application/pdf")),
            ("arquivos", ("bom.pdf", bom, "application/pdf")),
        ],
        headers=caso["doctor"]["auth"],
    )
    assert envio.status_code == 200, envio.text
    corpo = envio.json()
    assert corpo["resumo"]["identificados"] == 1, corpo
    assert corpo["resumo"]["com_problema"] == 1
    assert corpo["identificados"][0]["report_code"] == segundo["report_code"]

    # E o lote confirma normalmente — a FK não ficou órfã.
    confirmacao = client.post(
        "/api/v1/laudos/assinatura-externa/confirmar",
        json={
            "batch_id": corpo["batch_id"],
            "signed_document_ids": [
                corpo["identificados"][0]["signed_document_id"]
            ],
        },
        headers=caso["doctor"]["auth"],
    )
    assert confirmacao.status_code == 200, confirmacao.text
    assert confirmacao.json()["confirmados"] == 1


def test_um_arquivo_ruim_nao_derruba_o_lote(client, auth, db, caso):
    bom = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    ruim = _pdf_com_texto("Documento sem nenhum codigo")

    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files=[
            ("arquivos", ("bom.pdf", bom, "application/pdf")),
            ("arquivos", ("ruim.pdf", ruim, "application/pdf")),
        ],
        headers=caso["doctor"]["auth"],
    )
    assert envio.status_code == 200, envio.text
    corpo = envio.json()
    assert corpo["resumo"]["identificados"] == 1
    assert corpo["resumo"]["com_problema"] == 1


# =====================================================================
# 6. Armazenamento: versão nova, nada sobrescrito
# =====================================================================


def test_versao_original_e_o_laudo_concluido_sao_preservados(
    client, caso, db
):
    """O assinado que volta é versão NOVA — nada é sobrescrito."""

    antes = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == caso["document_id"]
        )
    ).scalars().all()
    tipos_antes = {v.kind: v.sha256 for v in antes}
    # Fecha a transação de leitura: em SQLite ela bloquearia a escrita que a
    # requisição seguinte precisa fazer.
    db.rollback()

    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )

    depois = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == caso["document_id"]
        )
    ).scalars().all()
    tipos_depois = {v.kind: v.sha256 for v in depois}

    # A MIR original e o laudo concluído seguem com o MESMO hash.
    assert tipos_antes["original"] == tipos_depois["original"]
    assert tipos_antes["laudo_liberado"] == tipos_depois["laudo_liberado"]
    # E existe uma versão nova, separada.
    assert "laudo_assinado_externo_recebido" in tipos_depois
    assert len(depois) == len(antes) + 1


def test_medica_baixa_o_assinado_com_nome_de_assinado(client, caso, db):
    """Pela rota da médica, o assinado NÃO pode sair "Para assinatura.pdf".

    Dois arquivos diferentes com o mesmo nome na pasta de downloads é
    exatamente como se leva o documento errado para assinar de novo.
    """

    _receber_e_confirmar(client, caso)
    versao = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == caso["document_id"],
            ReportDocumentVersion.kind == "laudo_assinado_externo_recebido",
        )
    ).scalars().one()
    version_id = versao.id
    # O download grava auditoria; a transação de leitura acima bloquearia
    # essa escrita no SQLite dos testes.
    db.rollback()

    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}/versoes/{version_id}/conteudo"
        "?modo=download",
        headers=caso["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    disposicao = resposta.headers["content-disposition"]
    assert "TESTE APAGAR Antonio Lopes - Assinado.pdf" in disposicao
    assert "Para assinatura" not in disposicao


def test_sha256_e_tamanho_do_recebido_sao_gravados(client, caso, db):
    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("recebido.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.sha256 == hashlib.sha256(assinado).hexdigest()
    assert registro.size_bytes == len(assinado)
    assert registro.received_filename == "recebido.pdf"
    assert registro.source_sha256 is not None
    assert registro.batch_id


def test_pdf_assinado_com_campo_de_assinatura_e_aceito():
    """Um PDF com /AcroForm de assinatura tem de passar na validação.

    Sem esta exceção, NENHUM PDF assinado voltaria — o perfil fechado
    recusaria justamente o arquivo que o fluxo existe para receber.
    """

    from app.services.report_pades import prepare_pades

    base = _pdf_com_texto("Laudo sintetico")
    preparado = prepare_pades(base, reason="teste", location="teste").data

    with pytest.raises(InvalidPdfError):
        validate_pdf_bytes(preparado, max_size_bytes=25 * 1024 * 1024)

    aceito = validate_pdf_bytes(
        preparado, max_size_bytes=25 * 1024 * 1024, allow_signature_form=True
    )
    assert aceito.page_count >= 1


def test_ciclo_completo_com_campo_de_assinatura_real(client, auth, caso):
    """O caso REALISTA, ponta a ponta.

    Anexar bytes ao fim do PDF prova pouco: um assinador de verdade cria um
    `/AcroForm` com o campo de assinatura, que é justamente o que o perfil
    fechado de validação recusa. Este teste faz o ciclo inteiro — baixar,
    ganhar campo de assinatura, devolver, parear, gravar e confirmar — com
    um PDF que tem o aparato de assinatura de fato.
    """

    from app.services.report_pades import prepare_pades

    concluido = _baixar_versao_concluida(client, caso)
    assinado = prepare_pades(concluido, reason="teste", location="teste").data
    assert assinado != concluido

    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("assinado-vidaas.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    assert envio.status_code == 200, envio.text
    corpo = envio.json()
    assert corpo["resumo"]["identificados"] == 1, corpo
    assert corpo["identificados"][0]["pareado_por"] == PAREAMENTO_METADADO

    confirmacao = client.post(
        "/api/v1/laudos/assinatura-externa/confirmar",
        json={
            "batch_id": corpo["batch_id"],
            "signed_document_ids": [
                corpo["identificados"][0]["signed_document_id"]
            ],
        },
        headers=caso["doctor"]["auth"],
    )
    assert confirmacao.status_code == 200, confirmacao.text

    # E o arquivo gravado é RELIDO sem falhar — o perfil de gravação e o de
    # releitura precisam concordar, senão o PDF nasceria "corrompido" e o
    # download administrativo devolveria 409 logo depois de gravar.
    baixado = client.get(
        f"/api/v1/laudos/{caso['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert baixado.status_code == 200, baixado.text
    assert baixado.content == assinado


def test_javascript_continua_recusado_mesmo_com_a_excecao_de_assinatura():
    """A exceção é do tamanho do aparato de assinatura, e de mais nada."""

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_js("app.alert('x');")
    saida = io.BytesIO()
    writer.write(saida)

    with pytest.raises(InvalidPdfError):
        validate_pdf_bytes(
            saida.getvalue(),
            max_size_bytes=25 * 1024 * 1024,
            allow_signature_form=True,
        )


# =====================================================================
# 7. Honestidade sobre ICP-Brasil
# =====================================================================


def test_upload_nunca_declara_assinatura_qualificada(client, caso, db):
    """Receber não é validar — em nenhum campo, em nenhuma resposta."""

    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    )
    corpo = envio.json()
    confirmacao = client.post(
        "/api/v1/laudos/assinatura-externa/confirmar",
        json={
            "batch_id": corpo["batch_id"],
            "signed_document_ids": [
                corpo["identificados"][0]["signed_document_id"]
            ],
        },
        headers=caso["doctor"]["auth"],
    )
    assert confirmacao.status_code == 200

    texto = (envio.text + confirmacao.text).lower()
    assert "icp-brasil" not in texto
    assert "qualified_signature\": true" not in texto

    # O documento clínico NÃO virou "assinado": continua concluído.
    documento = db.get(ReportDocument, caso["document_id"])
    assert documento.status == "liberado"
    assert documento.signed_at is None
    assert documento.signature_status == "liberada_institucional"


def test_estado_do_recebido_declara_validacao_pendente(client, caso, db):
    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    ).json()
    client.post(
        "/api/v1/laudos/assinatura-externa/confirmar",
        json={
            "batch_id": envio["batch_id"],
            "signed_document_ids": [
                envio["identificados"][0]["signed_document_id"]
            ],
        },
        headers=caso["doctor"]["auth"],
    )
    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == ASSINADO_RECEBIDO_VALIDACAO_PENDENTE
    assert registro.validated_at is None
    assert registro.validated_by_user_id is None


# =====================================================================
# 8. Fila administrativa, validação externa e downloads
# =====================================================================


def _receber_e_confirmar(client, caso_) -> str:
    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso_))
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a.pdf", assinado, "application/pdf")},
        headers=caso_["doctor"]["auth"],
    ).json()
    signed_id = envio["identificados"][0]["signed_document_id"]
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/confirmar",
        json={"batch_id": envio["batch_id"], "signed_document_ids": [signed_id]},
        headers=caso_["doctor"]["auth"],
    )
    assert resposta.status_code == 200, resposta.text
    return signed_id


def test_fila_administrativa_transiciona_sozinha(client, auth, caso):
    """A médica devolve; a administração vê. Sem WhatsApp no meio."""

    antes = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    )
    assert antes.status_code == 200, antes.text
    linha = next(
        item for item in antes.json()["itens"]
        if item["document_id"] == caso["document_id"]
    )
    assert linha["estado"] == "aguardando_assinatura"

    _receber_e_confirmar(client, caso)

    depois = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    ).json()
    linha = next(
        item for item in depois["itens"]
        if item["document_id"] == caso["document_id"]
    )
    assert linha["estado"] == "assinado_recebido_validacao_pendente"
    assert linha["assinado"]["assinatura_verificada_criptograficamente"] is False


def test_validacao_externa_exige_confirmacao_consciente(client, auth, caso):
    signed_id = _receber_e_confirmar(client, caso)
    recusado = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={"metodo": "validar_iti", "confirmacao": "ok"},
        headers=auth("admin"),
    )
    assert recusado.status_code == 422


def test_validacao_externa_registra_quem_e_como(client, auth, caso, db):
    signed_id = _receber_e_confirmar(client, caso)
    resposta = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={
            "metodo": "validar_iti",
            "confirmacao": VALIDACAO_CONFIRMACAO,
            "referencia": "protocolo-iti-sintetico-001",
        },
        headers=auth("admin"),
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["estado"] == "pronto_para_entrega"
    # A resposta diz honestamente o que NÃO foi feito.
    assert "não realizou" in resposta.json()["observacao"]

    registro = db.get(ExternalSignedDocument, signed_id)
    assert registro.status == ASSINADO_VALIDADO_EXTERNAMENTE
    assert registro.validated_by_user_id is not None
    assert registro.validated_at is not None
    assert registro.validation_method == "validar_iti"


def test_validacao_externa_nao_aceita_documento_em_conferencia(
    client, auth, caso, db
):
    assinado = _assinar_por_fora(_baixar_versao_concluida(client, caso))
    envio = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": ("a.pdf", assinado, "application/pdf")},
        headers=caso["doctor"]["auth"],
    ).json()
    signed_id = envio["identificados"][0]["signed_document_id"]

    resposta = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={"metodo": "validar_iti", "confirmacao": VALIDACAO_CONFIRMACAO},
        headers=auth("admin"),
    )
    assert resposta.status_code == 409
    assert resposta.json()["erro"]["codigo"] == "conferencia_da_medica_pendente"


def test_entrega_so_depois_da_conferencia(client, auth, caso):
    signed_id = _receber_e_confirmar(client, caso)
    cedo = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/entrega",
        headers=auth("operacional"),
    )
    assert cedo.status_code == 409

    client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={"metodo": "validar_iti", "confirmacao": VALIDACAO_CONFIRMACAO},
        headers=auth("admin"),
    )
    entregue = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/entrega",
        headers=auth("operacional"),
    )
    assert entregue.status_code == 200, entregue.text
    assert entregue.json()["estado"] == "entregue"


def test_admin_baixa_o_assinado_com_nome_humano(client, auth, caso):
    _receber_e_confirmar(client, caso)
    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    assert (
        "TESTE APAGAR Antonio Lopes - Assinado.pdf"
        in resposta.headers["content-disposition"]
    )


def test_admin_baixa_o_exame_tecnico_com_nome_humano(client, auth, caso):
    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}/exame-tecnico/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    assert (
        "TESTE APAGAR Antonio Lopes - Exame" in
        resposta.headers["content-disposition"]
    )


def test_download_administrativo_do_assinado_404_antes_de_receber(
    client, auth, caso
):
    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 404


# =====================================================================
# 9. Privacidade e auditoria
# =====================================================================


def test_auditoria_nao_registra_nome_de_paciente_nem_de_arquivo(
    client, caso, db
):
    _receber_e_confirmar(client, caso)
    registros = db.execute(select(AuditLog)).scalars().all()
    texto = " ".join(str(r.detalhes or {}) for r in registros)
    assert "Antonio" not in texto
    assert ".pdf" not in texto


def test_rota_publica_de_validacao_nao_expoe_dado_novo(client, auth, caso):
    """Receber um assinado não pode acrescentar NADA a esta rota.

    A comparação é entre o antes e o depois: o conjunto de campos precisa
    ser idêntico. Assim o teste pega um campo novo que vaze por aqui, sem
    congelar a lista de campos que a M25.2 já publicava.
    """

    codigo = caso["concluido"]["validation_code"]
    antes = client.get(
        f"/api/v1/laudos/validacao/{codigo}", headers=auth("leitura")
    )
    assert antes.status_code == 200, antes.text
    campos_antes = set(antes.json())

    _receber_e_confirmar(client, caso)

    depois = client.get(
        f"/api/v1/laudos/validacao/{codigo}", headers=auth("leitura")
    )
    assert depois.status_code == 200, depois.text
    assert set(depois.json()) == campos_antes
    # E nenhum dado de paciente entrou junto.
    assert "Antonio" not in depois.text
    assert depois.json()["qualified_signature"] is False


# =====================================================================
# 10. iPhone / mobile — sem depender de drag-and-drop
# =====================================================================


def _bloco_upload_da_central() -> str:
    """O trecho exato do input de devolução, isolado do resto do arquivo.

    Sem recortar, um `assert "multiple" in WORKFLOW_JS` passaria por causa
    do input da M25.8, que fica em outro bloco — e o teste diria "sim" para
    uma central que não tem input nenhum.
    """

    inicio = WORKFLOW_JS.index("reportSignatureUpload")
    trecho = WORKFLOW_JS[inicio - 400:inicio + 400]
    assert "data-signature-upload" in trecho
    return trecho


def test_upload_aceita_multiplos_pdfs_e_zip_no_input():
    """O input precisa aceitar as DUAS formas que o iPhone oferece."""

    bloco = _bloco_upload_da_central()
    assert 'type="file"' in bloco
    assert "multiple" in bloco
    assert "application/pdf" in bloco
    assert ".pdf" in bloco
    assert "application/zip" in bloco
    assert ".zip" in bloco


def test_nao_ha_caminho_exclusivo_de_drag_and_drop():
    """Arrastar pode existir como complemento; nunca como único caminho."""

    assert "data-signature-upload" in WORKFLOW_JS, (
        "a central precisa de um input de seleção de arquivos"
    )
    # A central não implementa drop: se um dia implementar, o input acima
    # tem de continuar existindo.
    for gatilho in ("dragover", "dragenter", "ondrop"):
        if gatilho in WORKFLOW_JS:
            assert "data-signature-upload" in WORKFLOW_JS


def test_selecao_acontece_na_linha_inteira_e_nao_num_quadradinho():
    """Alvo de toque de iPhone: `label` embrulha o input, não um `div`."""

    assert "<label class=\"report-signature-item" in WORKFLOW_JS
    assert "data-signature-pick" in WORKFLOW_JS


def test_central_tem_layout_responsivo_de_iphone():
    """CSS PRÓPRIO da central — não o da assinatura manuscrita da M25.2."""

    assert ".report-signature-item {" in WORKFLOW_CSS
    assert ".report-signature-upload-label {" in WORKFLOW_CSS
    assert ".report-signature-review {" in WORKFLOW_CSS
    # Alvos de toque com altura mínima e quebra em largura de iPhone.
    assert "min-height: 44px" in WORKFLOW_CSS
    assert "@media (max-width: 480px)" in WORKFLOW_CSS
    recorte = WORKFLOW_CSS[WORKFLOW_CSS.index(".report-signature-item {"):]
    assert "flex-direction: column" in recorte


def test_central_mostra_vazio_com_texto_claro():
    assert "Nenhum laudo aguardando assinatura." in WORKFLOW_JS


def test_fila_administrativa_tem_tela_e_nao_so_rota():
    """Os cinco estados precisam existir NA TELA da administração (§15)."""

    assert "function renderDeliveryQueue(" in WORKFLOW_JS
    assert "/laudos/assinatura-externa/fila" in WORKFLOW_JS
    assert "renderDeliveryQueue()" in WORKFLOW_JS
    # Os dois downloads do §17 e as duas ações do §16/§18.
    #
    # M25.29E — os downloads deixaram de ser `<a download href>` e passaram a
    # usar `apiBlob`, que exige `application/pdf` e transforma erro em
    # mensagem. O caminho agora é montado a partir do argumento, então o
    # contrato passa a ser: existem os dois botões e existe a chamada.
    assert "data-delivery-download-mir" in WORKFLOW_JS
    assert "data-delivery-download-assinado" in WORKFLOW_JS
    assert '"exame-tecnico"' in WORKFLOW_JS
    assert "/conteudo`" in WORKFLOW_JS
    assert "data-delivery-validate" in WORKFLOW_JS
    assert "data-delivery-deliver" in WORKFLOW_JS
    assert ".report-delivery-row {" in WORKFLOW_CSS


def test_tela_administrativa_declara_que_nao_validou_criptograficamente():
    """A negativa do §14 fica VISÍVEL, não só no JSON da API."""

    assert (
        "a SoproLife não verificou a assinatura criptograficamente"
        in WORKFLOW_JS
    )


def test_registro_de_validacao_externa_exige_frase_digitada():
    """Um clique distraído não pode virar testemunho de conferência."""

    assert "Confirmo a conferência externa" in WORKFLOW_JS
    assert "window.prompt(" in WORKFLOW_JS
    assert "NÃO valida a cadeia ICP-Brasil" in WORKFLOW_JS


def test_proxy_deixa_passar_os_nomes_novos():
    """O nome do ZIP e do assinado precisam atravessar o proxy do painel.

    A M25.18 rastreou um download com nome aleatório até a allowlist deste
    proxy descartando o `Content-Disposition` inteiro. Nomes novos —
    principalmente o do ZIP, que tem espaços e uma data — precisam ser
    conferidos aqui, e não descobertos em produção.
    """

    import importlib.util
    from datetime import date

    from app.services.download_names import content_disposition
    from app.services.signature_batch import (
        batch_zip_filename,
        signing_filename,
    )

    caminho = PANEL_ROOT / "scripts" / "command-center-local-server.py"
    spec = importlib.util.spec_from_file_location("_ccls_m2520", caminho)
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)

    nomes = [
        batch_zip_filename(generated_on=date(2026, 8, 10)),
        signing_filename("ANTONIO LOPES DA SILVA", fallback_code="LAU-000021"),
        signing_filename("Conceição Ramalhão Júnior", fallback_code="LAU-1"),
        "ANTONIO LOPES DA SILVA - Assinado.pdf",
        "ANTONIO LOPES DA SILVA - Exame técnico.pdf",
    ]
    for nome in nomes:
        cabecalho = content_disposition(nome, disposition="attachment")
        assert proxy._safe_content_disposition(cabecalho) == cabecalho, nome


def test_central_e_alimentada_pelo_servidor_e_nao_pelo_navegador():
    """A lista de pendências vem da rota dedicada, não de um filtro local."""

    assert "/laudos/assinatura-externa/pendentes" in WORKFLOW_JS
    assert "/laudos/assinatura-externa/baixar" in WORKFLOW_JS
    assert "/laudos/assinatura-externa/enviar" in WORKFLOW_JS
    assert "/laudos/assinatura-externa/confirmar" in WORKFLOW_JS


def test_confirmacao_e_um_passo_separado_da_conferencia():
    """Nada é gravado no upload: a médica confirma depois de ver a lista."""

    assert "data-signature-confirm" in WORKFLOW_JS
    assert "Confirmar ${identificados} identificado(s)" in WORKFLOW_JS
    assert "data-signature-discard" in WORKFLOW_JS
