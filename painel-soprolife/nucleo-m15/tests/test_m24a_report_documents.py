"""M24A — laudos PDF: upload seguro, RBAC, ciclo de vida, entrega autenticada.

Cobre os casos mandatórios do pedido: nome de arquivo hostil, PDF
malformado/criptografado/oversized, permissão/atomicidade de armazenamento,
RBAC, vínculo com exame, transições de ciclo de vida, imutabilidade do
finalizado, fluxo de versão corretiva, preview/composição, ausência de PII/
caminho de sistema de arquivos em respostas e logs, entrega autenticada.
"""

import asyncio
import hashlib
import io
import os
from pathlib import Path
import stat
import zipfile

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.config import get_settings
from app.errors import ReportDomainError, REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES
from app.models import AuditLog
from app.routers.reports import _read_upload_bounded

SYNTH_TEXT = "TESTE - APAGAR: texto sintetico nao clinico para teste automatizado."


def _minimal_pdf(pages: int = 1, width: int = 595, height: int = 842) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=height)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _encrypted_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt(user_password="", owner_password="dono-teste")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reports_storage(monkeypatch, tmp_path):
    """autouse: precisa terminar ANTES de `client`/`tokens` construírem o
    segredo de autenticação efêmero (get_settings().resolved_auth_secret()),
    senão limpar o cache de settings no meio do teste troca o segredo sob os
    tokens já emitidos e todo request autenticado passa a devolver 401.
    Fixtures autouse instanciam antes das explicitamente requisitadas no
    mesmo escopo — por isso isto funciona mesmo quando outro teste não lista
    `reports_storage` nos parâmetros."""
    storage_dir = tmp_path / "laudos-storage"
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    # Segredo FIXO (não efêmero): alguns testes chamam cache_clear() de novo
    # NO MEIO do teste (para trocar tamanho máximo/remover a raiz) — com
    # segredo efêmero isso rotacionaria a chave sob os tokens já emitidos.
    monkeypatch.setenv("M15_AUTH_SECRET", "m24a-teste-segredo-fixo-nao-usar-em-producao-0123456789")
    get_settings.cache_clear()
    yield storage_dir
    get_settings.cache_clear()


@pytest.fixture()
def exam(client, auth, person):
    resp = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {"data_exame": "2026-07-01", "status": "Realizado"},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["espirometria"]


@pytest.fixture()
def template(client, auth):
    resp = client.post(
        "/api/v1/laudos/templates",
        json={
            "codigo": "TESTE-01",
            "titulo": "TESTE - APAGAR",
            "texto_tooltip": "TESTE - APAGAR tooltip",
            "texto_completo": SYNTH_TEXT,
            "ativo": True,
        },
        headers=auth("admin"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload(client, auth, exam_id, data, filename="laudo.pdf", role="operacional", content_type="application/pdf"):
    return client.post(
        "/api/v1/laudos",
        data={"exam_id": exam_id},
        files={"file": (filename, data, content_type)},
        headers=auth(role),
    )


# --------------------------------------------------------------- validação


def test_upload_pdf_valido_cria_documento_rascunho(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], _minimal_pdf())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "rascunho"
    assert body["spirometry_exam_id"] == exam["id"]
    assert "storage_path" not in resp.text


def test_upload_nome_hostil_nunca_vira_caminho(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], _minimal_pdf(), filename="../../../etc/passwd.pdf")
    assert resp.status_code == 201, resp.text
    assert "original_filename" not in resp.text
    assert "passwd.pdf" not in resp.text
    # nenhum arquivo escapou da raiz configurada
    for root, _dirs, files in os.walk(reports_storage):
        for f in files:
            full = os.path.join(root, f)
            assert os.path.commonpath([os.path.abspath(full), str(reports_storage)]) == str(reports_storage)
            assert ".." not in full


def test_upload_nome_com_caracteres_controle(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], _minimal_pdf(), filename="laudo\x00\x1b.pdf")
    assert resp.status_code == 201, resp.text
    assert "original_filename" not in resp.text


def test_upload_texto_puro_rejeitado(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], b"isto nao e um pdf de verdade")
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "assinatura_invalida"
    assert isinstance(resp.json()["erro"]["mensagem"], str)


def test_upload_pdf_truncado_rejeitado(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], b"%PDF-1.4\n1 0 obj\n<<")
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "pdf_malformado"


def test_upload_pdf_criptografado_rejeitado(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], _encrypted_pdf())
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "pdf_criptografado"


def test_upload_polyglot_pdf_zip_rejeitado(client, auth, exam, reports_storage):
    buffer = io.BytesIO(_minimal_pdf())
    with zipfile.ZipFile(buffer, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("teste.txt", "conteudo sintetico")
    polyglot = buffer.getvalue()
    resp = _upload(client, auth, exam["id"], polyglot)
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "arquivo_polyglot_suspeito"


def test_quatro_bytes_zip_isolados_nao_geram_falso_polyglot(
    client, auth, exam, reports_storage
):
    pdf = _minimal_pdf() + b"\n% marcador inocuo PK\x03\x04 sem estrutura ZIP\n"
    resp = _upload(client, auth, exam["id"], pdf)
    assert resp.status_code == 201, resp.text


def test_upload_oversized_rejeitado(client, auth, exam, reports_storage, monkeypatch):
    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    try:
        resp = _upload(client, auth, exam["id"], _minimal_pdf())
        assert resp.status_code == 422
        assert resp.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"
    finally:
        get_settings.cache_clear()


def test_leitura_limitada_para_em_maximo_mais_um_byte():
    class TrackingUpload:
        def __init__(self):
            self.data = b"x" * 10_000
            self.position = 0

        async def read(self, size):
            start = self.position
            self.position = min(len(self.data), start + size)
            return self.data[start : self.position]

    upload = TrackingUpload()
    with pytest.raises(ReportDomainError) as caught:
        asyncio.run(_read_upload_bounded(upload, max_size_bytes=100))
    assert caught.value.codigo == "pdf_excede_tamanho_maximo"
    assert upload.position == 101


def test_validador_nao_recebe_upload_oversized(
    client, auth, exam, reports_storage, monkeypatch
):
    import app.routers.reports as reports_router

    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    calls = []

    def must_not_validate(data, **_kwargs):
        calls.append(len(data))
        raise AssertionError("validador não deveria receber o excedente")

    monkeypatch.setattr(reports_router, "validate_pdf_bytes", must_not_validate)
    try:
        resp = _upload(client, auth, exam["id"], b"x" * 10_000)
        assert resp.status_code == 422
        assert resp.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"
        assert calls == []
    finally:
        get_settings.cache_clear()


def test_content_length_declarado_oversized_e_recusado_antes_do_validador(
    client, auth, exam, reports_storage, monkeypatch
):
    import app.routers.reports as reports_router

    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(
        reports_router,
        "validate_pdf_bytes",
        lambda data, **kwargs: calls.append(len(data)),
    )
    try:
        resp = client.post(
            "/api/v1/laudos",
            data={"exam_id": exam["id"]},
            files={"file": ("teste.pdf", _minimal_pdf(), "application/pdf")},
            headers={
                **auth("operacional"),
                "Content-Length": str(100 + REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES + 1),
            },
        )
        assert resp.status_code == 413
        assert resp.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"
        assert calls == []
    finally:
        get_settings.cache_clear()


def test_content_type_malformado_nao_contorna_limite_declarado(
    client, auth, reports_storage, monkeypatch
):
    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/v1/laudos",
            content=b"x",
            headers={
                **auth("operacional"),
                "Content-Type": "application/octet-stream",
                "Content-Length": str(
                    100 + REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES + 1
                ),
            },
        )
        assert response.status_code == 413
        assert response.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"
    finally:
        get_settings.cache_clear()


def test_stream_multipart_sem_content_length_tambem_e_limitado_antes_do_parser(
    client, auth, exam, reports_storage, monkeypatch
):
    import app.routers.reports as reports_router

    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(
        reports_router,
        "validate_pdf_bytes",
        lambda data, **kwargs: calls.append(len(data)),
    )
    boundary = "M24A-BOUNDARY-TESTE"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="exam_id"\r\n\r\n'
        f"{exam['id']}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="teste.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + b"x" * (REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES + 1000) + (
        f"\r\n--{boundary}--\r\n"
    ).encode()

    def stream():
        for offset in range(0, len(body), 4096):
            yield body[offset : offset + 4096]

    try:
        response = client.post(
            "/api/v1/laudos",
            content=stream(),
            headers={
                **auth("operacional"),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        assert response.status_code == 413, response.text
        assert response.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"
        assert calls == []
    finally:
        get_settings.cache_clear()


def test_upload_com_exatamente_o_maximo_configurado_e_aceito(
    client, auth, exam, reports_storage, monkeypatch
):
    data = _minimal_pdf()
    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", str(len(data)))
    get_settings.cache_clear()
    try:
        resp = _upload(client, auth, exam["id"], data)
        assert resp.status_code == 201, resp.text
        assert resp.json()["versoes"][0]["size_bytes"] == len(data)
    finally:
        get_settings.cache_clear()


def test_upload_exame_inexistente(client, auth, reports_storage):
    resp = _upload(client, auth, "00000000-0000-0000-0000-000000000000", _minimal_pdf())
    assert resp.status_code == 404


# --------------------------------------------------------------------- RBAC


def test_upload_exige_operacional_ou_acima(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], _minimal_pdf(), role="leitura")
    assert resp.status_code == 403


def test_criar_template_exige_admin(client, auth, exam, reports_storage):
    for role in ("gestor", "operacional", "leitura"):
        resp = client.post(
            "/api/v1/laudos/templates",
            json={"codigo": f"NEG-{role[:3].upper()}", "titulo": "x", "texto_completo": ""},
            headers=auth(role),
        )
        assert resp.status_code == 403, role


def test_finalizar_exige_gestor_ou_admin(client, auth, exam, reports_storage, template):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc_id = upload.json()["id"]
    client.post(
        f"/api/v1/laudos/{doc_id}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "rodape"},
        headers=auth("operacional"),
    )
    client.post(f"/api/v1/laudos/{doc_id}/revisao", headers=auth("operacional"))

    for role in ("operacional", "leitura"):
        resp = client.post(f"/api/v1/laudos/{doc_id}/finalizar", headers=auth(role))
        assert resp.status_code == 403, role

    resp = client.post(f"/api/v1/laudos/{doc_id}/finalizar", headers=auth("gestor"))
    assert resp.status_code == 200


def test_download_exige_autenticacao(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc = upload.json()
    version_id = doc["current_version_id"]
    resp = client.get(f"/api/v1/laudos/{doc['id']}/versoes/{version_id}/conteudo")
    assert resp.status_code == 401


# ---------------------------------------------------------------- vínculo


def test_documento_vinculado_ao_exame_correto(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc = upload.json()
    resp = client.get(f"/api/v1/laudos?exam_id={exam['id']}", headers=auth("leitura"))
    ids = [d["id"] for d in resp.json()]
    assert doc["id"] in ids


# --------------------------------------------------------- ciclo de vida


def _full_lifecycle_to_review(client, auth, exam, template):
    upload = _upload(client, auth, exam["id"], _minimal_pdf(pages=2))
    doc_id = upload.json()["id"]
    compose = client.post(
        f"/api/v1/laudos/{doc_id}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "rodape"},
        headers=auth("operacional"),
    )
    assert compose.status_code == 200, compose.text
    review = client.post(f"/api/v1/laudos/{doc_id}/revisao", headers=auth("operacional"))
    assert review.status_code == 200, review.text
    return doc_id


def test_ciclo_de_vida_completo(client, auth, exam, reports_storage, template):
    doc_id = _full_lifecycle_to_review(client, auth, exam, template)
    resp = client.get(f"/api/v1/laudos/{doc_id}", headers=auth("leitura"))
    assert resp.json()["status"] == "em_revisao"

    final = client.post(f"/api/v1/laudos/{doc_id}/finalizar", headers=auth("gestor"))
    assert final.status_code == 200
    body = final.json()
    assert body["status"] == "finalizado"
    assert body["signature_status"] == "assinatura_pendente"
    assert body["finalized_by_user_id"] is not None


def test_compor_exige_status_rascunho(client, auth, exam, reports_storage, template):
    doc_id = _full_lifecycle_to_review(client, auth, exam, template)  # já em em_revisao
    resp = client.post(
        f"/api/v1/laudos/{doc_id}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "topo"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 409


def test_revisao_exige_rascunho_composto(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc_id = upload.json()["id"]
    resp = client.post(f"/api/v1/laudos/{doc_id}/revisao", headers=auth("operacional"))
    assert resp.status_code == 409


def test_finalizar_exige_em_revisao(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc_id = upload.json()["id"]
    resp = client.post(f"/api/v1/laudos/{doc_id}/finalizar", headers=auth("gestor"))
    assert resp.status_code == 409
    resp = client.post(
        f"/api/v1/laudos/{doc_id}/devolver-para-ajuste",
        json={"reason_code": "correcao_tecnica"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 409


def test_gestor_devolve_revisao_para_ajuste_com_motivo_tecnico_auditado(
    client, auth, exam, reports_storage, template, db
):
    document_id = _full_lifecycle_to_review(client, auth, exam, template)
    forbidden = client.post(
        f"/api/v1/laudos/{document_id}/devolver-para-ajuste",
        json={"reason_code": "ajuste_de_composicao"},
        headers=auth("operacional"),
    )
    assert forbidden.status_code == 403

    free_text = client.post(
        f"/api/v1/laudos/{document_id}/devolver-para-ajuste",
        json={"reason_code": "texto livre com dado"},
        headers=auth("gestor"),
    )
    assert free_text.status_code == 422

    returned = client.post(
        f"/api/v1/laudos/{document_id}/devolver-para-ajuste",
        json={"reason_code": "ajuste_de_pagina"},
        headers=auth("gestor"),
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "rascunho"

    db.expire_all()
    event = db.execute(
        select(AuditLog).where(
            AuditLog.acao == "laudo_devolvido_para_ajuste",
            AuditLog.entidade_id == document_id,
        )
    ).scalar_one()
    assert event.detalhes == {
        "status": "rascunho",
        "reason_code": "ajuste_de_pagina",
    }


# ----------------------------------------- reprodução independente H-2


def test_finalizacao_recusa_rascunho_valido_substituido_no_storage(
    client, auth, exam, reports_storage, template
):
    doc_id = _full_lifecycle_to_review(client, auth, exam, template)
    detail = client.get(f"/api/v1/laudos/{doc_id}", headers=auth("leitura")).json()
    draft = next(version for version in detail["versoes"] if version["kind"] == "rascunho")
    stored = Path(
        reports_storage, "laudos", exam["id"], doc_id, f"{draft['id']}.pdf"
    )
    replacement = _minimal_pdf(pages=1, width=400, height=400)
    assert replacement != stored.read_bytes()
    stored.write_bytes(replacement)

    response = client.post(
        f"/api/v1/laudos/{doc_id}/finalizar",
        headers=auth("gestor"),
    )
    assert response.status_code == 409


def test_corretiva_recusa_original_substituido_por_lixo(
    client, auth, exam, reports_storage, template
):
    doc_id = _full_lifecycle_to_review(client, auth, exam, template)
    finalized = client.post(
        f"/api/v1/laudos/{doc_id}/finalizar",
        headers=auth("gestor"),
    )
    assert finalized.status_code == 200, finalized.text
    detail = client.get(f"/api/v1/laudos/{doc_id}", headers=auth("leitura")).json()
    original = next(version for version in detail["versoes"] if version["kind"] == "original")
    stored = Path(
        reports_storage, "laudos", exam["id"], doc_id, f"{original['id']}.pdf"
    )
    stored.write_bytes(b"NAO E PDF")

    response = client.post(
        f"/api/v1/laudos/{doc_id}/nova-versao-corretiva",
        headers=auth("operacional"),
    )
    assert response.status_code == 409


def test_compor_trata_arquivo_original_ausente_com_erro_de_dominio(
    client, auth, exam, reports_storage, template
):
    uploaded = _upload(client, auth, exam["id"], _minimal_pdf()).json()
    original = uploaded["versoes"][0]
    stored = Path(
        reports_storage,
        "laudos",
        exam["id"],
        uploaded["id"],
        f"{original['id']}.pdf",
    )
    stored.unlink()
    response = client.post(
        f"/api/v1/laudos/{uploaded['id']}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "topo"},
        headers=auth("operacional"),
    )
    assert response.status_code == 409
    assert response.json()["erro"]["codigo"] == "arquivo_laudo_ausente"
    assert str(reports_storage) not in response.text


def test_compor_trata_arquivo_original_corrompido_com_erro_de_dominio(
    client, auth, exam, reports_storage, template
):
    uploaded = _upload(client, auth, exam["id"], _minimal_pdf()).json()
    original = uploaded["versoes"][0]
    stored = Path(
        reports_storage,
        "laudos",
        exam["id"],
        uploaded["id"],
        f"{original['id']}.pdf",
    )
    stored.write_bytes(b"%PDF-corrompido")
    response = client.post(
        f"/api/v1/laudos/{uploaded['id']}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "topo"},
        headers=auth("operacional"),
    )
    assert response.status_code == 409
    assert response.json()["erro"]["codigo"] == "pdf_armazenado_invalido"


def test_finalizar_trata_rascunho_ausente_com_erro_de_dominio(
    client, auth, exam, reports_storage, template
):
    document_id = _full_lifecycle_to_review(client, auth, exam, template)
    detail = client.get(
        f"/api/v1/laudos/{document_id}", headers=auth("leitura")
    ).json()
    draft = next(version for version in detail["versoes"] if version["kind"] == "rascunho")
    Path(
        reports_storage,
        "laudos",
        exam["id"],
        document_id,
        f"{draft['id']}.pdf",
    ).unlink()
    response = client.post(
        f"/api/v1/laudos/{document_id}/finalizar",
        headers=auth("gestor"),
    )
    assert response.status_code == 409
    assert response.json()["erro"]["codigo"] == "arquivo_laudo_ausente"


def test_corretiva_trata_original_ausente_com_erro_de_dominio(
    client, auth, exam, reports_storage, template
):
    document_id = _full_lifecycle_to_review(client, auth, exam, template)
    assert client.post(
        f"/api/v1/laudos/{document_id}/finalizar",
        headers=auth("gestor"),
    ).status_code == 200
    detail = client.get(
        f"/api/v1/laudos/{document_id}", headers=auth("leitura")
    ).json()
    original = next(version for version in detail["versoes"] if version["kind"] == "original")
    Path(
        reports_storage,
        "laudos",
        exam["id"],
        document_id,
        f"{original['id']}.pdf",
    ).unlink()
    response = client.post(
        f"/api/v1/laudos/{document_id}/nova-versao-corretiva",
        headers=auth("operacional"),
    )
    assert response.status_code == 409
    assert response.json()["erro"]["codigo"] == "arquivo_laudo_ausente"


# ------------------------------------------------- imutabilidade / correção


def test_finalizado_e_imutavel(client, auth, exam, reports_storage, template):
    doc_id = _full_lifecycle_to_review(client, auth, exam, template)
    client.post(f"/api/v1/laudos/{doc_id}/finalizar", headers=auth("gestor"))

    # nenhuma composição, revisão ou finalização nova é aceita
    resp = client.post(
        f"/api/v1/laudos/{doc_id}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "topo"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 409
    resp = client.post(f"/api/v1/laudos/{doc_id}/revisao", headers=auth("operacional"))
    assert resp.status_code == 409
    resp = client.post(f"/api/v1/laudos/{doc_id}/finalizar", headers=auth("gestor"))
    assert resp.status_code == 409


def test_versao_corretiva_nao_muta_finalizado(client, auth, exam, reports_storage, template):
    doc_id = _full_lifecycle_to_review(client, auth, exam, template)
    finalize = client.post(f"/api/v1/laudos/{doc_id}/finalizar", headers=auth("gestor"))
    original_final_body = client.get(f"/api/v1/laudos/{doc_id}", headers=auth("leitura")).json()

    corrective = client.post(f"/api/v1/laudos/{doc_id}/nova-versao-corretiva", headers=auth("operacional"))
    assert corrective.status_code == 201
    new_doc = corrective.json()
    assert new_doc["status"] == "rascunho"
    assert new_doc["id"] != doc_id
    assert new_doc["corrects_document_id"] == doc_id

    after = client.get(f"/api/v1/laudos/{doc_id}", headers=auth("leitura")).json()
    assert after["status"] == "finalizado"
    assert after["superseded_by_id"] == new_doc["id"]
    assert after["finalized_at"] == original_final_body["finalized_at"]
    assert after["current_version_id"] == original_final_body["current_version_id"]

    # segunda correção sobre o mesmo documento já superado é recusada
    resp = client.post(f"/api/v1/laudos/{doc_id}/nova-versao-corretiva", headers=auth("operacional"))
    assert resp.status_code == 409


# ------------------------------------------------------------ composição


def test_compor_pagina_fora_do_intervalo(client, auth, exam, reports_storage, template):
    upload = _upload(client, auth, exam["id"], _minimal_pdf(pages=1))
    doc_id = upload.json()["id"]
    resp = client.post(
        f"/api/v1/laudos/{doc_id}/compor",
        json={"template_id": template["id"], "page_number": 5, "placement": "topo"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "pagina_invalida"


def test_compor_template_inativo_e_recusado(client, auth, exam, reports_storage, template):
    client.patch(
        f"/api/v1/laudos/templates/{template['id']}", json={"ativo": False}, headers=auth("admin")
    )
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc_id = upload.json()["id"]
    resp = client.post(
        f"/api/v1/laudos/{doc_id}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "topo"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 404


def test_template_versao_incrementa_ao_mudar_texto(client, auth, template):
    resp = client.patch(
        f"/api/v1/laudos/templates/{template['id']}",
        json={"texto_completo": SYNTH_TEXT + " v2"},
        headers=auth("admin"),
    )
    assert resp.status_code == 200
    assert resp.json()["versao"] == 2


def test_snapshot_do_template_permanece_imutavel_apos_edicao(
    client, auth, exam, reports_storage, template
):
    document_id = _upload(client, auth, exam["id"], _minimal_pdf()).json()["id"]
    old_text = SYNTH_TEXT
    composed = client.post(
        f"/api/v1/laudos/{document_id}/compor",
        json={"template_id": template["id"], "page_number": 1, "placement": "topo"},
        headers=auth("operacional"),
    )
    assert composed.status_code == 200, composed.text
    draft = composed.json()["versoes"][0]
    assert draft["template_code_snapshot"] == template["codigo"]
    assert draft["template_version_snapshot"] == 1
    assert draft["template_text_snapshot"] == old_text
    assert draft["template_text_sha256"] == hashlib.sha256(
        old_text.encode("utf-8")
    ).hexdigest()

    new_text = "TESTE - APAGAR: segunda versão sintética do modelo."
    updated = client.patch(
        f"/api/v1/laudos/templates/{template['id']}",
        json={"texto_completo": new_text},
        headers=auth("admin"),
    )
    assert updated.status_code == 200
    assert updated.json()["versao"] == 2

    detail = client.get(
        f"/api/v1/laudos/{document_id}", headers=auth("leitura")
    ).json()
    persisted_draft = next(
        version for version in detail["versoes"] if version["id"] == draft["id"]
    )
    assert persisted_draft["template_text_snapshot"] == old_text
    assert persisted_draft["template_version_snapshot"] == 1

    assert client.post(
        f"/api/v1/laudos/{document_id}/revisao",
        headers=auth("operacional"),
    ).status_code == 200
    finalized = client.post(
        f"/api/v1/laudos/{document_id}/finalizar",
        headers=auth("gestor"),
    )
    assert finalized.status_code == 200, finalized.text
    final_version = finalized.json()["versoes"][0]
    for key in (
        "template_code_snapshot",
        "template_version_snapshot",
        "template_text_snapshot",
        "template_text_sha256",
    ):
        assert final_version[key] == draft[key]


# --------------------------------------------------- entrega autenticada


def test_download_devolve_pdf_valido_com_content_disposition_seguro(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf(), filename="../../evil name.pdf")
    doc = upload.json()
    version_id = doc["current_version_id"]
    resp = client.get(
        f"/api/v1/laudos/{doc['id']}/versoes/{version_id}/conteudo",
        headers=auth("leitura"),
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")
    disposition = resp.headers["content-disposition"]
    assert "evil" not in disposition
    assert ".." not in disposition
    assert exam["public_code"] in disposition


def test_download_modo_download_usa_attachment(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc = upload.json()
    version_id = doc["current_version_id"]
    resp = client.get(
        f"/api/v1/laudos/{doc['id']}/versoes/{version_id}/conteudo?modo=download",
        headers=auth("leitura"),
    )
    assert resp.headers["content-disposition"].startswith("attachment")


def test_cada_entrega_bem_sucedida_cria_auditoria_tecnica_minima(
    client, auth, exam, reports_storage, db
):
    upload = _upload(
        client,
        auth,
        exam["id"],
        _minimal_pdf(),
        filename="TESTE-APAGAR-nao-persistir.pdf",
    )
    document = upload.json()
    version_id = document["current_version_id"]
    path = f"/api/v1/laudos/{document['id']}/versoes/{version_id}/conteudo"

    assert client.get(path, headers=auth("leitura")).status_code == 200
    assert client.get(
        f"{path}?modo=download", headers=auth("leitura")
    ).status_code == 200

    db.expire_all()
    events = db.execute(
        select(AuditLog)
        .where(
            AuditLog.acao == "laudo_conteudo_entregue",
            AuditLog.entidade_id == document["id"],
        )
        .order_by(AuditLog.id)
    ).scalars().all()
    assert len(events) == 2
    assert [event.detalhes["delivery_mode"] for event in events] == [
        "inline",
        "download",
    ]
    for event in events:
        assert set(event.detalhes) == {
            "report_version_id",
            "delivery_mode",
            "institutional_status",
        }
        assert event.detalhes["report_version_id"] == version_id
        serialized = str(event.detalhes)
        assert "TESTE-APAGAR-nao-persistir.pdf" not in serialized
        assert str(reports_storage) not in serialized
        assert "%PDF" not in serialized


# ------------------------------------------------ armazenamento / permissão


def test_arquivo_armazenado_com_permissao_restritiva(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    assert upload.status_code == 201
    found = []
    for root, _dirs, files in os.walk(reports_storage):
        for f in files:
            found.append(os.path.join(root, f))
    assert len(found) == 1
    mode = stat.S_IMODE(os.stat(found[0]).st_mode)
    assert mode == 0o600


def test_todos_os_diretorios_internos_ficam_0700_com_umask_022(
    client, auth, exam, reports_storage
):
    previous = os.umask(0o022)
    try:
        assert _upload(client, auth, exam["id"], _minimal_pdf()).status_code == 201
    finally:
        os.umask(previous)
    for root, directories, _files in os.walk(reports_storage):
        assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
        for directory in directories:
            assert (
                stat.S_IMODE(os.stat(os.path.join(root, directory)).st_mode)
                == 0o700
            )


def test_storage_dir_ausente_falha_fechado(client, auth, exam, monkeypatch):
    monkeypatch.delenv("M15_REPORTS_STORAGE_DIR", raising=False)
    get_settings.cache_clear()
    try:
        resp = _upload(client, auth, exam["id"], _minimal_pdf())
        assert resp.status_code == 503
    finally:
        get_settings.cache_clear()


def test_configuracao_de_storage_insegura_vira_503_sem_caminho(
    client, auth, exam, tmp_path, monkeypatch, caplog
):
    unsafe = tmp_path / "permissive-reports"
    unsafe.mkdir(mode=0o755)
    os.chmod(unsafe, 0o755)
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(unsafe))
    get_settings.cache_clear()
    try:
        response = _upload(client, auth, exam["id"], _minimal_pdf())
        assert response.status_code == 503
        assert (
            response.json()["erro"]["codigo"]
            == "armazenamento_laudos_indisponivel"
        )
        assert str(unsafe) not in response.text
        assert str(unsafe) not in caplog.text
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------- sem PII/caminho


def test_resposta_nunca_expoe_caminho_de_armazenamento(client, auth, exam, reports_storage):
    upload = _upload(client, auth, exam["id"], _minimal_pdf())
    doc_id = upload.json()["id"]
    resp = client.get(f"/api/v1/laudos/{doc_id}", headers=auth("leitura"))
    assert "storage_path" not in resp.text
    assert str(reports_storage) not in resp.text
    assert "/laudos-storage/" not in resp.text
