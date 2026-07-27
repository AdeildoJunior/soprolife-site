"""Regressões M24A/M24B preservadas sob o fluxo clínico M24C.

As fixtures usam somente contas, códigos, texto e PDFs sintéticos. Estes
testes mantêm as garantias de upload limitado, validação estrutural,
armazenamento privado, entrega autenticada, auditoria mínima e composição
segura; autoria e acesso agora pertencem exclusivamente ao médico atribuído.
"""

from __future__ import annotations

import asyncio
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


SYNTH_INTERPRETATION = (
    "TESTE - APAGAR: interpretação sintética sem validade clínica."
)
SYNTH_TEMPLATE_BODY = (
    "TESTE - APAGAR: conteúdo controlado sintético para automação."
)


def _minimal_pdf(
    pages: int = 1, width: int = 595, height: int = 842
) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="", owner_password="dono-teste")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture(autouse=True)
def reports_storage(monkeypatch, tmp_path):
    storage_dir = tmp_path / "laudos-storage"
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m24a-teste-segredo-fixo-nao-usar-em-producao-0123456789",
    )
    get_settings.cache_clear()
    yield storage_dir
    get_settings.cache_clear()


@pytest.fixture()
def exam(client, auth, person):
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-07-01",
                "status": "Realizado",
            },
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()["espirometria"]


def _physician(client, auth) -> tuple[dict, dict]:
    cached = getattr(client, "_m24_regression_physician", None)
    if cached:
        return cached
    accounts = client.get(
        "/api/v1/laudos/admin/medicos", headers=auth("admin")
    )
    assert accounts.status_code == 200, accounts.text
    account = next(
        item
        for item in accounts.json()
        if item["user"]["email"] == "leitura@teste.local"
    )
    configured = client.patch(
        f"/api/v1/laudos/admin/medicos/{account['user']['id']}",
        json={
            "grant_physician_role": True,
            "professional_name": "TESTE APAGAR Médico Regressão",
            "crm_number": "424242",
            "crm_state": "AC",
            "verification_status": "verified",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert configured.status_code == 200, configured.text
    cached = (configured.json()["profile"], auth("leitura"))
    setattr(client, "_m24_regression_physician", cached)
    return cached


def _upload(
    client,
    auth,
    exam: dict | str,
    data: bytes,
    *,
    filename: str = "TESTE-APAGAR.pdf",
    role: str = "operacional",
    content_type: str = "application/pdf",
):
    profile, _physician_headers = _physician(client, auth)
    exam_code = exam["public_code"] if isinstance(exam, dict) else exam
    return client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam_code,
            "physician_profile_id": profile["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-teste-01",
            "origin_partner_unit_id": "",
        },
        files={"file": (filename, data, content_type)},
        headers=auth(role),
    )


def _approved_template(client, auth) -> dict:
    response = client.post(
        "/api/v1/laudos/templates",
        json={
            "codigo": "TESTE_REGRESSAO_M24",
            "titulo": "TESTE - APAGAR",
            "texto_tooltip": "Ajuda sintética",
            "texto_completo": SYNTH_TEMPLATE_BODY,
            "ativo": True,
            "status": "approved",
            "clinically_approved": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _compose(client, auth, document: dict, template: dict, **overrides):
    _profile, physician_headers = _physician(client, auth)
    body = {
        "template_id": template["id"],
        "interpretation_text": SYNTH_INTERPRETATION,
        "page_number": 1,
        "placement": "topo",
    }
    body.update(overrides)
    return client.post(
        f"/api/v1/laudos/{document['id']}/compor",
        json=body,
        headers=physician_headers,
    )


def test_upload_pdf_valido_cria_documento_atribuido(
    client, auth, exam, reports_storage
):
    response = _upload(client, auth, exam, _minimal_pdf())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "atribuido"
    assert body["public_code"].startswith("LAU-")
    assert body["spirometry_exam_id"] == exam["id"]
    assert body["origin_type"] == "coworking"
    assert body["assignment"]["active"] is True
    assert "storage_path" not in response.text


@pytest.mark.parametrize(
    "filename",
    ("../../../etc/passwd.pdf", "laudo\x00\x1b.pdf"),
)
def test_nome_hostil_nunca_vira_caminho_ou_resposta(
    client, auth, exam, reports_storage, filename
):
    response = _upload(
        client, auth, exam, _minimal_pdf(), filename=filename
    )
    assert response.status_code == 201, response.text
    assert "original_filename" not in response.text
    assert "passwd.pdf" not in response.text
    for root, _directories, files in os.walk(reports_storage):
        for stored_name in files:
            full = os.path.abspath(os.path.join(root, stored_name))
            assert os.path.commonpath([full, str(reports_storage)]) == str(
                reports_storage
            )
            assert ".." not in full


@pytest.mark.parametrize(
    ("data", "expected_code"),
    (
        (b"isto nao e um pdf de verdade", "assinatura_invalida"),
        (b"%PDF-1.4\n1 0 obj\n<<", "pdf_malformado"),
    ),
)
def test_upload_malformado_e_rejeitado(
    client, auth, exam, reports_storage, data, expected_code
):
    response = _upload(client, auth, exam, data)
    assert response.status_code == 422
    assert response.json()["erro"]["codigo"] == expected_code


def test_upload_pdf_criptografado_rejeitado(
    client, auth, exam, reports_storage
):
    response = _upload(client, auth, exam, _encrypted_pdf())
    assert response.status_code == 422
    assert response.json()["erro"]["codigo"] == "pdf_criptografado"


def test_upload_polyglot_pdf_zip_rejeitado(
    client, auth, exam, reports_storage
):
    buffer = io.BytesIO(_minimal_pdf())
    with zipfile.ZipFile(buffer, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("teste.txt", "conteúdo sintético")
    response = _upload(client, auth, exam, buffer.getvalue())
    assert response.status_code == 422
    assert (
        response.json()["erro"]["codigo"]
        == "arquivo_polyglot_suspeito"
    )


def test_marcador_zip_sem_estrutura_nao_gera_falso_positivo(
    client, auth, exam, reports_storage
):
    data = _minimal_pdf() + b"\n% marcador inocuo PK\x03\x04 sem ZIP\n"
    response = _upload(client, auth, exam, data)
    assert response.status_code == 201, response.text


def test_upload_oversized_e_bloqueado_antes_do_validador(
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
    response = _upload(client, auth, exam, b"x" * 10_000)
    assert response.status_code == 422
    assert response.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"
    assert calls == []


def test_leitura_do_upload_para_em_limite_mais_um_byte():
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


def test_content_length_oversized_recusado_antes_do_multipart(
    client, auth, exam, reports_storage, monkeypatch
):
    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    profile, _headers = _physician(client, auth)
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": "coworking",
        },
        files={"file": ("teste.pdf", _minimal_pdf(), "application/pdf")},
        headers={
            **auth("operacional"),
            "Content-Length": str(
                100 + REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES + 1
            ),
        },
    )
    assert response.status_code == 413
    assert response.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"


def test_stream_multipart_sem_content_length_tambem_e_limitado(
    client, auth, exam, reports_storage, monkeypatch
):
    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    profile, _headers = _physician(client, auth)
    boundary = "M24-BOUNDARY-TESTE"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="exam_code"\r\n\r\n'
        f"{exam['public_code']}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="physician_profile_id"\r\n\r\n'
        f"{profile['id']}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="origin_type"\r\n\r\n'
        "coworking\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="teste.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + b"x" * (REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES + 1000) + (
        f"\r\n--{boundary}--\r\n"
    ).encode()

    def stream():
        for offset in range(0, len(body), 4096):
            yield body[offset : offset + 4096]

    response = client.post(
        "/api/v1/laudos",
        content=stream(),
        headers={
            **auth("operacional"),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    assert response.status_code == 413
    assert response.json()["erro"]["codigo"] == "pdf_excede_tamanho_maximo"


def test_upload_exatamente_no_limite_e_aceito(
    client, auth, exam, reports_storage, monkeypatch
):
    data = _minimal_pdf()
    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", str(len(data)))
    get_settings.cache_clear()
    response = _upload(client, auth, exam, data)
    assert response.status_code == 201, response.text
    assert response.json()["versoes"][0]["size_bytes"] == len(data)


def test_upload_exame_inexistente_e_origem_invalida(
    client, auth, exam, reports_storage
):
    missing = _upload(client, auth, "ESP-999999999", _minimal_pdf())
    assert missing.status_code == 404
    profile, _headers = _physician(client, auth)
    invalid = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": "texto-livre",
        },
        files={"file": ("teste.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert invalid.status_code == 422


def test_upload_exige_operacional(client, auth, exam, reports_storage):
    response = _upload(
        client, auth, exam, _minimal_pdf(), role="leitura"
    )
    assert response.status_code == 403


def test_rotas_clinicas_legadas_foram_removidas(
    client, auth, exam, reports_storage
):
    document = _upload(client, auth, exam, _minimal_pdf()).json()
    for action in ("revisao", "finalizar", "devolver-para-ajuste"):
        response = client.post(
            f"/api/v1/laudos/{document['id']}/{action}",
            headers=auth("admin"),
        )
        assert response.status_code in {404, 405}


def test_composicao_preserva_validacao_de_pagina_e_integridade(
    client, auth, exam, reports_storage
):
    document = _upload(client, auth, exam, _minimal_pdf()).json()
    template = _approved_template(client, auth)
    invalid_page = _compose(
        client, auth, document, template, page_number=5
    )
    assert invalid_page.status_code == 422
    assert invalid_page.json()["erro"]["codigo"] == "pagina_invalida"

    original = document["versoes"][0]
    stored = (
        Path(reports_storage)
        / "laudos"
        / exam["id"]
        / document["id"]
        / f"{original['id']}.pdf"
    )
    stored.write_bytes(b"%PDF-corrompido")
    corrupt = _compose(client, auth, document, template)
    assert corrupt.status_code == 409
    assert corrupt.json()["erro"]["codigo"] == "pdf_armazenado_invalido"
    assert str(reports_storage) not in corrupt.text


def test_composicao_recusa_original_ausente(
    client, auth, exam, reports_storage
):
    document = _upload(client, auth, exam, _minimal_pdf()).json()
    template = _approved_template(client, auth)
    original = document["versoes"][0]
    (
        Path(reports_storage)
        / "laudos"
        / exam["id"]
        / document["id"]
        / f"{original['id']}.pdf"
    ).unlink()
    response = _compose(client, auth, document, template)
    assert response.status_code == 409
    assert response.json()["erro"]["codigo"] == "arquivo_laudo_ausente"


def test_entrega_exige_medico_atribuido_e_tem_headers_privados(
    client, auth, exam, reports_storage
):
    document = _upload(
        client,
        auth,
        exam,
        _minimal_pdf(),
        filename="../../evil name.pdf",
    ).json()
    version_id = document["current_version_id"]
    path = (
        f"/api/v1/laudos/{document['id']}/versoes/{version_id}/conteudo"
    )
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth("operacional")).status_code == 403
    _profile, physician_headers = _physician(client, auth)
    response = client.get(path, headers=physician_headers)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "inline" in response.headers["content-disposition"]
    assert "evil" not in response.headers["content-disposition"]
    download = client.get(f"{path}?modo=download", headers=physician_headers)
    assert download.headers["content-disposition"].startswith("attachment")


def test_entrega_cria_somente_auditoria_tecnica_minima(
    client, auth, exam, reports_storage, db
):
    document = _upload(
        client,
        auth,
        exam,
        _minimal_pdf(),
        filename="TESTE-APAGAR-nao-persistir.pdf",
    ).json()
    version_id = document["current_version_id"]
    _profile, physician_headers = _physician(client, auth)
    response = client.get(
        f"/api/v1/laudos/{document['id']}/versoes/{version_id}/conteudo",
        headers=physician_headers,
    )
    assert response.status_code == 200
    db.expire_all()
    event = db.execute(
        select(AuditLog).where(
            AuditLog.acao == "laudo_conteudo_entregue",
            AuditLog.entidade_id == document["id"],
        )
    ).scalar_one()
    assert event.detalhes == {
        "report_version_id": version_id,
        "delivery_mode": "inline",
        "institutional_status": "atribuido",
    }
    serialized = repr(event.detalhes)
    assert "TESTE-APAGAR-nao-persistir.pdf" not in serialized
    assert str(reports_storage) not in serialized
    assert "%PDF" not in serialized


def test_arquivo_e_diretorios_tem_permissoes_restritivas(
    client, auth, exam, reports_storage
):
    previous = os.umask(0o022)
    try:
        assert (
            _upload(client, auth, exam, _minimal_pdf()).status_code == 201
        )
    finally:
        os.umask(previous)
    found = []
    for root, directories, files in os.walk(reports_storage):
        assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
        for directory in directories:
            assert (
                stat.S_IMODE(os.stat(os.path.join(root, directory)).st_mode)
                == 0o700
            )
        for name in files:
            path = os.path.join(root, name)
            found.append(path)
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert len(found) == 1


def test_storage_ausente_ou_inseguro_falha_fechado_sem_caminho(
    client, auth, exam, tmp_path, monkeypatch, caplog
):
    monkeypatch.delenv("M15_REPORTS_STORAGE_DIR", raising=False)
    get_settings.cache_clear()
    missing = _upload(client, auth, exam, _minimal_pdf())
    assert missing.status_code == 503

    unsafe = tmp_path / "permissive-reports"
    unsafe.mkdir(mode=0o755)
    os.chmod(unsafe, 0o755)
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(unsafe))
    get_settings.cache_clear()
    response = _upload(client, auth, exam, _minimal_pdf())
    assert response.status_code == 503
    assert (
        response.json()["erro"]["codigo"]
        == "armazenamento_laudos_indisponivel"
    )
    assert str(unsafe) not in response.text
    assert str(unsafe) not in caplog.text


def test_respostas_nunca_expoem_storage_filename_ou_texto_clinico(
    client, auth, exam, reports_storage
):
    document = _upload(client, auth, exam, _minimal_pdf()).json()
    operational = client.get(
        f"/api/v1/laudos/{document['id']}",
        headers=auth("operacional"),
    )
    assert operational.status_code == 200
    for forbidden in (
        "storage_path",
        "original_filename",
        str(reports_storage),
        SYNTH_INTERPRETATION,
    ):
        assert forbidden not in operational.text
