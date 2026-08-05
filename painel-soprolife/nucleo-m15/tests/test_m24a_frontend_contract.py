"""Contrato executável entre a interface M24C e a API de laudos.

Somente dados marcadamente sintéticos são usados. O contrato prova que a
fila do médico é mínima, a identidade do paciente aparece apenas no
workspace atribuído e o navegador nunca recebe um estado liberável sem
assinatura qualificada.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from app.config import get_settings
from app.models import User
from app.security import (
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
)


SYNTH_TEMPLATE_TEXT = (
    "TESTE - APAGAR: conteúdo controlado sem interpretação clínica."
)
SYNTH_INTERPRETATION = (
    "TESTE - APAGAR: interpretação sintética sem validade clínica."
)


def _minimal_pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture(autouse=True)
def _reports_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m24c-frontend-contract-secret-only-for-tests-0123456789abcdef",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def physician(db, client, auth):
    ensure_roles_exist(db)
    user = User(
        email="medico-ui@teste.local",
        nome="TESTE APAGAR Médico UI",
        password_hash=hash_password("senha-ui-sintetica-123"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    headers = {
        "Authorization": f"Bearer {issue_token(user.id, user.password_hash)}"
    }
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json={
            "grant_physician_role": True,
            "professional_name": "TESTE APAGAR Profissional UI",
            "crm_number": "600001",
            "crm_state": "SC",
            "verification_status": "verified",
            "verification_reference": "CRM-VERIF-TESTE-0002",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    return {
        "user": user,
        "headers": headers,
        "profile": response.json()["profile"],
    }


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
            "codigo": "TESTE_UI_M24C",
            "titulo": "TESTE - APAGAR",
            "texto_tooltip": "Ajuda curta sintética",
            "texto_completo": SYNTH_TEMPLATE_TEXT,
            "ativo": True,
            "status": "approved",
            "clinically_approved": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def assigned_case(client, auth, physician, exam, template):
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": physician["profile"]["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-ui-teste",
            "origin_partner_unit_id": "",
        },
        files={
            "file": (
                "teste-sintetico.pdf",
                _minimal_pdf(),
                "application/pdf",
            )
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return {
        "document": response.json(),
        "physician": physician,
        "exam": exam,
        "template": template,
    }


def _compose(client, case):
    return client.post(
        f"/api/v1/laudos/{case['document']['id']}/compor",
        json={
            "template_id": case["template"]["id"],
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 2,
            "placement": "rodape",
        },
        headers=case["physician"]["headers"],
    )


def test_busca_do_painel_por_codigo_institucional_e_exata(
    client, auth, person, exam
):
    other = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-07-21",
                "status": "Realizado",
            },
        },
        headers=auth("operacional"),
    )
    assert other.status_code == 201, other.text
    response = client.get(
        "/api/v1/espirometrias",
        params={
            "public_code": f" {exam['public_code'].lower()} ",
            "tamanho": 50,
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 200
    assert [item["public_code"] for item in response.json()["itens"]] == [
        exam["public_code"]
    ]


def test_filas_e_detalhe_separam_metadado_operacional_de_identidade(
    client, auth, assigned_case, person
):
    document_id = assigned_case["document"]["id"]
    operational = client.get(
        "/api/v1/laudos",
        params={"exam_code": assigned_case["exam"]["public_code"]},
        headers=auth("operacional"),
    )
    physician_queue = client.get(
        "/api/v1/laudos/meus",
        headers=assigned_case["physician"]["headers"],
    )
    detail = client.get(
        f"/api/v1/laudos/{document_id}",
        headers=assigned_case["physician"]["headers"],
    )
    assert (
        operational.status_code
        == physician_queue.status_code
        == detail.status_code
        == 200
    )
    queue_row = physician_queue.json()[0]
    # Conjunto EXATO de chaves da fila: nenhuma identidade de paciente,
    # texto clínico, nome de arquivo ou caminho pode aparecer aqui. As
    # chaves M25.2 acrescentadas são só carimbos institucionais de estado
    # (liberado / corretivo / código de verificação).
    assert set(queue_row) == {
        "report_code",
        "document_id",
        "exam_code",
        "exam_date",
        "origin_type",
        "origin_label",
        "assignment_timestamp",
        "status",
        "signature_status",
        "releasable",
        "released_at",
        "locked",
        "is_corrective",
        "validation_code",
    }
    assert "patient" not in operational.text
    assert person["nome_completo"] not in operational.text
    assert "patient" not in physician_queue.text
    assert person["nome_completo"] not in physician_queue.text
    assert detail.json()["patient"]["full_name"] == person["nome_completo"]
    assert detail.json()["exam"]["public_code"] == assigned_case["exam"][
        "public_code"
    ]
    assert "storage_path" not in detail.text
    assert "original_filename" not in detail.text


def test_catalogo_clinico_entrega_somente_template_aprovado(
    client, auth, assigned_case
):
    response = client.get(
        "/api/v1/laudos/templates",
        headers=assigned_case["physician"]["headers"],
    )
    assert response.status_code == 200
    assert response.json() == [assigned_case["template"]]
    template = response.json()[0]
    assert {
        "codigo",
        "titulo",
        "texto_tooltip",
        "texto_completo",
        "ativo",
        "status",
        "clinically_approved",
        "versao",
    } <= template.keys()
    assert template["status"] == "approved"
    assert template["clinically_approved"] is True


def test_preview_inline_e_download_exigem_medico_atribuido(
    client, auth, assigned_case
):
    document = assigned_case["document"]
    path = (
        f"/api/v1/laudos/{document['id']}/versoes/"
        f"{document['current_version_id']}/conteudo"
    )
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth("operacional")).status_code == 403
    inline = client.get(
        path, headers=assigned_case["physician"]["headers"]
    )
    assert inline.status_code == 200
    assert inline.content.startswith(b"%PDF-")
    assert inline.headers["content-disposition"].startswith("inline;")
    assert inline.headers["cache-control"] == "private, no-store"
    assert inline.headers["x-content-type-options"] == "nosniff"
    download = client.get(
        path,
        params={"modo": "download"},
        headers=assigned_case["physician"]["headers"],
    )
    assert download.status_code == 200
    assert download.content == inline.content
    assert download.headers["content-disposition"].startswith("attachment;")


def test_interface_consome_apenas_elaboracao_e_assinatura_pendente(
    client, auth, assigned_case
):
    composed = _compose(client, assigned_case)
    assert composed.status_code == 200, composed.text
    assert composed.json()["status"] == "em_elaboracao"
    assert composed.json()["releasable"] is False
    assert composed.json()["versoes"][0]["kind"] == "rascunho"

    for role in ("operacional", "gestor", "admin"):
        denied = client.post(
            f"/api/v1/laudos/{assigned_case['document']['id']}/"
            "preparar-assinatura",
            headers=auth(role),
        )
        assert denied.status_code == 403, role

    prepared = client.post(
        f"/api/v1/laudos/{assigned_case['document']['id']}/"
        "preparar-assinatura",
        headers=assigned_case["physician"]["headers"],
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["status"] == "assinatura_pendente"
    assert body["signature_status"] == "assinatura_pendente"
    assert body["releasable"] is False
    assert body["signature"]["provider"] == "unconfigured"
    assert body["signature"]["releasable"] is False
    assert body["versoes"][0]["kind"] == "assinatura_pendente"
    # M24D: em modo piloto (fixture _reports_storage acima) o rodapé
    # congelado é o PILOTO INTERNO, não o rodapé genérico de TESTE.
    assert body["versoes"][0]["footer_text_snapshot"].endswith(
        "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE"
    )


def test_corretiva_e_assinatura_legada_falham_fechado(
    client, auth, assigned_case
):
    assert _compose(client, assigned_case).status_code == 200
    assert client.post(
        f"/api/v1/laudos/{assigned_case['document']['id']}/"
        "preparar-assinatura",
        headers=assigned_case["physician"]["headers"],
    ).status_code == 200
    correction = client.post(
        f"/api/v1/laudos/{assigned_case['document']['id']}/"
        "nova-versao-corretiva",
        json={"reason_code": "clinical_correction"},
        headers=assigned_case["physician"]["headers"],
    )
    assert correction.status_code == 409
    # M25.2 renomeou o código: correção parte de um laudo FECHADO
    # (assinado com evidência qualificada ou liberado institucionalmente).
    assert correction.json()["erro"]["codigo"] == "laudo_nao_fechado"
    assert client.post(
        f"/api/v1/laudos/{assigned_case['document']['id']}/finalizar",
        headers=auth("gestor"),
    ).status_code == 404
