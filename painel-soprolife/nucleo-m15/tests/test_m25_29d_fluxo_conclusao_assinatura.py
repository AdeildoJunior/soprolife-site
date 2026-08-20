"""M25.29D — concluir uma vez, e só o documento final pode ser assinado.

O incidente que originou estes testes é real e não tem nada de exótico: a
médica chegou ao assinador externo com um PDF que ainda dizia "PRÉVIA —
DOCUMENTO NÃO CONCLUÍDO", assinou, e o sistema não tinha uma única camada
capaz de dizer não. O fluxo pedia dois botões deliberados antes da
confirmação, o arquivo de prévia baixava com o mesmo nome do documento
final ("<Nome> - Para assinatura.pdf") e o retorno assinado era pareado pelo
código LAU impresso — que a prévia também carrega.

O que estes testes travam:

1. **Prévia não é assinável.** Nem pelo endpoint que prepara o download para
   assinatura, nem na volta, mesmo com assinatura por cima.
2. **O nome do arquivo não mente.** Prévia baixa como
   ``<Nome> - PREVIA - NAO ASSINAR.pdf``; só o concluído é "Para assinatura".
3. **Uma confirmação, uma só.** E cancelá-la preserva o rascunho.
4. **O MIR continua separado, o histórico continua imutável, e a conclusão
   clínica escrita pela médica não muda sozinha.**

Todos os pacientes, médicas, CRMs e PDFs são sintéticos.
"""

from __future__ import annotations

import io
import pathlib
import re

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AuditLog,
    ExternalSignedDocument,
    ReportDocument,
    ReportDocumentVersion,
    User,
)
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)
from app.services.download_names import SUFIXO_LAUDO, SUFIXO_PREVIA
from app.services.report_native_pdf import (
    PREVIEW_DO_NOT_SIGN,
    PREVIEW_WATERMARK,
)
from app.services.signature_batch import (
    ESTADO_CONCLUIDO,
    ESTADO_PREVIA,
    looks_like_preview,
    read_markers_from_metadata,
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


def _assinar_por_fora(pdf: bytes) -> bytes:
    """Assinatura externa sintética: uma revisão ANEXADA, como no PAdES.

    Um assinador real não reescreve o documento — ele acrescenta bytes. É
    justamente por isso que a tarja de prévia continua no papel e o metadado
    continua legível depois de assinado.
    """

    # M25.29H — a revisão anexada carrega um dicionário de assinatura de
    # verdade. Antes era só um comentário, e o teste passava por um caminho
    # que a produção não tem: as guardas documentais exigem `/ByteRange` e
    # `/Sig`, exatamente para separar um PDF assinado de um PDF devolvido
    # sem assinar. Sem esta estrutura a fixture simulava algo que o sistema
    # — corretamente — recusa.
    return pdf + (
        b"\n% revisao incremental de assinatura (sintetica)\n"
        b"9999 0 obj\n"
        b"<< /Type /Sig /Filter /Adobe.PPKLite"
        b" /SubFilter /ETSI.CAdES.detached\n"
        b"   /ByteRange [0 0 0 0] /Contents <00> >>\n"
        b"endobj\n"
    )


def _texto_do_pdf(pdf: bytes) -> str:
    leitor = PdfReader(io.BytesIO(pdf))
    return "".join(pagina.extract_text() or "" for pagina in leitor.pages)


def _sem_acento(texto: str) -> str:
    import unicodedata

    decomposto = unicodedata.normalize("NFKD", texto)
    sem = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem).upper()


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
        "m25-29d-conclusao-assinatura-secret-de-teste-0123456789",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _physician(db, *, suffix: str) -> tuple[User, dict]:
    ensure_roles_exist(db)
    user = User(
        email=f"medica-m25-29d-{suffix}@teste.local",
        nome=f"TESTE APAGAR Médica {suffix}",
        password_hash=hash_password("senha-medica-sintetica-m2529d"),
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
            "verification_reference": "CRM-VERIF-TESTE-M2529D",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["profile"]


def _caso_em_elaboracao(client, auth, db, *, nome_paciente, suffix="01"):
    """Um laudo atribuído, com prévia gerada — o estado do incidente."""

    user, doctor_auth = _physician(db, suffix=suffix)
    profile = _configure_profile(
        client,
        auth,
        user,
        crm=f"9{suffix}29",
        name=f"TESTE APAGAR Médica {suffix}",
    )
    pessoa = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": nome_paciente},
        headers=auth("operacional"),
    )
    assert pessoa.status_code == 201, pessoa.text
    pessoa = pessoa.json()

    atendimento = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-08-14",
                "status": "Realizado",
                "broncodilatador": True,
            },
        },
        headers=auth("operacional"),
    )
    assert atendimento.status_code == 201, atendimento.text
    exame = atendimento.json()["espirometria"]

    mir = _minimal_pdf()
    documento = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exame["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-teste-m2529d",
        },
        files={"file": ("TESTE-APAGAR.pdf", mir, "application/pdf")},
        headers=auth("operacional"),
    )
    assert documento.status_code == 201, documento.text
    documento = documento.json()

    previa = client.post(
        f"/api/v1/laudos/{documento['id']}/laudo/previa",
        json={
            "conclusion_code": "DVO_MODERADO",
            "bronchodilator_code": "RBD_POSITIVO",
        },
        headers=doctor_auth,
    )
    assert previa.status_code == 200, previa.text

    return {
        "doctor_auth": doctor_auth,
        "profile": profile,
        "pessoa": pessoa,
        "exame": exame,
        "document_id": documento["id"],
        "report_code": documento["public_code"],
        "mir_bytes": mir,
        "previa": previa.json(),
    }


def _concluir(client, caso) -> dict:
    corpo = caso["previa"]
    resposta = client.post(
        f"/api/v1/laudos/{caso['document_id']}/assinar-e-liberar",
        json={
            "confirmacao": RELEASE_CONFIRMATION,
            "expected_version_id": corpo["preview_version_id"],
            "expected_text_sha256": corpo["final_text_sha256"],
        },
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _baixar_versao(client, caso, version_id) -> "object":
    return client.get(
        f"/api/v1/laudos/{caso['document_id']}/versoes/{version_id}/conteudo"
        "?modo=download",
        headers=caso["doctor_auth"],
    )


@pytest.fixture()
def caso(client, auth, db):
    return _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Beatriz Andrade"
    )


# =====================================================================
# 1. A prévia não chega ao assinador
# =====================================================================


def test_previa_nao_pode_ser_baixada_para_assinatura(client, caso):
    """Item 1 — o endpoint "para assinatura" recusa laudo não concluído.

    A trava não pode viver no botão: quem chama a API direto (ou uma tela
    desatualizada aberta em outra aba) precisa receber a mesma recusa.
    """

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 409, resposta.text
    corpo = resposta.json()["erro"]
    assert corpo["codigo"] == "laudo_ainda_em_previa"
    assert "prévia" in corpo["mensagem"].lower()
    assert "conclua o laudo" in corpo["mensagem"].lower()
    # E — o que de fato importa — nenhum byte de PDF saiu.
    assert resposta.headers["content-type"].startswith("application/json")


def test_previa_nao_contamina_um_lote_com_laudo_concluido(
    client, auth, db, caso
):
    """Um id de prévia na seleção para o pedido INTEIRO.

    Antes, o id não elegível era descartado em silêncio: se sobrasse um
    concluído na lista, o download acontecia e a médica ficava com a
    impressão de que tudo o que ela marcou tinha vindo.
    """

    outro = _caso_em_elaboracao(
        client,
        auth,
        db,
        nome_paciente="TESTE APAGAR Carla Nogueira",
        suffix="02",
    )
    # O segundo caso é da MESMA médica? Não — cada um tem a sua. Para o
    # teste valer, concluímos o segundo e pedimos os dois pela médica dele.
    _concluir(client, outro)
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [outro["document_id"], caso["document_id"]]},
        headers=outro["doctor_auth"],
    )
    # O laudo em prévia é de OUTRA médica: continua invisível para esta, e o
    # download do que é dela acontece normalmente.
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "application/pdf"

    # Já para a própria médica do laudo em prévia, o pedido para.
    recusa = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert recusa.status_code == 409, recusa.text
    assert recusa.json()["erro"]["codigo"] == "laudo_ainda_em_previa"


def test_previa_baixa_com_nome_inequivoco(client, caso):
    """Item 2 — ``<Nome> - PREVIA - NAO ASSINAR.pdf``.

    O nome do arquivo é a última coisa lida antes de arrastá-lo para o
    assinador, e era a que mentia mais alto: a prévia baixava com o mesmo
    "- Para assinatura.pdf" do documento final.
    """

    resposta = _baixar_versao(
        client, caso, caso["previa"]["preview_version_id"]
    )
    assert resposta.status_code == 200, resposta.text
    disposicao = resposta.headers["content-disposition"]
    assert SUFIXO_PREVIA in disposicao
    assert "NAO ASSINAR" in disposicao
    assert SUFIXO_LAUDO not in disposicao
    assert "Beatriz Andrade" in disposicao


def test_previa_imprime_e_carimba_que_nao_deve_ser_assinada(client, caso):
    """A folha diz, e o arquivo diz. As duas leituras são independentes."""

    resposta = _baixar_versao(
        client, caso, caso["previa"]["preview_version_id"]
    )
    assert resposta.status_code == 200
    pdf = resposta.content

    texto = _sem_acento(_texto_do_pdf(pdf))
    assert _sem_acento(PREVIEW_WATERMARK) in texto
    assert "NAO ASSINAR" in texto
    assert _sem_acento(PREVIEW_DO_NOT_SIGN)[:30] in texto

    assert read_markers_from_metadata(pdf).document_state == ESTADO_PREVIA
    assert looks_like_preview(pdf) is True


def test_laudo_concluido_nunca_parece_previa(client, caso):
    """O contrário também precisa valer, ou o fluxo inteiro trava.

    Se um documento concluído fosse lido como prévia, a médica não
    conseguiria devolver o assinado de nada.
    """

    _concluir(client, caso)
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    pdf = resposta.content
    assert read_markers_from_metadata(pdf).document_state == ESTADO_CONCLUIDO
    assert looks_like_preview(pdf) is False
    assert "NAO ASSINAR" not in _sem_acento(_texto_do_pdf(pdf))


# =====================================================================
# 2. A conclusão: uma confirmação, e o que acontece depois
# =====================================================================


def test_cancelar_a_confirmacao_preserva_o_rascunho(client, caso):
    """Item 4 — desistir não pode custar o trabalho já digitado.

    "Cancelar" é uma decisão de tela: nada é enviado. O que se prova aqui é
    que o estado do servidor depois de gerar a prévia continua editável e
    com a conclusão intacta.
    """

    detalhe = client.get(
        f"/api/v1/laudos/{caso['document_id']}", headers=caso["doctor_auth"]
    ).json()
    assert detalhe["status"] == "em_elaboracao"

    texto_previa = caso["previa"]["final_text"]
    assert texto_previa.strip()

    nativa = [
        v for v in detalhe["versoes"] if v["kind"] == "laudo_previa"
    ][-1]
    assert nativa["interpretation_text_snapshot"] == texto_previa
    assert nativa["conclusion_code_snapshot"] == "DVO_MODERADO"


def test_confirmar_gera_versao_final_aguardando_assinatura(client, caso):
    """Itens 5 e 6 — a confirmação congela a versão e muda o estado."""

    liberado = _concluir(client, caso)
    assert liberado["qualified_signature"] is False
    assert liberado["validation_code"]

    detalhe = client.get(
        f"/api/v1/laudos/{caso['document_id']}", headers=caso["doctor_auth"]
    ).json()
    assert detalhe["status"] == "liberado"

    pendentes = client.get(
        "/api/v1/laudos/assinatura-externa/pendentes",
        headers=caso["doctor_auth"],
    ).json()
    assert [
        linha["report_code"] for linha in pendentes["laudos"]
    ] == [caso["report_code"]]


def test_versao_final_baixa_com_o_nome_de_assinatura(client, caso):
    """Itens 7 e 8 — o documento final é baixável e se chama pelo que é."""

    _concluir(client, caso)
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    disposicao = resposta.headers["content-disposition"]
    assert SUFIXO_LAUDO in disposicao
    assert "PREVIA" not in disposicao.upper()


def test_conclusao_clinica_nao_muda_no_caminho(client, caso):
    """Item 12 — o texto assinado é, byte a byte, o texto conferido."""

    texto_conferido = caso["previa"]["final_text"]
    sha_conferido = caso["previa"]["final_text_sha256"]
    liberado = _concluir(client, caso)

    final = [
        v for v in liberado["versoes"] if v["kind"] == "laudo_liberado"
    ][-1]
    assert final["interpretation_text_snapshot"] == texto_conferido
    assert final["interpretation_text_sha256"] == sha_conferido
    assert final["conclusion_code_snapshot"] == "DVO_MODERADO"


def _versoes(client, caso) -> list[dict]:
    detalhe = client.get(
        f"/api/v1/laudos/{caso['document_id']}", headers=caso["doctor_auth"]
    )
    assert detalhe.status_code == 200, detalhe.text
    return detalhe.json()["versoes"]


def test_historico_permanece_imutavel(client, caso):
    """Item 13 — concluir ACRESCENTA. A prévia continua onde estava."""

    previa_id = caso["previa"]["preview_version_id"]
    antes = {v["id"]: v["sha256"] for v in _versoes(client, caso)}
    assert previa_id in antes

    _concluir(client, caso)

    depois = {v["id"]: v["sha256"] for v in _versoes(client, caso)}
    # Nenhuma versão anterior sumiu nem teve o conteúdo trocado.
    for version_id, sha in antes.items():
        assert depois.get(version_id) == sha

    kinds = sorted(v["kind"] for v in _versoes(client, caso))
    assert kinds == ["laudo_liberado", "laudo_previa", "original"]


# =====================================================================
# 3. O retorno assinado
# =====================================================================


def test_assinado_da_versao_final_entra_no_fluxo(client, caso):
    """Item 9 — o caminho feliz continua funcionando."""

    _concluir(client, caso)
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assinado = _assinar_por_fora(baixado.content)

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": (
                "TESTE APAGAR - assinado.pdf", assinado, "application/pdf"
            )
        },
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["resumo"]["identificados"] == 1
    assert corpo["resumo"]["com_problema"] == 0
    assert corpo["identificados"][0]["report_code"] == caso["report_code"]


def test_assinado_de_previa_e_rejeitado(client, caso, db):
    """Item 10 — o coração da etapa.

    Este é EXATAMENTE o incidente: o PDF assinado por fora é a prévia. Ele
    carrega o mesmo código LAU impresso do documento final, então o
    pareamento acerta o laudo — e é justamente por isso que a recusa precisa
    ser explícita. Assinatura por cima não conserta a folha errada.
    """

    previa = _baixar_versao(
        client, caso, caso["previa"]["preview_version_id"]
    ).content
    _concluir(client, caso)  # o laudo JÁ está concluído e elegível

    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": (
                "TESTE APAGAR - Para assinatura.pdf",
                _assinar_por_fora(previa),
                "application/pdf",
            )
        },
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["identificados"] == []
    assert corpo["resumo"]["identificados"] == 0
    assert corpo["resumo"]["com_problema"] == 1

    (veredito,) = corpo["arquivos"]
    assert veredito["resultado"] == "recusado"
    assert "PRÉVIA" in veredito["mensagem"]
    assert "assine o pdf final" in veredito["mensagem"].lower()

    # Nada foi gravado como documento assinado.
    assert db.execute(select(ExternalSignedDocument)).scalars().all() == []
    # E nenhuma versão nova nasceu do arquivo recusado.
    versoes = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == caso["document_id"]
        )
    ).scalars().all()
    assert all(
        v.kind != "laudo_assinado_externo_recebido" for v in versoes
    )


def test_recusa_por_previa_e_contada_na_auditoria(client, caso, db):
    """O incidente precisa ser contável sem abrir arquivo nenhum."""

    previa = _baixar_versao(
        client, caso, caso["previa"]["preview_version_id"]
    ).content
    _concluir(client, caso)
    client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": ("x.pdf", _assinar_por_fora(previa), "application/pdf")
        },
        headers=caso["doctor_auth"],
    )

    registro = db.execute(
        select(AuditLog).where(
            AuditLog.acao == "laudos_assinados_recebidos"
        )
    ).scalars().all()[-1]
    assert registro.detalhes["recusadas_por_previa"] == 1
    # A auditoria continua sem nome de paciente e sem nome de arquivo.
    serializado = str(registro.detalhes)
    assert "Beatriz" not in serializado
    assert ".pdf" not in serializado


def test_upload_de_previa_nao_vira_documento_final_mesmo_sem_metadado(caso):
    """Segunda camada: a tarja impressa, sozinha, já denuncia a prévia.

    Um assinador que reescreva o dicionário de metadados apagaria o carimbo.
    O texto do documento continua dizendo o que ele é.
    """

    from pypdf import PdfWriter as _Writer

    # Reconstrói o PDF SEM metadados — o pior caso para o carimbo.
    pdf_previa = _minimal_pdf()
    escritor = _Writer(clone_from=PdfReader(io.BytesIO(pdf_previa)))
    buffer = io.BytesIO()
    escritor.write(buffer)
    assert looks_like_preview(buffer.getvalue()) is False  # controle

    from reportlab.pdfgen import canvas as _canvas

    saida = io.BytesIO()
    c = _canvas.Canvas(saida)
    c.drawString(72, 760, "PREVIA - DOCUMENTO NAO CONCLUIDO")
    c.drawString(72, 740, "LAU-000999")
    c.save()
    assert looks_like_preview(saida.getvalue()) is True


# =====================================================================
# 4. O que NÃO pode ter mudado
# =====================================================================


def test_mir_permanece_separado_e_intacto(client, caso):
    """Item 11 — o PDF do equipamento não é tocado, fundido nem renomeado."""

    _concluir(client, caso)
    documentos = client.get(
        f"/api/v1/laudos/{caso['document_id']}/documentos",
        headers=caso["doctor_auth"],
    ).json()
    original_id = documentos["tecnico_mir"]["version_id"]

    resposta = _baixar_versao(client, caso, original_id)
    assert resposta.status_code == 200
    assert resposta.content == caso["mir_bytes"]
    assert "cnico" in resposta.headers["content-disposition"]  # "Exame técnico"

    assert documentos["tecnico_mir"]["assinavel"] is False
    assert documentos["laudo_soprolife"]["version_id"] != original_id
    assert documentos["tecnico_mir"]["kind"] == "original"


def test_documentos_dizem_o_que_e_previa(client, caso):
    """A tela não precisa conhecer `kind` para saber o que é assinável."""

    antes = client.get(
        f"/api/v1/laudos/{caso['document_id']}/documentos",
        headers=caso["doctor_auth"],
    ).json()
    assert antes["laudo_soprolife"]["previa"] is True
    assert antes["laudo_soprolife"]["assinavel"] is False

    _concluir(client, caso)
    depois = client.get(
        f"/api/v1/laudos/{caso['document_id']}/documentos",
        headers=caso["doctor_auth"],
    ).json()
    assert depois["laudo_soprolife"]["previa"] is False
    assert depois["laudo_soprolife"]["assinavel"] is True


def test_auditoria_do_caso_descreve_sem_vazar_pii(client, caso, db, capsys):
    """Item 18 — a ferramenta de investigação não imprime PII.

    O script é o que se roda em PRODUÇÃO para responder "a prévia assinada
    voltou ao sistema?". Uma auditoria que despeja nome de paciente na tela
    (e no terminal de quem a executa, e no relatório colado depois) troca um
    incidente por outro.
    """

    import importlib.util
    from pathlib import Path

    # `scripts/` não é pacote (é um diretório de executáveis), então o módulo
    # é carregado pelo caminho — do mesmo jeito que ele roda na VPS.
    caminho = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "auditar_caso_laudo.py"
    )
    spec = importlib.util.spec_from_file_location("auditar_caso_laudo", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    auditar = modulo.auditar

    _concluir(client, caso)
    codigo = auditar(db, report_code=caso["report_code"], exam_code=None)
    assert codigo == 0

    saida = capsys.readouterr().out
    # Descreve o caso...
    assert caso["report_code"] in saida
    assert "laudo_liberado" in saida
    assert "NÃO. Nenhum arquivo assinado" in saida
    # ...sem dizer de quem ele é.
    assert "Beatriz" not in saida
    assert "Andrade" not in saida


def test_medica_continua_sem_financeiro_e_sem_admin(client, caso):
    """Item 14 — o gate da M25.23 não foi afrouxado por esta etapa."""

    for rota in (
        "/api/v1/financeiro/resumo",
        "/api/v1/admin/usuarios",
        "/api/v1/laudos/admin/medicos",
    ):
        resposta = client.get(rota, headers=caso["doctor_auth"])
        assert resposta.status_code in (403, 404), (rota, resposta.status_code)


def test_administrador_continua_funcional(client, auth, caso):
    """Item 15 — a operação administrativa segue vendo o laudo."""

    lista = client.get("/api/v1/laudos", headers=auth("operacional"))
    assert lista.status_code == 200, lista.text
    codigos = [item["report_code"] for item in lista.json()]
    assert caso["report_code"] in codigos


# =====================================================================
# 5. Contrato de tela — uma confirmação, e nenhuma palavra interna
# =====================================================================


# Os comentários do próprio arquivo citam os rótulos ANTIGOS para explicar o
# que mudou e por quê. Uma varredura de texto cru os encontraria e diria que
# o botão velho continua lá — por isso o código é lido sem comentários.
_COMENTARIO_BLOCO = re.compile(r"/\*.*?\*/", re.S)
_COMENTARIO_LINHA = re.compile(r"^\s*//.*$", re.M)


def _js_sem_comentarios() -> str:
    sem_bloco = _COMENTARIO_BLOCO.sub("", WORKFLOW_JS)
    return _COMENTARIO_LINHA.sub("", sem_bloco)


def test_uma_unica_confirmacao_de_conclusao():
    """Item 3 — o fluxo tem UM ponto de confirmação, e um texto só."""

    codigo = _js_sem_comentarios()
    assert "Concluir este laudo?" in codigo
    assert "Confira as conclusões antes de continuar." in codigo
    assert "Voltar e revisar" in codigo
    # UM lugar desenha o botão de confirmar, UM lugar o trata.
    assert codigo.count("data-report-release-confirm") == 2
    # O passo intermediário sumiu: não há mais um botão que só gera prévia
    # como ação principal do formulário, nem uma segunda pergunta depois.
    assert "Gerar prévia do laudo" not in codigo
    assert "Sim, concluir laudo" not in codigo


def test_acao_principal_leva_direto_a_confirmacao():
    assert "Concluir e preparar para assinatura" in WORKFLOW_JS
    assert "previewNativeReport({ concluir: true })" in WORKFLOW_JS
    assert "data-report-preview-only" in WORKFLOW_JS
    assert "Só conferir a prévia" in WORKFLOW_JS


def test_tela_de_conclusao_mostra_o_proximo_passo():
    assert "✓ Laudo concluído" in WORKFLOW_JS
    assert "Agora baixe o PDF final para assinatura." in WORKFLOW_JS
    assert "Baixar PDF para assinar" in WORKFLOW_JS
    assert "data-report-download-final" in WORKFLOW_JS


def test_tela_medica_nao_usa_vocabulario_interno():
    """Item de UX — a médica não lê `kind`, `batch` nem `release`.

    A varredura é sobre o TEXTO visível dos blocos novos desta etapa; nomes
    de atributo e de função continuam em inglês, como no resto do arquivo.
    """

    visiveis = re.findall(r">([^<>{}]{6,})<", WORKFLOW_JS)
    proibidos = ("qualified_signature", "kind ", "batch ", " release ")
    for trecho in visiveis:
        baixo = trecho.lower()
        for termo in proibidos:
            assert termo not in baixo, trecho


def _bloco_media(consulta: str, *, ancora: str = "") -> str:
    """O corpo de uma `@media`, delimitado por contagem de chaves.

    Fatiar por `"}\\n}"` dependia da formatação da última regra dentro do
    bloco — quebrava ao acrescentar uma regra nova, que é exatamente o que
    esta etapa faz. `ancora` escolhe QUAL ocorrência da consulta interessa:
    a mesma largura aparece em vários pontos da folha.
    """

    desde = WORKFLOW_CSS.index(ancora) if ancora else 0
    inicio = WORKFLOW_CSS.index(consulta, desde)
    abertura = WORKFLOW_CSS.index("{", inicio)
    profundidade = 0
    for posicao in range(abertura, len(WORKFLOW_CSS)):
        if WORKFLOW_CSS[posicao] == "{":
            profundidade += 1
        elif WORKFLOW_CSS[posicao] == "}":
            profundidade -= 1
            if profundidade == 0:
                return WORKFLOW_CSS[abertura + 1:posicao]
    raise AssertionError(f"bloco não fechado: {consulta}")


def test_mobile_iphone_empilha_os_botoes_da_conclusao():
    """Item 16 — 430px: nada lado a lado, nada fora da viewport."""

    assert ".report-native-actions" in WORKFLOW_CSS
    assert ".report-conclude-cta" in WORKFLOW_CSS
    assert ".report-download-final" in WORKFLOW_CSS

    # Há mais de uma `@media (max-width: 720px)` no arquivo; a desta etapa
    # é a que vem depois do bloco da liberação consciente.
    bloco = _bloco_media(
        "@media (max-width: 720px)", ancora=".report-release-confirm {"
    )
    assert ".report-native-actions" in bloco
    assert "flex-direction: column" in bloco
    assert ".report-release-buttons" in bloco
    assert ".report-conclude-cta" in bloco
    assert "width: 100%" in bloco


def test_botoes_tem_area_de_toque_adequada():
    """Sem hover, sem precisão de mouse: alvos grandes de verdade."""

    for seletor, minimo in (
        (".report-conclude-cta", 52),
        (".report-preview-only", 52),
        (".report-download-final", 56),
        # Medido no navegador a 430px: os dois botões da confirmação vinham
        # com 33px, herdados do `.m15-btn` genérico. É a decisão mais
        # consequente da tela, em duas faixas finas coladas uma na outra.
        (".report-release-buttons .m15-btn", 48),
    ):
        bloco = WORKFLOW_CSS.split(seletor + " {")[1].split("}")[0]
        achado = re.search(r"min-height:\s*(\d+)px", bloco)
        assert achado is not None, seletor
        assert int(achado.group(1)) >= minimo, seletor
