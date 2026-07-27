"""M24C — papel médico isolado, atribuição e ciclo clínico fail-closed.

Todos os nomes, CRMs, contas, exames, textos e PDFs são marcadamente
sintéticos. Nenhuma fixture representa médico, paciente ou laudo real.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    PhysicianProfile,
    ReportAssignment,
    ReportAssignmentEvent,
    ReportDocument,
    ReportDocumentVersion,
    ReportSignature,
    ReportTemplate,
    User,
)
from app.security import (
    ROLE_ADMIN,
    ROLE_MEDICO,
    ensure_roles_exist,
    get_role,
    hash_password,
    issue_token,
    user_effective_roles,
)
from app.services.report_catalog import (
    PROVISIONAL_CODES,
    PROVISIONAL_WARNING,
)
from app.services.report_publication import (
    ReportPublicationTransaction,
    report_publication_transaction,
)


SYNTH_INTERPRETATION = (
    "TESTE - APAGAR: interpretação sintética sem validade clínica."
)
SYNTH_TEMPLATE_BODY = (
    "TESTE - APAGAR: conteúdo controlado sintético para automação."
)


def _minimal_pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m24c-synthetic-test-secret-only-0123456789-abcdef",
    )
    monkeypatch.delenv(
        "M15_REPORTS_TEST_ALLOW_PROVISIONAL_TEMPLATES", raising=False
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _create_physician_user(
    db,
    *,
    suffix: str,
    extra_role: str | None = None,
) -> tuple[User, dict]:
    ensure_roles_exist(db)
    user = User(
        email=f"medico-{suffix}@teste.local",
        nome=f"TESTE APAGAR Médico {suffix}",
        password_hash=hash_password("senha-medico-sintetica-123"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    if extra_role:
        user.roles.append(get_role(db, extra_role))
    db.add(user)
    db.commit()
    token = issue_token(user.id, user.password_hash)
    return user, {"Authorization": f"Bearer {token}"}


def _configure_profile(
    client,
    auth,
    user: User,
    *,
    crm: str,
    name: str,
    active: bool = True,
    verification: str = "verified",
) -> dict:
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{user.id}",
        json={
            "grant_physician_role": True,
            "professional_name": name,
            "crm_number": crm,
            "crm_state": "RJ",
            "rqe": "RQE-TESTE-001",
            "verification_status": verification,
            "active": active,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    return response.json()["profile"]


def _create_exam(client, auth, person, *, day: str = "2026-07-01") -> dict:
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": day,
                "status": "Realizado",
            },
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()["espirometria"]


def _create_approved_template(client, auth, code: str = "TESTE_M24C") -> dict:
    response = client.post(
        "/api/v1/laudos/templates",
        json={
            "codigo": code,
            "titulo": "TESTE - APAGAR",
            "texto_tooltip": "Ajuda sintética sem decisão clínica",
            "texto_completo": SYNTH_TEMPLATE_BODY,
            "ativo": True,
            "status": "approved",
            "clinically_approved": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(
    client,
    auth,
    exam: dict,
    profile: dict,
    *,
    origin: str = "coworking",
    origin_label: str = "unidade-teste-01",
    filename: str = "TESTE-APAGAR.pdf",
    role: str = "operacional",
):
    return client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": origin,
            "origin_label": origin_label,
            "origin_partner_unit_id": "",
        },
        files={"file": (filename, _minimal_pdf(), "application/pdf")},
        headers=auth(role),
    )


@pytest.fixture()
def medical_case(client, auth, db, person):
    doctor, doctor_auth = _create_physician_user(db, suffix="001")
    profile = _configure_profile(
        client,
        auth,
        doctor,
        crm="CRM 00.123",
        name="TESTE APAGAR Profissional Um",
    )
    exam = _create_exam(client, auth, person)
    template = _create_approved_template(client, auth)
    uploaded = _upload(client, auth, exam, profile)
    assert uploaded.status_code == 201, uploaded.text
    return {
        "doctor": doctor,
        "doctor_auth": doctor_auth,
        "profile": profile,
        "exam": exam,
        "template": template,
        "document": uploaded.json(),
    }


def _compose(client, case, *, template_id: str | None = None):
    return client.post(
        f"/api/v1/laudos/{case['document']['id']}/compor",
        json={
            "template_id": (
                case["template"]["id"] if template_id is None else template_id
            ),
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 2,
            "placement": "topo",
        },
        headers=case["doctor_auth"],
    )


def test_medico_e_papel_isolado_e_admin_nao_herda_autoria(
    client, auth, db, medical_case
):
    doctor = medical_case["doctor"]
    db.expire_all()
    loaded = db.get(User, doctor.id)
    assert user_effective_roles(loaded) == {"medico"}
    admin = db.execute(
        select(User).where(User.email == "admin@teste.local")
    ).scalar_one()
    assert "medico" not in user_effective_roles(admin)

    # Papel médico puro não abre áreas gerais.
    for path in (
        "/api/v1/pessoas",
        "/api/v1/crm/workspace",
        "/api/v1/financeiro",
        "/api/v1/auditoria",
        "/api/v1/admin/usuarios",
    ):
        assert client.get(path, headers=medical_case["doctor_auth"]).status_code in {
            403,
            404,
            405,
        }, path

    document_id = medical_case["document"]["id"]
    assert client.post(
        f"/api/v1/laudos/{document_id}/compor",
        json={
            "template_id": medical_case["template"]["id"],
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 1,
            "placement": "topo",
        },
        headers=auth("admin"),
    ).status_code == 403


def test_multirole_explicito_ainda_exige_perfil_e_atribuicao(
    client, auth, db, medical_case
):
    admin_user = db.execute(
        select(User).where(User.email == "admin@teste.local")
    ).scalar_one()
    admin_user_id = admin_user.id
    db.rollback()
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{admin_user_id}",
        json={
            "grant_physician_role": True,
            "professional_name": "TESTE APAGAR Administrador Médico",
            "crm_number": "88001",
            "crm_state": "SP",
            "verification_status": "verified",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["user"]["papeis"]) == {"admin", "medico"}
    denied = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/compor",
        json={
            "template_id": medical_case["template"]["id"],
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 1,
            "placement": "topo",
        },
        headers=auth("admin"),
    )
    assert denied.status_code == 404
    assert denied.json()["erro"]["codigo"] == "laudo_nao_atribuido"


def test_perfil_normaliza_crm_valida_uf_e_recusa_ativacao_insegura(
    client, auth, db
):
    doctor, _headers = _create_physician_user(db, suffix="profile")
    blank_name = client.patch(
        f"/api/v1/laudos/admin/medicos/{doctor.id}",
        json={
            "professional_name": "  ",
            "crm_number": "12345",
            "crm_state": "RJ",
        },
        headers=auth("admin"),
    )
    assert blank_name.status_code == 422

    invalid_uf = client.patch(
        f"/api/v1/laudos/admin/medicos/{doctor.id}",
        json={
            "professional_name": "TESTE APAGAR Perfil",
            "crm_number": "12.345",
            "crm_state": "XX",
        },
        headers=auth("admin"),
    )
    assert invalid_uf.status_code == 422

    pending = client.patch(
        f"/api/v1/laudos/admin/medicos/{doctor.id}",
        json={
            "professional_name": "TESTE APAGAR Perfil",
            "crm_number": "CRM/RJ 0012.345",
            "crm_state": "RJ",
            "verification_status": "pending",
            "active": False,
        },
        headers=auth("admin"),
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["profile"]["crm_number"] == "0012345"
    unsafe = client.patch(
        f"/api/v1/laudos/admin/medicos/{doctor.id}",
        json={"active": True},
        headers=auth("admin"),
    )
    assert unsafe.status_code == 409
    assert unsafe.json()["erro"]["codigo"] == "ativacao_medica_insegura"


def test_crm_uf_ativo_unico_no_banco(client, auth, db):
    first, _ = _create_physician_user(db, suffix="crm-a")
    second, _ = _create_physician_user(db, suffix="crm-b")
    _configure_profile(
        client,
        auth,
        first,
        crm="77.001",
        name="TESTE APAGAR CRM A",
    )
    duplicate = client.patch(
        f"/api/v1/laudos/admin/medicos/{second.id}",
        json={
            "professional_name": "TESTE APAGAR CRM B",
            "crm_number": "CRM 77001",
            "crm_state": "RJ",
            "verification_status": "verified",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["erro"]["codigo"] == "crm_uf_ativo_duplicado"


@pytest.mark.parametrize(
    "origin",
    [
        "pastore",
        "coworking",
        "residencial",
        "clinica_parceira",
        "empresa_pcmso",
        "outro",
    ],
)
def test_seis_origens_controladas_sao_aceitas(
    client, auth, db, person, origin
):
    doctor, _ = _create_physician_user(db, suffix=f"origin-{origin}")
    profile = _configure_profile(
        client,
        auth,
        doctor,
        crm=f"91{len(origin):03d}",
        name=f"TESTE APAGAR Origem {origin}",
    )
    exam = _create_exam(client, auth, person, day="2026-07-02")
    response = _upload(
        client,
        auth,
        exam,
        profile,
        origin=origin,
        origin_label="origem-teste",
    )
    assert response.status_code == 201, response.text
    assert response.json()["origin_type"] == origin


def test_origem_recusa_pii_e_nota_clinica(client, auth, medical_case):
    for unsafe in (
        "Maria Silva",
        "teste@paciente.local",
        "(21) 99999-0000",
        "diagnóstico de asma",
    ):
        response = client.post(
            "/api/v1/laudos",
            data={
                "exam_code": medical_case["exam"]["public_code"],
                "physician_profile_id": medical_case["profile"]["id"],
                "origin_type": "outro",
                "origin_label": unsafe,
            },
            files={"file": ("hostil.pdf", _minimal_pdf(), "application/pdf")},
            headers=auth("operacional"),
        )
        assert response.status_code == 422, unsafe
        assert response.json()["erro"]["codigo"] == "rotulo_origem_inseguro"


def test_referencia_de_unidade_exige_origem_clinica_parceira(
    client, auth, medical_case
):
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": medical_case["exam"]["public_code"],
            "physician_profile_id": medical_case["profile"]["id"],
            "origin_type": "coworking",
            "origin_label": "unidade-teste",
            "origin_partner_unit_id": (
                "24c20000-0000-4000-8000-000000000001"
            ),
        },
        files={
            "file": (
                "TESTE-APAGAR-unidade.pdf",
                _minimal_pdf(),
                "application/pdf",
            )
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 422
    assert (
        response.json()["erro"]["codigo"]
        == "unidade_origem_incompativel"
    )


def test_upload_cria_uma_atribuicao_e_fila_minima_sem_paciente(
    client, auth, db, medical_case, person
):
    document_id = medical_case["document"]["id"]
    db.expire_all()
    assignments = db.execute(
        select(ReportAssignment).where(
            ReportAssignment.report_document_id == document_id
        )
    ).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].active is True

    queue = client.get(
        "/api/v1/laudos/meus", headers=medical_case["doctor_auth"]
    )
    assert queue.status_code == 200
    assert len(queue.json()) == 1
    row = queue.json()[0]
    assert row["report_code"].startswith("LAU-")
    assert row["exam_code"] == medical_case["exam"]["public_code"]
    assert row["origin_type"] == "coworking"
    assert row["status"] == "atribuido"
    dump = queue.text
    assert person["nome_completo"] not in dump
    assert person["id"] not in dump
    assert "interpretation" not in dump

    operational = client.get(
        f"/api/v1/laudos/{document_id}", headers=auth("operacional")
    )
    assert operational.status_code == 200
    assert "patient" not in operational.text
    assert "versoes" not in operational.text


def test_medico_ve_somente_atribuido_e_original_e_autenticado(
    client, auth, db, medical_case
):
    second, second_auth = _create_physician_user(db, suffix="second")
    _configure_profile(
        client,
        auth,
        second,
        crm="99002",
        name="TESTE APAGAR Profissional Dois",
    )
    document = medical_case["document"]
    assert client.get(
        "/api/v1/laudos/meus", headers=second_auth
    ).json() == []
    assert client.get(
        f"/api/v1/laudos/{document['id']}", headers=second_auth
    ).status_code == 404

    detail = client.get(
        f"/api/v1/laudos/{document['id']}",
        headers=medical_case["doctor_auth"],
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["patient"]["full_name"]
    original = detail.json()["versoes"][0]
    content_path = (
        f"/api/v1/laudos/{document['id']}/versoes/{original['id']}/conteudo"
    )
    delivered = client.get(content_path, headers=medical_case["doctor_auth"])
    assert delivered.status_code == 200
    assert delivered.headers["cache-control"] == "private, no-store"
    assert delivered.content.startswith(b"%PDF-")
    assert client.get(content_path, headers=auth("operacional")).status_code == 403


def test_reatribuicao_append_only_antes_do_primeiro_rascunho(
    client, auth, db, medical_case
):
    second, second_auth = _create_physician_user(db, suffix="reassign")
    second_profile = _configure_profile(
        client,
        auth,
        second,
        crm="99003",
        name="TESTE APAGAR Reatribuído",
    )
    current = medical_case["document"]["assignment"]
    response = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/reatribuir",
        json={
            "physician_profile_id": second_profile["id"],
            "expected_assignment_id": current["id"],
            "reason_code": "physician_unavailable",
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["assignment"]["physician_profile_id"] == second_profile["id"]
    assert client.get(
        "/api/v1/laudos/meus", headers=medical_case["doctor_auth"]
    ).json() == []
    assert len(client.get(
        "/api/v1/laudos/meus", headers=second_auth
    ).json()) == 1

    db.expire_all()
    rows = db.execute(
        select(ReportAssignment)
        .where(
            ReportAssignment.report_document_id
            == medical_case["document"]["id"]
        )
        .order_by(ReportAssignment.assigned_at)
    ).scalars().all()
    assert len(rows) == 2
    assert sum(1 for row in rows if row.active) == 1
    assert rows[0].ended_at is not None
    events = db.execute(
        select(ReportAssignmentEvent).where(
            ReportAssignmentEvent.report_document_id
            == medical_case["document"]["id"]
        )
    ).scalars().all()
    assert {event.event_type for event in events} == {"assigned", "reassigned"}
    assert {event.reason_code for event in events} == {
        "initial_assignment",
        "physician_unavailable",
    }


def test_reatribuicao_concorrente_usa_expected_assignment(
    client, auth, db, medical_case
):
    second, _ = _create_physician_user(db, suffix="race-a")
    third, _ = _create_physician_user(db, suffix="race-b")
    second_profile = _configure_profile(
        client, auth, second, crm="90011", name="TESTE APAGAR Race A"
    )
    third_profile = _configure_profile(
        client, auth, third, crm="90012", name="TESTE APAGAR Race B"
    )
    expected = medical_case["document"]["assignment"]["id"]
    first = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/reatribuir",
        json={
            "physician_profile_id": second_profile["id"],
            "expected_assignment_id": expected,
            "reason_code": "assignment_correction",
        },
        headers=auth("operacional"),
    )
    assert first.status_code == 200
    stale = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/reatribuir",
        json={
            "physician_profile_id": third_profile["id"],
            "expected_assignment_id": expected,
            "reason_code": "operational_redistribution",
        },
        headers=auth("operacional"),
    )
    assert stale.status_code == 409
    assert stale.json()["erro"]["codigo"] == "atribuicao_desatualizada"


def test_composicao_e_exclusiva_do_atribuido_e_congela_snapshots(
    client, auth, db, medical_case
):
    original_hash = medical_case["document"]["versoes"][0]["sha256"]
    assert _compose(client, medical_case).status_code == 200
    response = _compose(client, medical_case)
    assert response.status_code == 200, response.text
    version = response.json()["versoes"][0]
    assert response.json()["status"] == "em_elaboracao"
    assert version["kind"] == "rascunho"
    assert version["template_code_snapshot"] == medical_case["template"]["codigo"]
    assert version["template_version_snapshot"] == 1
    assert version["interpretation_text_snapshot"] == SYNTH_INTERPRETATION
    assert version["physician_name_snapshot"] == "TESTE APAGAR Profissional Um"
    assert version["physician_crm_number_snapshot"] == "00123"
    assert version["origin_type_snapshot"] == "coworking"
    assert version["footer_code_snapshot"] == "TESTE_NAO_ASSINADO"
    assert (
        "MODELO DE TESTE — DOCUMENTO NÃO ASSINADO E SEM VALIDADE PARA LIBERAÇÃO"
        in version["footer_text_snapshot"]
    )
    assert "assinado digitalmente" not in version["footer_text_snapshot"].lower()

    db.expire_all()
    original = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.report_document_id
            == medical_case["document"]["id"],
            ReportDocumentVersion.kind == "original",
        )
    ).scalar_one()
    assert original.sha256 == original_hash

    blocked = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/reatribuir",
        json={
            "physician_profile_id": medical_case["profile"]["id"],
            "expected_assignment_id": medical_case["document"]["assignment"]["id"],
            "reason_code": "assignment_correction",
        },
        headers=auth("operacional"),
    )
    assert blocked.status_code == 409
    assert blocked.json()["erro"]["codigo"] == "reatribuicao_clinica_bloqueada"


def test_operacional_gestor_e_outro_medico_nao_editam_interpretacao(
    client, auth, db, medical_case
):
    path = f"/api/v1/laudos/{medical_case['document']['id']}/compor"
    payload = {
        "template_id": medical_case["template"]["id"],
        "interpretation_text": SYNTH_INTERPRETATION,
        "page_number": 1,
        "placement": "topo",
    }
    assert client.post(
        path, json=payload, headers=auth("operacional")
    ).status_code == 403
    assert client.post(
        path, json=payload, headers=auth("gestor")
    ).status_code == 403

    other, other_auth = _create_physician_user(db, suffix="not-assigned")
    _configure_profile(
        client,
        auth,
        other,
        crm="55501",
        name="TESTE APAGAR Não Atribuído",
    )
    assert client.post(path, json=payload, headers=other_auth).status_code == 404


def test_perfil_suspenso_bloqueia_operacao_imediatamente(
    client, auth, medical_case
):
    suspended = client.patch(
        f"/api/v1/laudos/admin/medicos/{medical_case['doctor'].id}",
        json={"active": False},
        headers=auth("admin"),
    )
    assert suspended.status_code == 200
    assert client.get(
        "/api/v1/laudos/meus", headers=medical_case["doctor_auth"]
    ).status_code == 403
    assert _compose(client, medical_case).status_code == 403


def test_edicao_do_perfil_nao_reescreve_snapshot_e_exige_nova_previa(
    client, auth, db, medical_case
):
    composed = _compose(client, medical_case)
    assert composed.status_code == 200
    old_version_id = composed.json()["versoes"][0]["id"]
    updated = client.patch(
        f"/api/v1/laudos/admin/medicos/{medical_case['doctor'].id}",
        json={
            "professional_name": "TESTE APAGAR Profissional Um Revisado",
            "crm_number": "00123",
            "crm_state": "RJ",
            "rqe": "RQE-TESTE-002",
            "verification_status": "verified",
            "active": True,
        },
        headers=auth("admin"),
    )
    assert updated.status_code == 200, updated.text
    stale = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/preparar-assinatura",
        headers=medical_case["doctor_auth"],
    )
    assert stale.status_code == 409
    assert stale.json()["erro"]["codigo"] == "snapshot_autoria_desatualizado"
    db.expire_all()
    old = db.get(ReportDocumentVersion, old_version_id)
    assert old.physician_name_snapshot == "TESTE APAGAR Profissional Um"
    assert old.physician_rqe_snapshot == "RQE-TESTE-001"
    db.rollback()

    fresh = _compose(client, medical_case)
    assert fresh.status_code == 200
    assert (
        fresh.json()["versoes"][0]["physician_name_snapshot"]
        == "TESTE APAGAR Profissional Um Revisado"
    )


def test_preparacao_assinatura_fica_pendente_sem_estado_assinado(
    client, auth, db, medical_case
):
    assert _compose(client, medical_case).status_code == 200
    prepared = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/preparar-assinatura",
        headers=medical_case["doctor_auth"],
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["status"] == "assinatura_pendente"
    assert body["signature_status"] == "assinatura_pendente"
    assert body["releasable"] is False
    assert body["versoes"][0]["kind"] == "assinatura_pendente"
    assert body["signature"]["provider"] == "unconfigured"
    assert body["signature"]["status"] == "assinatura_pendente"
    assert body["signature"]["releasable"] is False
    assert body["signature"]["completed_at"] is None

    db.expire_all()
    assert db.execute(
        select(func.count(ReportDocument.id)).where(
            ReportDocument.status == "assinado"
        )
    ).scalar_one() == 0
    assert db.execute(
        select(func.count(ReportSignature.id)).where(
            ReportSignature.status == "assinada"
        )
    ).scalar_one() == 0
    assert client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/finalizar",
        headers=auth("gestor"),
    ).status_code == 404
    assert client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/preparar-assinatura",
        headers=auth("admin"),
    ).status_code == 403


def test_seis_templates_provisorios_bloqueados_sem_override(
    client, auth, medical_case
):
    catalog = client.get(
        "/api/v1/laudos/templates?catalog=admin", headers=auth("admin")
    )
    assert catalog.status_code == 200
    provisional = [
        item for item in catalog.json() if item["codigo"] in PROVISIONAL_CODES
    ]
    assert len(provisional) == 6
    assert {item["codigo"] for item in provisional} == PROVISIONAL_CODES
    for item in provisional:
        assert item["status"] == "draft"
        assert item["clinically_approved"] is False
        assert item["texto_completo"] == PROVISIONAL_WARNING

    clinical = client.get(
        "/api/v1/laudos/templates?catalog=clinical",
        headers=medical_case["doctor_auth"],
    )
    assert all(
        item["codigo"] not in PROVISIONAL_CODES for item in clinical.json()
    )
    denied = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/compor",
        json={
            "template_id": provisional[0]["id"],
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 1,
            "placement": "topo",
        },
        headers=medical_case["doctor_auth"],
    )
    assert denied.status_code == 409
    assert denied.json()["erro"]["codigo"] == "template_nao_aprovado"


def test_override_provisorio_e_exclusivo_de_teste_dev(
    client, medical_case, monkeypatch
):
    monkeypatch.setenv(
        "M15_REPORTS_TEST_ALLOW_PROVISIONAL_TEMPLATES", "true"
    )
    get_settings.cache_clear()
    templates = client.get(
        "/api/v1/laudos/templates?catalog=clinical",
        headers=medical_case["doctor_auth"],
    )
    provisional = [
        item for item in templates.json() if item["codigo"] in PROVISIONAL_CODES
    ]
    assert len(provisional) == 6
    response = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/compor",
        json={
            "template_id": provisional[0]["id"],
            "interpretation_text": "TESTE - APAGAR override provisório explícito.",
            "page_number": 1,
            "placement": "rodape",
        },
        headers=medical_case["doctor_auth"],
    )
    assert response.status_code == 200, response.text
    with pytest.raises(ValueError, match="proibido em prod"):
        Settings(
            env="prod",
            auth_secret="abcdefghijklmnopqrstuvwxyz0123456789!@#",
            reports_test_allow_provisional_templates=True,
        )


def test_template_aprovado_entra_como_nova_revisao_sem_reescrita(
    client, auth, db, medical_case
):
    catalog = client.get(
        "/api/v1/laudos/templates?catalog=admin", headers=auth("admin")
    ).json()
    old = next(
        item for item in catalog if item["codigo"] == "NORMAL_PROVISORIO"
    )
    revision = client.patch(
        f"/api/v1/laudos/templates/{old['id']}",
        json={
            "titulo": old["titulo"],
            "texto_tooltip": "TESTE - APAGAR aprovação sintética",
            "texto_completo": SYNTH_TEMPLATE_BODY,
            "ativo": True,
            "status": "approved",
            "clinically_approved": True,
        },
        headers=auth("admin"),
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()["versao"] == 2
    assert revision.json()["supersedes_template_id"] == old["id"]
    db.expire_all()
    persisted_old = db.get(ReportTemplate, old["id"])
    assert persisted_old.versao == 1
    assert persisted_old.texto_completo == PROVISIONAL_WARNING
    assert persisted_old.clinically_approved is False
    clinical = client.get(
        "/api/v1/laudos/templates?catalog=clinical",
        headers=medical_case["doctor_auth"],
    ).json()
    selected = next(
        item for item in clinical if item["codigo"] == "NORMAL_PROVISORIO"
    )
    assert selected["id"] == revision.json()["id"]
    assert selected["versao"] == 2


def test_versoes_e_eventos_sao_imutaveis_na_camada_orm(
    client, db, medical_case
):
    composed = _compose(client, medical_case)
    assert composed.status_code == 200
    version_id = composed.json()["versoes"][0]["id"]
    db.expire_all()
    version = db.get(ReportDocumentVersion, version_id)
    version.interpretation_text_snapshot = "TENTATIVA DE REESCRITA"
    with pytest.raises(ValueError, match="imutável"):
        db.commit()
    db.rollback()

    event = db.execute(select(ReportAssignmentEvent)).scalars().first()
    event.reason_code = "assignment_correction"
    with pytest.raises(ValueError, match="imutável"):
        db.commit()
    db.rollback()


def test_auditoria_nao_recebe_paciente_medico_filename_ou_texto(
    client, auth, db, medical_case, person
):
    assert _compose(client, medical_case).status_code == 200
    assert client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/preparar-assinatura",
        headers=medical_case["doctor_auth"],
    ).status_code == 200
    db.expire_all()
    logs = db.execute(
        select(AuditLog).where(AuditLog.entidade == "report_documents")
    ).scalars().all()
    dump = repr(
        [
            {
                "acao": row.acao,
                "entidade_id": row.entidade_id,
                "detalhes": row.detalhes,
            }
            for row in logs
        ]
    )
    for forbidden in (
        person["nome_completo"],
        "TESTE APAGAR Profissional Um",
        "00123",
        "TESTE-APAGAR.pdf",
        SYNTH_INTERPRETATION,
        SYNTH_TEMPLATE_BODY,
    ):
        assert forbidden not in dump
    assert "physician_profile_id" in dump
    assert "signature_status" in dump


def _materialize_synthetic_signed(db, medical_case) -> tuple[str, str]:
    """Cria evidência assinada somente dentro da fixture isolada."""

    from app.routers import reports as reports_router

    db.expire_all()
    document = db.get(ReportDocument, medical_case["document"]["id"])
    profile = db.get(PhysicianProfile, medical_case["profile"]["id"])
    pending = db.get(ReportDocumentVersion, document.current_version_id)
    stored = reports_router._read_stored_version(pending)
    with report_publication_transaction(db) as publication:
        signed = reports_router._store_new_version(
            db,
            publication=publication,
            document=document,
            exam_id=document.spirometry_exam_id,
            kind="assinado",
            data=stored.data,
            created_by_user_id=medical_case["doctor"].id,
            template_id=pending.template_id,
            template_code_snapshot=pending.template_code_snapshot,
            template_version_snapshot=pending.template_version_snapshot,
            template_text_snapshot=pending.template_text_snapshot,
            interpretation_text_snapshot=pending.interpretation_text_snapshot,
            physician_profile_id_snapshot=pending.physician_profile_id_snapshot,
            physician_name_snapshot=pending.physician_name_snapshot,
            physician_crm_number_snapshot=pending.physician_crm_number_snapshot,
            physician_crm_state_snapshot=pending.physician_crm_state_snapshot,
            physician_rqe_snapshot=pending.physician_rqe_snapshot,
            origin_type_snapshot=pending.origin_type_snapshot,
            origin_label_snapshot=pending.origin_label_snapshot,
            origin_partner_unit_id_snapshot=(
                pending.origin_partner_unit_id_snapshot
            ),
            footer_template_id=pending.footer_template_id,
            footer_code_snapshot=pending.footer_code_snapshot,
            footer_version_snapshot=pending.footer_version_snapshot,
            footer_text_snapshot=pending.footer_text_snapshot,
            issued_at_snapshot=pending.issued_at_snapshot,
            page_number=pending.page_number,
            placement=pending.placement,
        )
        now = datetime.now(timezone.utc)
        document.status = "assinado"
        document.signature_status = "assinada"
        document.signed_at = now
        document.current_version_id = signed.id
        db.add(
            ReportSignature(
                report_document_version_id=signed.id,
                provider="TEST_PROVIDER_ISOLATED_ONLY",
                status="assinada",
                external_reference="TEST-REF-NOT-REAL",
                requested_by_user_id=medical_case["doctor"].id,
                requested_at=now,
                completed_at=now,
                verification_metadata={
                    "qualified_signature": True,
                    "standard": "PAdES",
                    "trust_chain": "ICP-Brasil",
                    "signer_physician_profile_id": profile.id,
                    "document_sha256": signed.sha256,
                },
            )
        )
        publication.commit()
    return document.id, signed.id


def test_correcao_exige_evidencia_qualificada_e_cria_documento_separado(
    client, db, medical_case
):
    assert _compose(client, medical_case).status_code == 200
    prepared = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/preparar-assinatura",
        headers=medical_case["doctor_auth"],
    )
    assert prepared.status_code == 200
    blocked = client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/nova-versao-corretiva",
        json={"reason_code": "clinical_correction"},
        headers=medical_case["doctor_auth"],
    )
    assert blocked.status_code == 409
    assert blocked.json()["erro"]["codigo"] == "laudo_nao_assinado"

    # Fixture isolada: materializa evidência sintética completa diretamente
    # no banco. Nenhum provider/mock de runtime devolve sucesso.
    document_id, signed_id = _materialize_synthetic_signed(db, medical_case)
    document = db.get(ReportDocument, document_id)
    predecessor_updated_at = document.updated_at
    db.rollback()
    correction = client.post(
        f"/api/v1/laudos/{document_id}/nova-versao-corretiva",
        json={"reason_code": "clinical_correction"},
        headers=medical_case["doctor_auth"],
    )
    assert correction.status_code == 201, correction.text
    assert correction.json()["id"] != document_id
    assert correction.json()["corrects_document_id"] == document_id
    assert correction.json()["status"] == "atribuido"
    db.expire_all()
    predecessor = db.get(ReportDocument, document_id)
    assert predecessor.status == "assinado"
    assert predecessor.current_version_id == signed_id
    assert predecessor.updated_at.replace(tzinfo=None) == predecessor_updated_at.replace(tzinfo=None)


def test_falha_concorrente_da_corretiva_remove_somente_o_novo_pdf(
    client, db, medical_case, monkeypatch
):
    assert _compose(client, medical_case).status_code == 200
    assert client.post(
        f"/api/v1/laudos/{medical_case['document']['id']}/preparar-assinatura",
        headers=medical_case["doctor_auth"],
    ).status_code == 200
    document_id, _signed_id = _materialize_synthetic_signed(db, medical_case)
    db.rollback()
    storage_root = get_settings().resolved_reports_storage_dir()
    files_before = {
        path.relative_to(storage_root)
        for path in storage_root.rglob("*")
        if path.is_file()
    }

    def duplicate_correction_commit(_publication):
        raise IntegrityError(
            "synthetic corrective uniqueness",
            {},
            RuntimeError("race"),
        )

    monkeypatch.setattr(
        ReportPublicationTransaction,
        "commit",
        duplicate_correction_commit,
    )
    response = client.post(
        f"/api/v1/laudos/{document_id}/nova-versao-corretiva",
        json={"reason_code": "technical_document_correction"},
        headers=medical_case["doctor_auth"],
    )
    assert response.status_code == 409
    assert response.json()["erro"]["codigo"] == "laudo_ja_possui_corretiva"
    files_after = {
        path.relative_to(storage_root)
        for path in storage_root.rglob("*")
        if path.is_file()
    }
    assert files_after == files_before
    assert db.execute(
        select(func.count(ReportDocument.id))
    ).scalar_one() == 1
