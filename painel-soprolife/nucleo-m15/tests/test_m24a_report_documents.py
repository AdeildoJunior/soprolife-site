"""M24A — laudos PDF: upload seguro, RBAC, ciclo de vida, entrega autenticada.

Cobre os casos mandatórios do pedido: nome de arquivo hostil, PDF
malformado/criptografado/oversized, permissão/atomicidade de armazenamento,
RBAC, vínculo com exame, transições de ciclo de vida, imutabilidade do
finalizado, fluxo de versão corretiva, preview/composição, ausência de PII/
caminho de sistema de arquivos em respostas e logs, entrega autenticada.
"""

import io
import os
import stat

import pytest
from pypdf import PdfWriter

from app.config import get_settings

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
    assert resp.json()["original_filename_display"] == "passwd.pdf"
    # nenhum arquivo escapou da raiz configurada
    for root, _dirs, files in os.walk(reports_storage):
        for f in files:
            full = os.path.join(root, f)
            assert os.path.commonpath([os.path.abspath(full), str(reports_storage)]) == str(reports_storage)
            assert ".." not in full


def test_upload_nome_com_caracteres_controle(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], _minimal_pdf(), filename="laudo\x00\x1b.pdf")
    assert resp.status_code == 201, resp.text
    assert "\x00" not in resp.json()["original_filename_display"]


def test_upload_texto_puro_rejeitado(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], b"isto nao e um pdf de verdade")
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "assinatura_invalida"


def test_upload_pdf_truncado_rejeitado(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], b"%PDF-1.4\n1 0 obj\n<<")
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pdf_malformado"


def test_upload_pdf_criptografado_rejeitado(client, auth, exam, reports_storage):
    resp = _upload(client, auth, exam["id"], _encrypted_pdf())
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pdf_criptografado"


def test_upload_polyglot_pdf_zip_rejeitado(client, auth, exam, reports_storage):
    polyglot = _minimal_pdf() + b"PK\x03\x04" + b"conteudo-zip-falso"
    resp = _upload(client, auth, exam["id"], polyglot)
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "arquivo_polyglot_suspeito"


def test_upload_oversized_rejeitado(client, auth, exam, reports_storage, monkeypatch):
    monkeypatch.setenv("M15_REPORTS_MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    try:
        resp = _upload(client, auth, exam["id"], _minimal_pdf())
        assert resp.status_code == 422
        assert resp.json()["erro"]["mensagem"]["codigo"] == "pdf_excede_tamanho_maximo"
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
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pagina_invalida"


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


def test_storage_dir_ausente_falha_fechado(client, auth, exam, monkeypatch):
    monkeypatch.delenv("M15_REPORTS_STORAGE_DIR", raising=False)
    get_settings.cache_clear()
    try:
        resp = _upload(client, auth, exam["id"], _minimal_pdf())
        assert resp.status_code == 503
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
