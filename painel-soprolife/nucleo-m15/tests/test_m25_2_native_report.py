"""M25.2 — laudo próprio da SoproLife: catálogo, prévia, liberação e adendo.

Todos os pacientes, médicos, CRMs, exames e PDFs são marcadamente
sintéticos. Nenhuma fixture representa pessoa, laudo ou assinatura real —
em particular, NENHUM ativo de assinatura manuscrita autêntico entra aqui:
os testes usam um PNG gerado na hora, sem qualquer semelhança com uma
assinatura de verdade.
"""

from __future__ import annotations

import io
import struct
import zlib
import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AuditLog,
    PartnerUnit,
    Partner,
    Person,
    PhysicianSignatureAsset,
    ReportAddendum,
    ReportDocument,
    ReportDocumentVersion,
    ReportSignature,
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
from app.services.report_conclusions import (
    BRONCHODILATOR_BY_CODE,
    CONCLUSION_OPTIONS,
)

RELEASE_CONFIRMATION = "ASSINAR E LIBERAR"
ADDENDUM_CONFIRMATION = "PUBLICAR ADENDO"
SIGNATURE_CONFIRMATION = "ATIVO DE ASSINATURA AUTORIZADO"


# ------------------------------------------------------------- utilidades


def _minimal_pdf(pages: int = 1) -> bytes:
    """PDF sintético que faz o papel do arquivo técnico da MIR."""

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _synthetic_signature_png(width: int = 300, height: int = 90) -> bytes:
    """PNG sintético — traço geométrico, jamais uma assinatura real."""

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filtro None por linha
        for x in range(width):
            on = abs((x * height) // width - y) < 3
            value = 0 if on else 255
            raw.extend((value, value, value, 255 if on else 0))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


def _pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


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
        "m25-2-synthetic-test-secret-only-0123456789-abcdef",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _physician(db, *, suffix: str) -> tuple[User, dict]:
    ensure_roles_exist(db)
    user = User(
        email=f"medica-m25-{suffix}@teste.local",
        nome=f"TESTE APAGAR Médica {suffix}",
        password_hash=hash_password("senha-medica-sintetica-123"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    return user, {"Authorization": f"Bearer {issue_token(user.id, user.password_hash)}"}


def _configure_profile(client, auth, user, *, crm="00123", name=None):
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json={
            "grant_physician_role": True,
            "professional_name": name or "TESTE APAGAR Profissional M25",
            "crm_number": crm,
            "crm_state": "RJ",
            "rqe": "RQE-TESTE-58224",
            "verification_status": "verified",
            "verification_reference": "CRM-VERIF-TESTE-M25",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    return response.json()["profile"]


def _create_exam(client, auth, person, *, com_bd: bool) -> dict:
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-07-15",
                "status": "Realizado",
                "broncodilatador": com_bd,
            },
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()["espirometria"]


def _upload(client, auth, exam, profile, *, origin="coworking", unit_id=""):
    return client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": origin,
            "origin_label": "unidade-teste-m25",
            "origin_partner_unit_id": unit_id,
        },
        files={"file": ("TESTE-APAGAR.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )


def _make_case(client, auth, db, person, *, com_bd: bool = True, suffix="001"):
    doctor, doctor_auth = _physician(db, suffix=suffix)
    profile = _configure_profile(client, auth, doctor, crm=f"00{suffix}")
    exam = _create_exam(client, auth, person, com_bd=com_bd)
    uploaded = _upload(client, auth, exam, profile)
    assert uploaded.status_code == 201, uploaded.text
    return {
        "doctor": doctor,
        "doctor_auth": doctor_auth,
        "profile": profile,
        "exam": exam,
        "document": uploaded.json(),
    }


@pytest.fixture()
def case(client, auth, db, person):
    return _make_case(client, auth, db, person, com_bd=True)


def _preview(client, case, **overrides):
    payload = {
        "conclusion_code": "DVO_MODERADO",
        "bronchodilator_code": "RBD_POSITIVO",
    }
    payload.update(overrides)
    return client.post(
        f"/api/v1/laudos/{case['document']['id']}/laudo/previa",
        json=payload,
        headers=case["doctor_auth"],
    )


def _release(client, case, preview_body):
    return client.post(
        f"/api/v1/laudos/{case['document']['id']}/assinar-e-liberar",
        json={
            "confirmacao": RELEASE_CONFIRMATION,
            "expected_version_id": preview_body["preview_version_id"],
            "expected_text_sha256": preview_body["final_text_sha256"],
        },
        headers=case["doctor_auth"],
    )


# ------------------------------------------------------------- catálogo


def test_catalogo_tem_o_conjunto_fechado_e_converte_para_texto_por_extenso(
    client, case
):
    response = client.get(
        f"/api/v1/laudos/{case['document']['id']}/catalogo-conclusoes",
        headers=case["doctor_auth"],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    codes = [item["codigo"] for item in body["conclusoes"]]
    assert len(codes) == 18
    assert codes[0] == "NORMAL"
    assert codes[-1] == "PERSONALIZADO"
    assert len(set(codes)) == len(codes)

    por_codigo = {item["codigo"]: item for item in body["conclusoes"]}
    # O botão é curto; o texto que vai ao PDF é por extenso.
    assert por_codigo["DVO_MOD_GRAVE"]["rotulo"] == "DVO Mod. grave"
    assert por_codigo["DVO_MOD_GRAVE"]["texto"] == (
        "Distúrbio ventilatório obstrutivo moderadamente grave."
    )
    assert por_codigo["NORMAL"]["texto"] == (
        "Espirometria dentro dos limites da normalidade."
    )
    assert por_codigo["DVR_SUG_LEVE"]["texto"] == (
        "Padrão sugestivo de distúrbio ventilatório restritivo leve."
    )
    assert por_codigo["DVI"]["texto"] == (
        "Padrão sugestivo de distúrbio ventilatório inespecífico."
    )
    assert por_codigo["PERSONALIZADO"]["personalizado"] is True
    assert por_codigo["PERSONALIZADO"]["texto"] == ""

    # Nenhum grau é sugerido/selecionado pelo servidor.
    assert not any("selecionado" in item for item in body["conclusoes"])


def test_exame_sem_pos_bd_nao_oferece_complemento_incompativel(
    client, auth, db, person
):
    sem_bd = _make_case(client, auth, db, person, com_bd=False, suffix="002")
    body = client.get(
        f"/api/v1/laudos/{sem_bd['document']['id']}/catalogo-conclusoes",
        headers=sem_bd["doctor_auth"],
    ).json()
    assert body["exame_com_pos_bd"] is False
    assert [item["codigo"] for item in body["complementos_bd"]] == [
        "BD_NAO_REALIZADO"
    ]

    recusado = _preview(
        client, sem_bd, conclusion_code="NORMAL", bronchodilator_code="RBD_POSITIVO"
    )
    assert recusado.status_code == 422
    assert recusado.json()["erro"]["codigo"] == "complemento_bd_incompativel"

    # "BD não realizado" não acrescenta frase nenhuma.
    ok = _preview(
        client,
        sem_bd,
        conclusion_code="NORMAL",
        bronchodilator_code="BD_NAO_REALIZADO",
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["final_text"] == (
        "Espirometria dentro dos limites da normalidade."
    )


def test_exame_com_pos_bd_oferece_todos_os_complementos(client, case):
    body = client.get(
        f"/api/v1/laudos/{case['document']['id']}/catalogo-conclusoes",
        headers=case["doctor_auth"],
    ).json()
    assert body["exame_com_pos_bd"] is True
    assert [item["codigo"] for item in body["complementos_bd"]] == [
        "RBD_POSITIVO",
        "RBD_NEGATIVO",
        "REV_COMPLETA",
        "REV_PARCIAL",
        "BD_NAO_REALIZADO",
    ]


def test_conclusao_personalizada_exige_texto_da_medica(client, case):
    vazio = _preview(
        client, case, conclusion_code="PERSONALIZADO", conclusion_custom_text=" "
    )
    assert vazio.status_code == 422
    assert vazio.json()["erro"]["codigo"] == "texto_personalizado_ausente"

    # Texto livre não é aceito junto de uma conclusão de catálogo.
    conflito = _preview(
        client,
        case,
        conclusion_code="NORMAL",
        conclusion_custom_text="texto sintético indevido",
    )
    assert conflito.status_code == 422
    assert conflito.json()["erro"]["codigo"] == "texto_personalizado_inesperado"

    ok = _preview(
        client,
        case,
        conclusion_code="PERSONALIZADO",
        conclusion_custom_text="TESTE APAGAR: conclusão sintética personalizada.",
        bronchodilator_code="RBD_NEGATIVO",
    )
    assert ok.status_code == 200, ok.text
    assert "conclusão sintética personalizada" in ok.json()["final_text"]


def test_conclusao_fora_do_catalogo_e_recusada(client, case):
    response = _preview(client, case, conclusion_code="DVO_INVENTADO")
    assert response.status_code == 422
    assert response.json()["erro"]["codigo"] == "conclusao_desconhecida"


# --------------------------------------------------------------- prévia


def test_previa_gera_documento_nativo_separado_do_pdf_da_mir(client, case, db):
    response = _preview(client, case)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "em_elaboracao"
    assert body["final_text"] == (
        "Distúrbio ventilatório obstrutivo moderado.\n"
        "Com resposta significativa ao broncodilatador."
    )

    version = db.get(ReportDocumentVersion, body["preview_version_id"])
    assert version.kind == "laudo_previa"
    assert version.conclusion_code_snapshot == "DVO_MODERADO"
    assert version.bronchodilator_code_snapshot == "RBD_POSITIVO"
    assert version.validation_code_snapshot is None
    assert version.released_at_snapshot is None

    # O PDF original da MIR continua intacto, como versão própria.
    original = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == case["document"]["id"],
            ReportDocumentVersion.kind == "original",
        )
    ).scalar_one()
    assert original.sha256 != version.sha256
    assert original.conclusion_code_snapshot is None

    documentos = client.get(
        f"/api/v1/laudos/{case['document']['id']}/documentos",
        headers=case["doctor_auth"],
    ).json()
    assert documentos["tecnico_mir"]["version_id"] == original.id
    assert documentos["laudo_soprolife"]["version_id"] == version.id
    assert documentos["tecnico_mir"]["download_path"] != (
        documentos["laudo_soprolife"]["download_path"]
    )


def test_medica_pode_editar_livremente_o_texto_final(client, case, db):
    editado = "TESTE APAGAR: redação final revisada pela médica sintética."
    response = _preview(client, case, final_text=editado)
    assert response.status_code == 200, response.text
    assert response.json()["final_text"] == editado

    version = db.get(
        ReportDocumentVersion, response.json()["preview_version_id"]
    )
    # A escolha de catálogo permanece auditável mesmo com texto reescrito.
    assert version.interpretation_text_snapshot == editado
    assert version.conclusion_code_snapshot == "DVO_MODERADO"
    assert version.conclusion_text_snapshot == (
        "Distúrbio ventilatório obstrutivo moderado."
    )


def test_previa_contem_os_dados_obrigatorios_do_laudo(client, case, db, person):
    body = _preview(client, case, observations="Observação sintética.").json()
    version = db.get(ReportDocumentVersion, body["preview_version_id"])
    stored = _stored_bytes(db, version)
    texto = _pdf_text(stored)

    assert "Laudo de Espirometria" in texto
    assert person["nome_completo"] in texto
    assert case["exam"]["public_code"] in texto
    assert case["document"]["public_code"] in texto
    assert "Distúrbio ventilatório obstrutivo moderado." in texto
    assert "Observação sintética." in texto
    assert "TESTE APAGAR Profissional M25" in texto
    assert "CRM-RJ" in texto
    assert "RQE" in texto
    # O laudo declara que o PDF técnico da MIR é documento separado.
    assert "MIR" in texto and "SEPARADO" in texto
    # Prévia é inequivocamente prévia.
    assert "PRÉVIA" in texto


def _stored_bytes(db, version) -> bytes:
    from app.services.report_storage import read_and_validate_stored_pdf

    settings = get_settings()
    root = settings.resolved_reports_storage_dir()
    return read_and_validate_stored_pdf(
        root / version.storage_path,
        root=root,
        expected_sha256=version.sha256,
        expected_size_bytes=version.size_bytes,
        expected_page_count=version.page_count,
        max_size_bytes=settings.reports_max_upload_bytes,
    ).data


# ------------------------------------------------------------ liberação


def test_assinar_e_liberar_exige_confirmacao_consciente(client, case):
    preview = _preview(client, case).json()
    sem_confirmacao = client.post(
        f"/api/v1/laudos/{case['document']['id']}/assinar-e-liberar",
        json={
            "confirmacao": "sim",
            "expected_version_id": preview["preview_version_id"],
            "expected_text_sha256": preview["final_text_sha256"],
        },
        headers=case["doctor_auth"],
    )
    assert sem_confirmacao.status_code == 422


def test_assinar_recusa_conteudo_diferente_do_conferido(client, case):
    primeira = _preview(client, case).json()
    # A médica gera outra prévia: a anterior deixa de valer.
    _preview(client, case, conclusion_code="DVO_GRAVE")

    resposta = _release(client, case, primeira)
    assert resposta.status_code == 409
    assert resposta.json()["erro"]["codigo"] == "previa_desatualizada"

    atual = _preview(client, case, conclusion_code="DVO_GRAVE").json()
    adulterado = dict(atual)
    adulterado["final_text_sha256"] = "0" * 64
    divergente = _release(client, case, adulterado)
    assert divergente.status_code == 409
    assert (
        divergente.json()["erro"]["codigo"] == "conteudo_divergente_da_previa"
    )


def test_liberacao_congela_hash_versao_codigo_e_bloqueia_edicao(
    client, case, db
):
    preview = _preview(client, case).json()
    released = _release(client, case, preview)
    assert released.status_code == 200, released.text
    body = released.json()

    assert body["status"] == "liberado"
    assert body["locked"] is True
    assert body["signature_status"] == "liberada_institucional"
    assert body["qualified_signature"] is False
    assert len(body["validation_code"]) == 12
    assert body["validation_url"].endswith(body["validation_code"])
    assert len(body["document_sha256"]) == 64

    document = db.get(ReportDocument, case["document"]["id"])
    db.refresh(document)
    assert document.released_at is not None
    assert document.released_physician_profile_id == case["profile"]["id"]
    assert document.signed_at is None  # não é assinatura qualificada

    released_version = db.get(ReportDocumentVersion, body["released_version_id"])
    assert released_version.kind == "laudo_liberado"
    assert released_version.validation_code_snapshot == body["validation_code"]
    assert released_version.sha256 == body["document_sha256"]
    # A prévia anterior é PRESERVADA.
    previa = db.get(ReportDocumentVersion, preview["preview_version_id"])
    assert previa is not None and previa.kind == "laudo_previa"

    # Depois de liberado, não há mais edição de conteúdo clínico.
    bloqueado = _preview(client, case, conclusion_code="NORMAL")
    assert bloqueado.status_code == 409
    assert bloqueado.json()["erro"]["codigo"] == "laudo_bloqueado_para_edicao"

    # E não é possível liberar de novo.
    de_novo = _release(client, case, preview)
    assert de_novo.status_code == 409
    assert de_novo.json()["erro"]["codigo"] == "laudo_ja_liberado"


def test_pdf_liberado_declara_liberacao_e_nao_alega_icp_brasil(
    client, case, db
):
    preview = _preview(client, case).json()
    body = _release(client, case, preview).json()
    version = db.get(ReportDocumentVersion, body["released_version_id"])
    texto = _pdf_text(_stored_bytes(db, version))

    assert "DOCUMENTO LIBERADO" in texto
    assert body["validation_code"] in texto
    assert "PRÉVIA" not in texto
    # Declaração honesta: nunca afirma assinatura qualificada.
    assert "não constitui" in texto and "ICP-Brasil" in texto

    signature = db.execute(
        select(ReportSignature).where(
            ReportSignature.report_document_version_id == version.id
        )
    ).scalar_one()
    assert signature.provider == "institutional_release"
    assert signature.verification_metadata["qualified_signature"] is False
    assert signature.verification_metadata["document_sha256"] == version.sha256


def test_pdf_tecnico_da_mir_permanece_byte_a_byte_intacto(client, case, db):
    original = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id == case["document"]["id"],
            ReportDocumentVersion.kind == "original",
        )
    ).scalar_one()
    antes = _stored_bytes(db, original)
    # A sessão do teste e a API usam conexões SQLite distintas: encerrar a
    # transação de leitura evita "database is locked" na escrita seguinte.
    db.rollback()

    preview = _preview(client, case).json()
    _release(client, case, preview)

    db.expire_all()
    depois_row = db.get(ReportDocumentVersion, original.id)
    assert depois_row.sha256 == original.sha256
    assert _stored_bytes(db, depois_row) == antes


# --------------------------------------------------------- autorização


def test_usuario_nao_autorizado_nao_assina(client, auth, case):
    preview = _preview(client, case).json()
    corpo = {
        "confirmacao": RELEASE_CONFIRMATION,
        "expected_version_id": preview["preview_version_id"],
        "expected_text_sha256": preview["final_text_sha256"],
    }
    for papel in ("admin", "gestor", "operacional", "leitura"):
        resposta = client.post(
            f"/api/v1/laudos/{case['document']['id']}/assinar-e-liberar",
            json=corpo,
            headers=auth(papel),
        )
        assert resposta.status_code == 403, papel
        assert (
            resposta.json()["erro"]["codigo"]
            == "papel_medico_explicito_necessario"
        )


def test_outra_medica_nao_assina_laudo_alheio(client, auth, db, person, case):
    outra, outra_auth = _physician(db, suffix="099")
    _configure_profile(client, auth, outra, crm="00099")
    preview = _preview(client, case).json()
    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/assinar-e-liberar",
        json={
            "confirmacao": RELEASE_CONFIRMATION,
            "expected_version_id": preview["preview_version_id"],
            "expected_text_sha256": preview["final_text_sha256"],
        },
        headers=outra_auth,
    )
    assert resposta.status_code == 404
    assert resposta.json()["erro"]["codigo"] == "laudo_nao_atribuido"


def test_perfil_suspenso_perde_a_capacidade_de_assinar(
    client, auth, case, db
):
    preview = _preview(client, case).json()
    suspenso = client.patch(
        f"/api/v1/laudos/admin/medicos/{case['doctor'].id}",
        json={"active": False},
        headers=auth("admin"),
    )
    assert suspenso.status_code == 200, suspenso.text
    resposta = _release(client, case, preview)
    assert resposta.status_code == 403
    assert resposta.json()["erro"]["codigo"] == "perfil_medico_indisponivel"


# ------------------------------------------------------------- adendo


def test_adendo_preserva_a_versao_liberada_anterior(client, case, db):
    preview = _preview(client, case).json()
    released = _release(client, case, preview).json()

    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/adendo",
        json={
            "body_text": "TESTE APAGAR: adendo sintético de esclarecimento.",
            "confirmacao": ADDENDUM_CONFIRMATION,
        },
        headers=case["doctor_auth"],
    )
    assert resposta.status_code == 201, resposta.text
    body = resposta.json()
    assert body["addendum_sequence"] == 1
    assert body["status"] == "liberado"

    anterior = db.get(ReportDocumentVersion, released["released_version_id"])
    assert anterior is not None and anterior.kind == "laudo_liberado"

    nova = db.get(ReportDocumentVersion, body["addendum_version_id"])
    assert nova.kind == "laudo_adendo"
    assert nova.addendum_sequence == 1
    assert nova.sha256 != anterior.sha256
    # O código de validação do documento não muda com o adendo.
    assert nova.validation_code_snapshot == released["validation_code"]

    texto = _pdf_text(_stored_bytes(db, nova))
    # `draw_section_heading` imprime o título em caixa alta.
    assert "ADENDO 1" in texto
    assert "adendo sintético de esclarecimento" in texto
    # O conteúdo original continua no documento.
    assert "Distúrbio ventilatório obstrutivo moderado." in texto

    db.rollback()
    segundo = client.post(
        f"/api/v1/laudos/{case['document']['id']}/adendo",
        json={
            "body_text": "TESTE APAGAR: segundo adendo sintético.",
            "confirmacao": ADDENDUM_CONFIRMATION,
        },
        headers=case["doctor_auth"],
    )
    assert segundo.status_code == 201
    assert segundo.json()["addendum_sequence"] == 2
    assert (
        db.execute(
            select(ReportAddendum).where(
                ReportAddendum.report_document_id == case["document"]["id"]
            )
        )
        .scalars()
        .all()
        .__len__()
        == 2
    )


def test_adendo_exige_laudo_liberado(client, case):
    _preview(client, case)
    resposta = client.post(
        f"/api/v1/laudos/{case['document']['id']}/adendo",
        json={
            "body_text": "TESTE APAGAR: adendo indevido.",
            "confirmacao": ADDENDUM_CONFIRMATION,
        },
        headers=case["doctor_auth"],
    )
    assert resposta.status_code == 409
    assert resposta.json()["erro"]["codigo"] == "laudo_nao_liberado"


def test_correcao_apos_liberacao_cria_documento_novo_sem_tocar_no_anterior(
    client, case, db
):
    preview = _preview(client, case).json()
    released = _release(client, case, preview).json()

    corretiva = client.post(
        f"/api/v1/laudos/{case['document']['id']}/nova-versao-corretiva",
        json={"reason_code": "clinical_correction"},
        headers=case["doctor_auth"],
    )
    assert corretiva.status_code == 201, corretiva.text
    novo = corretiva.json()
    assert novo["id"] != case["document"]["id"]
    assert novo["corrects_document_id"] == case["document"]["id"]
    assert novo["status"] == "atribuido"

    anterior = db.get(ReportDocument, case["document"]["id"])
    db.refresh(anterior)
    assert anterior.status == "liberado"
    assert anterior.validation_code == released["validation_code"]
    assert anterior.current_version_id == released["released_version_id"]


# ------------------------------------------------------------ validação


def test_validacao_por_codigo_nao_expoe_paciente_nem_conclusao(
    client, auth, case, person
):
    preview = _preview(client, case).json()
    released = _release(client, case, preview).json()

    resposta = client.get(
        f"/api/v1/laudos/validacao/{released['validation_code']}",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    body = resposta.json()
    assert body["report_code"] == case["document"]["public_code"]
    assert body["status"] == "liberado"
    assert body["qualified_signature"] is False
    assert body["document_sha256"] == released["document_sha256"]

    serializado = resposta.text
    assert person["nome_completo"] not in serializado
    assert "Distúrbio" not in serializado

    desconhecido = client.get(
        "/api/v1/laudos/validacao/ZZZZZZZZZZZZ", headers=auth("operacional")
    )
    assert desconhecido.status_code == 404


# ------------------------------------------ ativo de assinatura manuscrita


def test_laudo_funciona_sem_ativo_de_assinatura_cadastrado(client, case, db):
    preview = _preview(client, case).json()
    released = _release(client, case, preview)
    assert released.status_code == 200, released.text
    body = released.json()
    assert body["handwritten_signature_applied"] is False

    version = db.get(ReportDocumentVersion, body["released_version_id"])
    assert version.signature_asset_id_snapshot is None
    texto = _pdf_text(_stored_bytes(db, version))
    # Sem imagem, o bloco identificador da médica continua presente.
    assert "TESTE APAGAR Profissional M25" in texto


def test_ativo_de_assinatura_e_admin_only_e_nunca_e_devolvido_em_bytes(
    client, auth, case, db
):
    png = _synthetic_signature_png()
    profile_id = case["profile"]["id"]
    caminho = f"/api/v1/laudos/admin/medicos/{profile_id}/assinatura"

    # Nem a própria médica cadastra o próprio ativo.
    negado = client.post(
        caminho,
        data={"confirmacao": SIGNATURE_CONFIRMATION},
        files={"arquivo": ("a.png", png, "image/png")},
        headers=case["doctor_auth"],
    )
    assert negado.status_code == 403

    sem_confirmacao = client.post(
        caminho,
        data={"confirmacao": "ok"},
        files={"arquivo": ("a.png", png, "image/png")},
        headers=auth("admin"),
    )
    assert sem_confirmacao.status_code == 422
    assert (
        sem_confirmacao.json()["erro"]["codigo"]
        == "confirmacao_assinatura_ausente"
    )

    criado = client.post(
        caminho,
        data={"confirmacao": SIGNATURE_CONFIRMATION},
        files={"arquivo": ("qualquer-nome.png", png, "image/png")},
        headers=auth("admin"),
    )
    assert criado.status_code == 201, criado.text
    body = criado.json()
    assert body["mime_type"] == "image/png"
    assert body["active"] is True
    # Nenhum byte, caminho ou nome de arquivo sai na resposta.
    assert "storage_path" not in body
    assert "data" not in body
    assert "qualquer-nome" not in criado.text

    status = client.get(caminho, headers=auth("admin")).json()
    assert status["configurada"] is True


def test_ativo_invalido_e_recusado(client, auth, case):
    caminho = (
        f"/api/v1/laudos/admin/medicos/{case['profile']['id']}/assinatura"
    )
    for conteudo, esperado in (
        (b"%PDF-1.7\n', not a png", "assinatura_formato_invalido"),
        (b"\x89PNG\r\n\x1a\nlixo", "assinatura_corrompida"),
    ):
        resposta = client.post(
            caminho,
            data={"confirmacao": SIGNATURE_CONFIRMATION},
            files={"arquivo": ("x.png", conteudo, "image/png")},
            headers=auth("admin"),
        )
        assert resposta.status_code == 422, resposta.text
        assert resposta.json()["erro"]["codigo"] == esperado


def test_assinatura_manuscrita_entra_somente_apos_a_liberacao(
    client, auth, case, db
):
    png = _synthetic_signature_png()
    criado = client.post(
        f"/api/v1/laudos/admin/medicos/{case['profile']['id']}/assinatura",
        data={"confirmacao": SIGNATURE_CONFIRMATION},
        files={"arquivo": ("a.png", png, "image/png")},
        headers=auth("admin"),
    )
    assert criado.status_code == 201, criado.text
    asset_sha = criado.json()["sha256"]

    preview = _preview(client, case).json()
    previa = db.get(ReportDocumentVersion, preview["preview_version_id"])
    # A prévia nunca carrega a assinatura.
    assert previa.signature_asset_id_snapshot is None
    previa_size = previa.size_bytes
    db.rollback()

    body = _release(client, case, preview).json()
    assert body["handwritten_signature_applied"] is True
    liberada = db.get(ReportDocumentVersion, body["released_version_id"])
    assert liberada.signature_asset_sha256_snapshot == asset_sha
    # O PDF liberado ficou maior que a prévia por conter a imagem.
    assert liberada.size_bytes > previa_size


def test_revogar_ativo_preserva_o_historico(client, auth, case, db):
    caminho = (
        f"/api/v1/laudos/admin/medicos/{case['profile']['id']}/assinatura"
    )
    client.post(
        caminho,
        data={"confirmacao": SIGNATURE_CONFIRMATION},
        files={"arquivo": ("a.png", _synthetic_signature_png(), "image/png")},
        headers=auth("admin"),
    )
    revogado = client.delete(caminho, headers=auth("admin"))
    assert revogado.status_code == 200
    assert revogado.json()["active"] is False

    # A linha antiga permanece — nada é apagado.
    assets = db.execute(select(PhysicianSignatureAsset)).scalars().all()
    assert len(assets) == 1 and assets[0].active is False
    db.rollback()

    # Um novo cadastro revoga o anterior sem removê-lo.
    client.post(
        caminho,
        data={"confirmacao": SIGNATURE_CONFIRMATION},
        files={"arquivo": ("b.png", _synthetic_signature_png(320, 96), "image/png")},
        headers=auth("admin"),
    )
    db.expire_all()
    assets = db.execute(select(PhysicianSignatureAsset)).scalars().all()
    assert len(assets) == 2
    assert sum(1 for a in assets if a.active) == 1


def test_ativo_corrompido_no_disco_falha_fechado(client, auth, case, db):
    client.post(
        f"/api/v1/laudos/admin/medicos/{case['profile']['id']}/assinatura",
        data={"confirmacao": SIGNATURE_CONFIRMATION},
        files={"arquivo": ("a.png", _synthetic_signature_png(), "image/png")},
        headers=auth("admin"),
    )
    db.expire_all()
    asset = db.execute(select(PhysicianSignatureAsset)).scalars().one()
    caminho = get_settings().resolved_reports_storage_dir() / asset.storage_path
    caminho.write_bytes(_synthetic_signature_png(200, 60))
    db.rollback()

    preview = _preview(client, case).json()
    resposta = _release(client, case, preview)
    assert resposta.status_code == 409
    assert (
        resposta.json()["erro"]["codigo"] == "assinatura_armazenada_divergente"
    )


# --------------------------------------------------------------- local


def test_local_usa_a_unidade_parceira_vinculada_ao_exame(
    client, auth, db, person
):
    partner = Partner(public_code="CLI-990001", nome="Pastore")
    db.add(partner)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-990001",
        partner_id=partner.id,
        nome="Unidade Ipanema",
        logradouro="Rua Teixeira de Melo, 54",
        bairro="Ipanema",
        cidade="Rio de Janeiro",
        uf="RJ",
        telefone_central="(21) 2508-9001",
    )
    db.add(unit)
    db.commit()

    caso = _make_case(client, auth, db, person, com_bd=True, suffix="300")
    exam = db.get(SpirometryExam, caso["exam"]["id"])
    exam.partner_unit_id = unit.id
    db.commit()

    body = _preview(client, caso).json()
    assert body["location"]["nome"] == "Pastore — Unidade Ipanema"
    assert body["location"]["endereco"] == (
        "Rua Teixeira de Melo, 54 — Ipanema, Rio de Janeiro — RJ"
    )
    assert body["location"]["contato"] == "Central: (21) 2508-9001"
    assert body["location"]["origem_do_dado"] == "exame_unidade"

    version = db.get(ReportDocumentVersion, body["preview_version_id"])
    assert version.location_partner_unit_id_snapshot == unit.id
    texto = _pdf_text(_stored_bytes(db, version))
    assert "Rua Teixeira de Melo, 54" in texto
    assert "(21) 2508-9001" in texto


def test_local_generico_sem_unidade_nao_inventa_endereco(client, case, db):
    body = _preview(client, case).json()
    assert body["location"]["partner_unit_id"] is None
    assert body["location"]["endereco"] is None
    assert body["location"]["origem_do_dado"] == "origem_sem_unidade"
    # Origem "coworking" descreve a modalidade, sem endereço fixo.
    assert "Espaço de atendimento SoproLife" in body["location"]["nome"]

    texto = _pdf_text(
        _stored_bytes(
            db, db.get(ReportDocumentVersion, body["preview_version_id"])
        )
    )
    assert "Teixeira de Melo" not in texto


def test_dados_ausentes_do_paciente_nao_quebram_o_laudo(
    client, auth, db, person
):
    caso = _make_case(client, auth, db, person, com_bd=False, suffix="400")
    pessoa = db.get(Person, person["id"])
    pessoa.data_nascimento = None
    pessoa.sexo = None
    db.commit()

    body = _preview(
        client, caso, conclusion_code="NORMAL", bronchodilator_code=None
    )
    assert body.status_code == 200, body.text
    texto = _pdf_text(
        _stored_bytes(
            db,
            db.get(ReportDocumentVersion, body.json()["preview_version_id"]),
        )
    )
    assert "não informada" in texto
    assert "não informado" in texto


# ------------------------------------------------------------ auditoria


def test_auditoria_registra_liberacao_sem_dado_clinico_ou_paciente(
    client, case, db, person
):
    preview = _preview(client, case).json()
    released = _release(client, case, preview).json()

    eventos = (
        db.execute(
            select(AuditLog).where(
                AuditLog.entidade_id == case["document"]["id"]
            )
        )
        .scalars()
        .all()
    )
    acoes = {evento.acao for evento in eventos}
    assert "laudo_nativo_previa_gerada" in acoes
    assert "laudo_assinado_e_liberado" in acoes

    liberacao = next(
        e for e in eventos if e.acao == "laudo_assinado_e_liberado"
    )
    detalhes = liberacao.detalhes
    assert detalhes["qualified_signature"] is False
    assert detalhes["document_sha256"] == released["document_sha256"]
    assert detalhes["conclusion_code"] == "DVO_MODERADO"
    assert liberacao.user_id == case["doctor"].id

    # Nenhum dado clínico por extenso, paciente ou caminho no registro.
    serializado = repr(detalhes)
    assert person["nome_completo"] not in serializado
    assert "Distúrbio ventilatório" not in serializado
    assert "/" not in detalhes.get("validation_code", "")
    assert "storage" not in serializado


def test_catalogo_do_modulo_bate_com_o_texto_exigido():
    """Trava o catálogo fechado contra edição acidental."""

    esperado = {
        "NORMAL": "Espirometria dentro dos limites da normalidade.",
        "DVO_LEVE": "Distúrbio ventilatório obstrutivo leve.",
        "DVO_MODERADO": "Distúrbio ventilatório obstrutivo moderado.",
        "DVO_MOD_GRAVE": (
            "Distúrbio ventilatório obstrutivo moderadamente grave."
        ),
        "DVO_GRAVE": "Distúrbio ventilatório obstrutivo grave.",
        "DVO_MUITO_GRAVE": "Distúrbio ventilatório obstrutivo muito grave.",
        "DVR_SUG_LEVE": (
            "Padrão sugestivo de distúrbio ventilatório restritivo leve."
        ),
        "DVR_SUG_MODERADO": (
            "Padrão sugestivo de distúrbio ventilatório restritivo moderado."
        ),
        "DVR_SUG_MOD_GRAVE": (
            "Padrão sugestivo de distúrbio ventilatório restritivo "
            "moderadamente grave."
        ),
        "DVR_SUG_GRAVE": (
            "Padrão sugestivo de distúrbio ventilatório restritivo grave."
        ),
        "DVR_SUG_MUITO_GRAVE": (
            "Padrão sugestivo de distúrbio ventilatório restritivo muito grave."
        ),
        "DVM_SUG_LEVE": (
            "Padrão sugestivo de distúrbio ventilatório misto leve."
        ),
        "DVM_SUG_MODERADO": (
            "Padrão sugestivo de distúrbio ventilatório misto moderado."
        ),
        "DVM_SUG_MOD_GRAVE": (
            "Padrão sugestivo de distúrbio ventilatório misto moderadamente "
            "grave."
        ),
        "DVM_SUG_GRAVE": (
            "Padrão sugestivo de distúrbio ventilatório misto grave."
        ),
        "DVM_SUG_MUITO_GRAVE": (
            "Padrão sugestivo de distúrbio ventilatório misto muito grave."
        ),
        "DVI": "Padrão sugestivo de distúrbio ventilatório inespecífico.",
        "PERSONALIZADO": "",
    }
    assert {
        option.code: option.full_text for option in CONCLUSION_OPTIONS
    } == esperado

    assert {
        code: option.full_text
        for code, option in BRONCHODILATOR_BY_CODE.items()
    } == {
        "RBD_POSITIVO": "Com resposta significativa ao broncodilatador.",
        "RBD_NEGATIVO": "Sem resposta significativa ao broncodilatador.",
        "REV_COMPLETA": "Reversibilidade completa após broncodilatador.",
        "REV_PARCIAL": "Reversibilidade parcial após broncodilatador.",
        "BD_NAO_REALIZADO": "",
    }


# ------------------------------------------------------- M25.3 (finalização)


def test_identificacao_profissional_completa_chega_ao_pdf(
    client, auth, db, person
):
    """M25.3 — `especialidade` e `crm_display` são graváveis e impressos.

    As duas colunas nasceram no M25.2 e o gerador do PDF já as lia, mas
    NENHUMA rota sabia gravá-las: o laudo saía sem especialidade e com o CRM
    em dígitos crus. Sem isso a identificação exigida ("Médica Pneumologista",
    "CRM-RJ 52.62307-5") era inalcançável pelo cadastro real.
    """

    caso = _make_case(client, auth, db, person, suffix="931")
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{caso['doctor'].id}",
        json={
            "professional_name": "TESTE APAGAR Dra. Profissional M25-3",
            "crm_number": "52623075",
            "crm_display": "52.62307-5",
            "crm_state": "RJ",
            "rqe": "58224",
            "especialidade": "Médica Pneumologista",
            "verification_status": "verified",
            "verification_reference": "CRM-VERIF-TESTE-M25-3",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["crm_display"] == "52.62307-5"
    assert profile["especialidade"] == "Médica Pneumologista"

    preview = _preview(client, caso)
    assert preview.status_code == 200, preview.text
    released = _release(client, caso, preview.json())
    assert released.status_code == 200, released.text
    version = db.get(ReportDocumentVersion, released.json()["released_version_id"])
    texto = _pdf_text(_stored_bytes(db, version))
    assert "TESTE APAGAR Dra. Profissional M25-3" in texto
    assert "Médica Pneumologista" in texto
    # Formatado, não em dígitos crus.
    assert "CRM-RJ 52.62307-5" in texto
    assert "RQE 58224" in texto


def test_crm_display_nao_pode_alterar_os_digitos_do_crm(
    client, auth, db, person
):
    """A formatação não pode virar caminho lateral para trocar o registro."""

    caso = _make_case(client, auth, db, person, suffix="932")
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{caso['doctor'].id}",
        json={
            "crm_number": "52623075",
            # dígitos diferentes do CRM canônico
            "crm_display": "99.99999-9",
            "crm_state": "RJ",
        },
        headers=auth("admin"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["erro"]["codigo"] == "crm_display_divergente"


def test_tela_medica_mostra_exame_e_local_estruturado(
    client, auth, db, person
):
    """M25.3 — a médica vê local e contexto do exame ANTES de concluir.

    Antes a tela só exibia o código técnico da origem; o local de realização
    existia apenas dentro do PDF, o que impedia conferir o cabeçalho antes de
    assinar.
    """

    partner = Partner(public_code="CLI-993201", nome="Clínica Exemplo M25-3")
    db.add(partner)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-993201",
        partner_id=partner.id,
        nome="Unidade Exemplo",
        logradouro="Rua Exemplo, 100",
        bairro="Bairro Exemplo",
        cidade="Cidade Exemplo",
        uf="RJ",
        telefone_central="(21) 0000-0000",
    )
    db.add(unit)
    db.commit()

    caso = _make_case(client, auth, db, person, suffix="933")
    exam = db.get(SpirometryExam, caso["exam"]["id"])
    exam.hora_exame = "09:20"
    exam.indicacao_clinica = "Indicação sintética de teste."
    document = db.get(ReportDocument, caso["document"]["id"])
    document.origin_type = "clinica_parceira"
    document.origin_partner_unit_id = unit.id
    db.commit()

    response = client.get(
        f"/api/v1/laudos/{caso['document']['id']}", headers=caso["doctor_auth"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["location"]["nome"] == "Clínica Exemplo M25-3 — Unidade Exemplo"
    assert "Rua Exemplo, 100" in body["location"]["endereco"]
    assert body["location"]["contato"] == "Central: (21) 0000-0000"
    assert body["location"]["origem_do_dado"] == "documento_unidade"
    assert body["exam"]["exam_time"] == "09:20"
    assert body["exam"]["post_bronchodilator"] is True
    assert body["exam"]["clinical_indication"] == "Indicação sintética de teste."


def test_laudo_liberado_tipico_cabe_em_uma_pagina(client, auth, db, person):
    """M25.3 — o laudo liberado padrão não deve derramar numa página quase vazia.

    A prévia já cabia em uma página, mas a versão LIBERADA (que acrescenta o
    bloco de validação e a área de assinatura) estourava por poucos pontos e
    empurrava a assinatura sozinha para a página 2.
    """

    caso = _make_case(client, auth, db, person, suffix="934")
    preview = _preview(client, caso)
    assert preview.status_code == 200, preview.text
    released = _release(client, caso, preview.json())
    assert released.status_code == 200, released.text
    version = db.get(
        ReportDocumentVersion, released.json()["released_version_id"]
    )
    assert version.page_count == 1, (
        "o laudo liberado típico precisa caber em uma página "
        f"(gerou {version.page_count})"
    )


# ----------------------------------------------------- M25.4 (visual/selo)


def test_selo_institucional_so_aparece_em_laudo_liberado(client, case, db):
    """O selo é uma afirmação de estado: numa prévia seria mentira visual."""

    preview = _preview(client, case)
    assert preview.status_code == 200, preview.text
    previa = db.get(ReportDocumentVersion, preview.json()["preview_version_id"])
    texto_previa = _pdf_text(_stored_bytes(db, previa))
    assert "LIBERAÇÃO" not in texto_previa.upper()
    # Encerra a transação de leitura antes de voltar à API: no SQLite uma
    # transação ORM aberta faz o commit da liberação falhar com
    # "database is locked".
    db.rollback()

    released = _release(client, case, preview.json())
    assert released.status_code == 200, released.text
    liberada = db.get(
        ReportDocumentVersion, released.json()["released_version_id"]
    )
    texto = _pdf_text(_stored_bytes(db, liberada))
    # O selo imprime o estado em duas linhas dentro do anel.
    assert "SOPROLIFE" in texto
    assert "LIBERAÇÃO" in texto.upper()
    assert "INSTITUCIONAL" in texto.upper()
    # E nunca sugere assinatura qualificada.
    assert "assinado digitalmente" not in texto.lower()


def test_laudo_nao_repete_codigo_e_versao_no_bloco_de_validacao(
    client, case, db
):
    """M25.4 — código e versão constam do cabeçalho e do rodapé.

    O bloco de validação repetia os dois uma TERCEIRA vez. A informação
    continua no documento; o que saiu foi a redundância.
    """

    preview = _preview(client, case)
    released = _release(client, case, preview.json())
    assert released.status_code == 200, released.text
    version = db.get(
        ReportDocumentVersion, released.json()["released_version_id"]
    )
    texto = _pdf_text(_stored_bytes(db, version))
    assert "IDENTIFICAÇÃO E VALIDAÇÃO" in texto.upper()
    # O bloco não traz mais as linhas "Documento:" e "Versão:".
    assert "Documento: " not in texto
    assert "Versão: " not in texto
    # Mas o código do laudo continua visível (cabeçalho/rodapé) e o código
    # de verificação segue no bloco.
    assert case["document"]["public_code"] in texto
    assert "Código de verificação" in texto
