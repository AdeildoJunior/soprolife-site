"""M24A — contrato executável entre o fluxo do painel e a API de laudos.

Os testes usam somente pessoa, exame, texto e PDF sintéticos. Além do shape
consumido pelo navegador, cobrem a entrega binária autenticada, os estados
que governam o RBAC visual e a correção sem mutar o PDF finalizado.
"""

import io

import pytest
from pypdf import PdfWriter

from app.config import get_settings

SYNTH_TEMPLATE_TEXT = "TESTE - APAGAR: conteúdo sintético sem interpretação clínica."


def _minimal_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture(autouse=True)
def _reports_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m24a-frontend-contract-secret-only-for-tests-0123456789abcdef",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def exam(client, auth, person):
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-07-20",
                "status": "Realizado",
                "modalidade": "cowork",
            },
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()["espirometria"]


@pytest.fixture()
def template(client, auth):
    response = client.post(
        "/api/v1/laudos/templates",
        json={
            "codigo": "TST-UI",
            "titulo": "TESTE - APAGAR",
            "texto_tooltip": "Ajuda curta sintética",
            "texto_completo": SYNTH_TEMPLATE_TEXT,
            "ativo": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, auth, exam_id: str, pages: int = 1):
    return client.post(
        "/api/v1/laudos",
        data={"exam_id": exam_id},
        files={"file": ("teste-sintetico.pdf", _minimal_pdf(pages), "application/pdf")},
        headers=auth("operacional"),
    )


def _compose(client, auth, document_id: str, template_id: str):
    return client.post(
        f"/api/v1/laudos/{document_id}/compor",
        json={"template_id": template_id, "page_number": 1, "placement": "rodape"},
        headers=auth("operacional"),
    )


def test_busca_do_painel_por_codigo_institucional_e_exata(client, auth, person, exam):
    other = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {"data_exame": "2026-07-21", "status": "Realizado"},
        },
        headers=auth("operacional"),
    )
    assert other.status_code == 201, other.text

    response = client.get(
        "/api/v1/espirometrias",
        params={"public_code": f" {exam['public_code'].lower()} ", "tamanho": 50},
        headers=auth("leitura"),
    )
    assert response.status_code == 200
    assert [item["public_code"] for item in response.json()["itens"]] == [
        exam["public_code"]
    ]
    assert response.json()["itens"][0]["id"] == exam["id"]


def test_shapes_consumidos_pelo_frontend_e_metadados_sem_caminho(
    client, auth, exam, template
):
    upload = _upload(client, auth, exam["id"], pages=2)
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]

    documents = client.get(
        "/api/v1/laudos",
        params={"exam_id": exam["id"]},
        headers=auth("leitura"),
    )
    detail = client.get(
        f"/api/v1/laudos/{document_id}",
        headers=auth("leitura"),
    )
    templates = client.get("/api/v1/laudos/templates", headers=auth("leitura"))

    assert documents.status_code == detail.status_code == templates.status_code == 200
    assert documents.json()[0]["status"] == "rascunho"
    body = detail.json()
    assert {
        "id",
        "spirometry_exam_id",
        "status",
        "signature_status",
        "current_version_id",
        "superseded_by_id",
        "submitted_for_review_at",
        "finalized_at",
        "created_at_utc",
        "versoes",
    } <= body.keys()
    version = body["versoes"][0]
    assert {
        "id",
        "kind",
        "version_number",
        "sha256",
        "size_bytes",
        "page_count",
        "template_id",
        "page_number",
        "placement",
        "created_at",
    } <= version.keys()
    assert version["page_count"] == 2
    assert "storage_path" not in detail.text
    assert "content_url" not in detail.text

    assert templates.json() == [template]
    assert {
        "codigo",
        "titulo",
        "texto_tooltip",
        "texto_completo",
        "ativo",
    } <= templates.json()[0].keys()


def test_preview_inline_e_download_exigem_sessao_e_nao_cacheiam(
    client, auth, exam
):
    upload = _upload(client, auth, exam["id"])
    assert upload.status_code == 201, upload.text
    document = upload.json()
    path = (
        f"/api/v1/laudos/{document['id']}/versoes/"
        f"{document['current_version_id']}/conteudo"
    )

    assert client.get(path).status_code == 401

    inline = client.get(path, headers=auth("leitura"))
    assert inline.status_code == 200
    assert inline.content.startswith(b"%PDF-")
    assert inline.headers["content-type"] == "application/pdf"
    assert inline.headers["content-disposition"].startswith("inline;")
    assert inline.headers["cache-control"] == "private, no-store"
    assert inline.headers["x-content-type-options"] == "nosniff"

    download = client.get(
        path,
        params={"modo": "download"},
        headers=auth("leitura"),
    )
    assert download.status_code == 200
    assert download.content == inline.content
    assert download.headers["content-disposition"].startswith("attachment;")
    assert exam["public_code"] in download.headers["content-disposition"]


def test_estados_e_rbac_que_controlam_acoes_da_interface(
    client, auth, exam, template
):
    upload = _upload(client, auth, exam["id"])
    document_id = upload.json()["id"]
    compose = _compose(client, auth, document_id, template["id"])
    assert compose.status_code == 200, compose.text
    assert compose.json()["status"] == "rascunho"

    review = client.post(
        f"/api/v1/laudos/{document_id}/revisao",
        headers=auth("operacional"),
    )
    assert review.status_code == 200
    assert review.json()["status"] == "em_revisao"

    forbidden = client.post(
        f"/api/v1/laudos/{document_id}/finalizar",
        headers=auth("operacional"),
    )
    assert forbidden.status_code == 403

    finalized = client.post(
        f"/api/v1/laudos/{document_id}/finalizar",
        headers=auth("gestor"),
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "finalizado"
    assert finalized.json()["signature_status"] == "assinatura_pendente"


def test_corretiva_preserva_pdf_hash_e_marcos_do_finalizado(
    client, auth, exam, template
):
    upload = _upload(client, auth, exam["id"])
    document_id = upload.json()["id"]
    assert _compose(client, auth, document_id, template["id"]).status_code == 200
    assert client.post(
        f"/api/v1/laudos/{document_id}/revisao",
        headers=auth("operacional"),
    ).status_code == 200
    assert client.post(
        f"/api/v1/laudos/{document_id}/finalizar",
        headers=auth("gestor"),
    ).status_code == 200

    before = client.get(
        f"/api/v1/laudos/{document_id}",
        headers=auth("leitura"),
    ).json()
    old_current = next(
        version
        for version in before["versoes"]
        if version["id"] == before["current_version_id"]
    )

    corrective = client.post(
        f"/api/v1/laudos/{document_id}/nova-versao-corretiva",
        headers=auth("operacional"),
    )
    assert corrective.status_code == 201
    assert corrective.json()["status"] == "rascunho"
    assert corrective.json()["id"] != document_id

    after = client.get(
        f"/api/v1/laudos/{document_id}",
        headers=auth("leitura"),
    ).json()
    after_current = next(
        version
        for version in after["versoes"]
        if version["id"] == after["current_version_id"]
    )
    assert after["status"] == "finalizado"
    assert after["superseded_by_id"] == corrective.json()["id"]
    assert after["finalized_at"] == before["finalized_at"]
    assert after["current_version_id"] == before["current_version_id"]
    assert after_current["sha256"] == old_current["sha256"]
    assert after_current["size_bytes"] == old_current["size_bytes"]
