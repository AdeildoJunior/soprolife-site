"""M24D — piloto interno controlado de laudos.

Cobre exclusivamente o que é novo nesta etapa: o contrato de três estados,
o gate de deploy dedicado do piloto (autorização + backup verificado), a
recuperação de laudo com médico suspenso (F3) e a remoção do oráculo de
existência entre papéis (F4). Autoverificação (F2), estados alcançáveis e
templates provisórios já têm cobertura equivalente em
test_m24c_medical_workflow.py — o que muda ali é reaproveitado, não
duplicado aqui.

Todos os nomes, CRMs, contas, exames e PDFs são marcadamente sintéticos.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models import (
    ReportAssignmentEvent,
    ReportDocument,
    ReportDocumentVersion,
    User,
)
from app.security import ROLE_ADMIN, ROLE_MEDICO, ensure_roles_exist, get_role, hash_password, issue_token

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import reports_go_live_gate as gate  # noqa: E402
import reports_pilot_backup as backup_tool  # noqa: E402

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


# --------------------------------------------------------------- config


def test_modo_disabled_e_o_padrao(monkeypatch):
    monkeypatch.delenv("M15_REPORTS_MODE", raising=False)
    assert Settings().reports_mode == "disabled"


def test_modo_invalido_falha_fechado(monkeypatch):
    monkeypatch.setenv("M15_REPORTS_MODE", "beta")
    with pytest.raises(Exception):
        Settings()


# ------------------------------------------------------- gate: piloto


def _synthetic_repo(tmp_path: Path, *, reports_enabled: bool) -> Path:
    repo = tmp_path / "synthetic-repo"
    config = repo / "painel-soprolife/data/m15-config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "reports_enabled": reports_enabled,
                "reports_mode": "pilot" if reports_enabled else "disabled",
                "api_base": "/painel-soprolife/api/m15",
            }
        ),
        encoding="utf-8",
    )
    return repo


def _private_root(tmp_path: Path, name: str = "synthetic-private-reports") -> Path:
    root = tmp_path / name
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _https_responses(*, enabled: bool):
    api_status = 401 if enabled else 503
    api_code = "http_401" if enabled else "relatorios_desabilitados"
    return {
        "https://pilot-gate.example.invalid" + gate.REPORTS_PANEL_PATH: (
            200,
            b'<section id="laudos-espirometria"></section>'
            b'<script src="./js/report-workflow.js"></script>',
        ),
        "https://pilot-gate.example.invalid" + gate.REPORTS_CONFIG_PATH: (
            200,
            json.dumps(
                {"reports_enabled": enabled, "api_base": gate.REPORTS_API_BASE}
            ).encode(),
        ),
        "https://pilot-gate.example.invalid" + gate.REPORTS_API_PATH: (
            api_status,
            json.dumps({"erro": {"codigo": api_code}}).encode(),
        ),
    }


def _getter(responses):
    def fake(url, _deadline):
        return responses[url]

    return fake


def _write_manifest(tmp_path: Path, *, dump: Path, archive: Path, created_at=None) -> Path:
    manifest = backup_tool.build_manifest(
        postgresql_dump_path=dump,
        storage_archive_path=archive,
        report_documents=2,
        report_document_versions=3,
        physician_profiles=1,
        created_at=created_at,
    )
    path = tmp_path / "manifest.json"
    backup_tool.write_manifest_atomic(path, manifest)
    return path


def _artifacts(tmp_path: Path) -> tuple[Path, Path]:
    dump = tmp_path / "pg.dump"
    dump.write_bytes(b"synthetic-dump")
    archive = tmp_path / "storage.tar"
    archive.write_bytes(b"synthetic-archive")
    return dump, archive


def _pilot_preflight(repo, root, manifest, *, unit_text=None, **overrides):
    values = {
        "repo_root": repo,
        "mode_value": "pilot",
        "backend_flag": "true",
        "pilot_authorization": gate.PILOT_AUTHORIZATION_PHRASE,
        "storage_root_value": str(root),
        "backup_manifest_path": str(manifest),
        "effective_unit_text": unit_text
        or f"[Service]\nReadWritePaths=/unrelated/var {root}\n",
        "expected_uid": os.getuid(),
        "expected_gid": os.getgid(),
        "https_base_url": "https://pilot-gate.example.invalid",
        "http_get": _getter(_https_responses(enabled=True)),
    }
    values.update(overrides)
    return gate.check_pilot_preflight(**values)


def test_pilot_preflight_aprova_quando_tudo_esta_correto(tmp_path):
    repo = _synthetic_repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    dump, archive = _artifacts(tmp_path)
    manifest = _write_manifest(tmp_path, dump=dump, archive=archive)
    result = _pilot_preflight(repo, root, manifest)
    assert result.enabled is True
    assert result.storage_root == root


@pytest.mark.parametrize(
    "overrides,expected_code",
    [
        ({"mode_value": "disabled"}, "reports_pilot_mode_not_selected"),
        ({"mode_value": "production"}, "reports_pilot_mode_not_selected"),
        ({"mode_value": None}, "reports_pilot_mode_not_selected"),
        ({"backend_flag": "false"}, "reports_pilot_backend_flag_missing"),
        ({"pilot_authorization": None}, "reports_pilot_authorization_missing"),
        (
            {"pilot_authorization": "HABILITAR PILOTO DE LAUDOS "},
            "reports_pilot_authorization_missing",
        ),
        (
            {"pilot_authorization": "AUTORIZO GO-LIVE DE LAUDOS"},
            "reports_pilot_authorization_missing",
        ),
    ],
)
def test_pilot_preflight_recusa_condicoes_incompletas(
    tmp_path, overrides, expected_code
):
    repo = _synthetic_repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    dump, archive = _artifacts(tmp_path)
    manifest = _write_manifest(tmp_path, dump=dump, archive=archive)
    with pytest.raises(gate.ReportsGateError) as caught:
        _pilot_preflight(repo, root, manifest, **overrides)
    assert str(caught.value) == expected_code


def test_general_m15_flag_alone_never_authorizes_pilot(tmp_path, monkeypatch):
    monkeypatch.setenv("SOPROLIFE_M15_GO_LIVE", "YES")
    repo = _synthetic_repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    dump, archive = _artifacts(tmp_path)
    manifest = _write_manifest(tmp_path, dump=dump, archive=archive)
    with pytest.raises(gate.ReportsGateError) as caught:
        _pilot_preflight(repo, root, manifest, pilot_authorization=None)
    assert str(caught.value) == "reports_pilot_authorization_missing"


def test_pilot_preflight_recusa_readwritepath_ausente_ou_amplo(tmp_path):
    repo = _synthetic_repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    dump, archive = _artifacts(tmp_path)
    manifest = _write_manifest(tmp_path, dump=dump, archive=archive)
    with pytest.raises(gate.ReportsGateError) as caught:
        _pilot_preflight(
            repo, root, manifest, unit_text="[Service]\nReadWritePaths=/other\n"
        )
    assert str(caught.value) == "systemd_exact_storage_readwritepath_missing"

    broad = f"[Service]\nReadWritePaths={root} {root.parent}\n"
    with pytest.raises(gate.ReportsGateError) as caught:
        _pilot_preflight(repo, root, manifest, unit_text=broad)
    assert str(caught.value) == "systemd_broad_writable_parent_forbidden"


def test_pilot_preflight_recusa_backup_manifesto_ausente(tmp_path):
    repo = _synthetic_repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    missing_manifest = tmp_path / "missing-manifest.json"
    with pytest.raises(gate.ReportsGateError) as caught:
        _pilot_preflight(repo, root, missing_manifest)
    assert str(caught.value) == "reports_backup_manifest_unreadable"

    with pytest.raises(gate.ReportsGateError) as caught:
        _pilot_preflight(repo, root, missing_manifest, backup_manifest_path="")
    assert str(caught.value) == "reports_backup_manifest_missing"


def test_production_mode_permanece_bloqueado_pelo_gate_de_producao(tmp_path):
    repo = _synthetic_repo(tmp_path, reports_enabled=True)
    root = _private_root(tmp_path)
    with pytest.raises(gate.ReportsGateError) as caught:
        gate.check_preflight(
            repo_root=repo,
            backend_flag="true",
            reports_authorization=gate.REPORTS_AUTHORIZATION_PHRASE,
            storage_root_value=str(root),
            backup_attestation=gate.BACKUP_ATTESTATION_PHRASE,
            effective_unit_text=f"[Service]\nReadWritePaths={root}\n",
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            https_base_url="https://pilot-gate.example.invalid",
            http_get=_getter(_https_responses(enabled=True)),
        )
    assert str(caught.value) == gate.M24C_PRODUCTION_BLOCKER


# ---------------------------------------------------- manifesto de backup


def test_manifest_builder_recusa_artefato_ausente_symlink_ou_dono_errado(tmp_path):
    dump, archive = _artifacts(tmp_path)

    with pytest.raises(backup_tool.BackupManifestError):
        backup_tool.build_manifest(
            postgresql_dump_path=tmp_path / "missing.dump",
            storage_archive_path=archive,
            report_documents=0,
            report_document_versions=0,
            physician_profiles=0,
        )

    link = tmp_path / "dump-link"
    link.symlink_to(dump)
    with pytest.raises(backup_tool.BackupManifestError):
        backup_tool.build_manifest(
            postgresql_dump_path=link,
            storage_archive_path=archive,
            report_documents=0,
            report_document_versions=0,
            physician_profiles=0,
        )

    with pytest.raises(backup_tool.BackupManifestError):
        backup_tool.build_manifest(
            postgresql_dump_path=dump,
            storage_archive_path=archive,
            report_documents=-1,
            report_document_versions=0,
            physician_profiles=0,
        )


def test_manifest_escrito_e_0600_e_nunca_sobrescreve(tmp_path):
    dump, archive = _artifacts(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = backup_tool.build_manifest(
        postgresql_dump_path=dump,
        storage_archive_path=archive,
        report_documents=1,
        report_document_versions=1,
        physician_profiles=1,
    )
    backup_tool.write_manifest_atomic(manifest_path, manifest)
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    with pytest.raises(backup_tool.BackupManifestError):
        backup_tool.write_manifest_atomic(manifest_path, manifest)


def test_gate_recusa_manifesto_com_hash_divergente_ou_expirado(tmp_path):
    dump, archive = _artifacts(tmp_path)
    manifest = backup_tool.build_manifest(
        postgresql_dump_path=dump,
        storage_archive_path=archive,
        report_documents=1,
        report_document_versions=1,
        physician_profiles=1,
    )
    manifest["postgresql_dump_sha256"] = "0" * 64
    tampered = tmp_path / "tampered-manifest.json"
    tampered.write_text(json.dumps(manifest), encoding="utf-8")
    os.chmod(tampered, 0o600)
    with pytest.raises(gate.ReportsGateError) as caught:
        gate._validate_backup_manifest(
            str(tampered), expected_uid=os.getuid(), expected_gid=os.getgid()
        )
    assert str(caught.value) == "reports_backup_manifest_hash_mismatch"

    stale = backup_tool.build_manifest(
        postgresql_dump_path=dump,
        storage_archive_path=archive,
        report_documents=1,
        report_document_versions=1,
        physician_profiles=1,
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    stale_path = tmp_path / "stale-manifest.json"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    os.chmod(stale_path, 0o600)
    with pytest.raises(gate.ReportsGateError) as caught:
        gate._validate_backup_manifest(
            str(stale_path), expected_uid=os.getuid(), expected_gid=os.getgid()
        )
    assert str(caught.value) == "reports_backup_manifest_stale"


# -------------------------------------------------------- API: fixtures


@pytest.fixture(autouse=True)
def reports_pilot_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET",
        "m24d-synthetic-test-secret-only-0123456789-abcdef",
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _create_physician(db, *, suffix: str) -> tuple[User, dict]:
    ensure_roles_exist(db)
    user = User(
        email=f"medico-{suffix}@teste.local",
        nome=f"TESTE APAGAR Médico {suffix}",
        password_hash=hash_password("senha-medico-sintetica-123"),
    )
    user.roles.append(get_role(db, ROLE_MEDICO))
    db.add(user)
    db.commit()
    token = issue_token(user.id, user.password_hash)
    return user, {"Authorization": f"Bearer {token}"}


def _configure_and_verify(client, db, user_id: str, *, crm: str, name: str) -> dict:
    ensure_roles_exist(db)
    verifier = User(
        email=f"verificador-{crm}@teste.local",
        nome="TESTE APAGAR Verificador",
        password_hash=hash_password("senha-admin-sintetica-123"),
    )
    verifier.roles.append(get_role(db, ROLE_ADMIN))
    db.add(verifier)
    db.commit()
    headers = {
        "Authorization": f"Bearer {issue_token(verifier.id, verifier.password_hash)}"
    }
    response = client.patch(
        f"/api/v1/laudos/admin/medicos/{user_id}",
        json={
            "grant_physician_role": True,
            "professional_name": name,
            "crm_number": crm,
            "crm_state": "RJ",
            "verification_status": "verified",
            "verification_reference": f"CRM-VERIF-{crm}",
            "active": True,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["profile"]


def _exam(client, auth, person: dict, *, day: str = "2026-07-01") -> dict:
    response = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {"data_exame": day, "status": "Realizado"},
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()["espirometria"]


def _approved_template(client, auth, code: str) -> dict:
    response = client.post(
        "/api/v1/laudos/templates",
        json={
            "codigo": code,
            "titulo": "TESTE APAGAR Template",
            "texto_completo": "TESTE - APAGAR: texto controlado sintético.",
            "ativo": True,
            "status": "approved",
            "clinically_approved": True,
        },
        headers=auth("admin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, auth, exam: dict, profile: dict, *, suffix: str) -> dict:
    response = client.post(
        "/api/v1/laudos",
        data={
            "exam_code": exam["public_code"],
            "physician_profile_id": profile["id"],
            "origin_type": "coworking",
            "origin_label": f"unidade-teste-{suffix}",
            "origin_partner_unit_id": "",
        },
        files={"file": (f"TESTE-APAGAR-{suffix}.pdf", _minimal_pdf(), "application/pdf")},
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _operational_row(client, auth, document_id: str) -> dict:
    rows = client.get("/api/v1/laudos", headers=auth("operacional")).json()
    return next(row for row in rows if row["document_id"] == document_id)


# ------------------------------------------------ F4: oráculo de existência


def test_papel_sem_acesso_recebe_mesma_resposta_para_id_existente_e_inexistente(
    client, auth, db, person
):
    exam = _exam(client, auth, person)
    doctor, _doctor_auth = _create_physician(db, suffix="oracle")
    profile = _configure_and_verify(
        client, db, doctor.id, crm="90001", name="TESTE APAGAR Oracle"
    )
    document = _upload(client, auth, exam, profile, suffix="oracle")
    document_id = document["id"]
    fake_id = "00000000-0000-4000-8000-000000000000"

    for path in (
        f"/api/v1/laudos/{document_id}",
        f"/api/v1/laudos/{document_id}/assinatura",
    ):
        existing = client.get(path, headers=auth("leitura"))
        assert existing.status_code == 403
        assert existing.json()["erro"]["codigo"] == "permissao_insuficiente"

    for path in (
        f"/api/v1/laudos/{fake_id}",
        f"/api/v1/laudos/{fake_id}/assinatura",
    ):
        nonexistent = client.get(path, headers=auth("leitura"))
        assert nonexistent.status_code == 403
        assert nonexistent.json()["erro"]["codigo"] == "permissao_insuficiente"


def test_download_exige_papel_medico_antes_de_saber_se_id_existe(
    client, auth, db, person
):
    exam = _exam(client, auth, person)
    doctor, _doctor_auth = _create_physician(db, suffix="oracle-dl")
    profile = _configure_and_verify(
        client, db, doctor.id, crm="90002", name="TESTE APAGAR Oracle Download"
    )
    document = _upload(client, auth, exam, profile, suffix="oracle-dl")
    version_id = document["versoes"][0]["id"]
    fake_id = "00000000-0000-4000-8000-000000000001"

    real = client.get(
        f"/api/v1/laudos/{document['id']}/versoes/{version_id}/conteudo",
        headers=auth("leitura"),
    )
    fake = client.get(
        f"/api/v1/laudos/{fake_id}/versoes/{fake_id}/conteudo",
        headers=auth("leitura"),
    )
    assert real.status_code == fake.status_code == 403
    assert (
        real.json()["erro"]["codigo"]
        == fake.json()["erro"]["codigo"]
        == "papel_medico_explicito_necessario"
    )


# ---------------------------------------- F3: recuperação de médico suspenso


def test_recuperacao_de_medico_suspenso_preserva_evidencia_e_reabre_atribuido(
    client, auth, db, person
):
    exam = _exam(client, auth, person)
    doctor, doctor_auth = _create_physician(db, suffix="recuperacao")
    profile = _configure_and_verify(
        client, db, doctor.id, crm="90010", name="TESTE APAGAR Recuperação"
    )
    document = _upload(client, auth, exam, profile, suffix="recuperacao")
    document_id = document["id"]

    template = _approved_template(client, auth, "M24D_TEMPLATE_RECUP")

    composed = client.post(
        f"/api/v1/laudos/{document_id}/compor",
        json={
            "template_id": template["id"],
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 1,
            "placement": "topo",
        },
        headers=doctor_auth,
    )
    assert composed.status_code == 200, composed.text
    original_version_id = composed.json()["versoes"][0]["id"]

    # médico deixa de ser elegível DEPOIS do primeiro rascunho clínico.
    second_admin = User(
        email="segundo-admin-recup@teste.local",
        nome="TESTE APAGAR Segundo Admin",
        password_hash=hash_password("senha-admin-sintetica-123"),
    )
    second_admin.roles.append(get_role(db, ROLE_ADMIN))
    db.add(second_admin)
    db.commit()
    second_admin_headers = {
        "Authorization": f"Bearer {issue_token(second_admin.id, second_admin.password_hash)}"
    }
    suspend = client.patch(
        f"/api/v1/laudos/admin/medicos/{doctor.id}",
        json={"verification_status": "rejected"},
        headers=second_admin_headers,
    )
    assert suspend.status_code == 200, suspend.text

    # a reatribuição comum permanece bloqueada nesse ponto (por desenho).
    active_assignment_id = _operational_row(client, auth, document_id)[
        "assignment_id"
    ]
    second_doctor, _ = _create_physician(db, suffix="recuperacao-novo")
    second_profile = _configure_and_verify(
        client,
        db,
        second_doctor.id,
        crm="90011",
        name="TESTE APAGAR Recuperação Novo",
    )
    blocked = client.post(
        f"/api/v1/laudos/{document_id}/reatribuir",
        json={
            "physician_profile_id": second_profile["id"],
            "expected_assignment_id": active_assignment_id,
            "reason_code": "profile_suspended",
        },
        headers=auth("operacional"),
    )
    assert blocked.status_code == 409
    assert blocked.json()["erro"]["codigo"] == "reatribuicao_clinica_bloqueada"

    recovered = client.post(
        f"/api/v1/laudos/{document_id}/recuperar-medico-suspenso",
        json={
            "physician_profile_id": second_profile["id"],
            "expected_assignment_id": active_assignment_id,
        },
        headers=auth("admin"),
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "atribuido"

    db.expire_all()
    doc = db.get(ReportDocument, document_id)
    assert doc.status == "atribuido"
    assert doc.clinical_started_at is None

    old_version = db.get(ReportDocumentVersion, original_version_id)
    assert old_version is not None
    assert old_version.physician_name_snapshot == "TESTE APAGAR Recuperação"

    events = db.execute(
        select(ReportAssignmentEvent).where(
            ReportAssignmentEvent.report_document_id == document_id
        )
    ).scalars().all()
    assert any(event.event_type == "recovered_after_draft" for event in events)

    # admin comum (não-elegível) não pode recuperar sem reason_code fechado
    # nem sem papel admin.
    denied = client.post(
        f"/api/v1/laudos/{document_id}/recuperar-medico-suspenso",
        json={
            "physician_profile_id": second_profile["id"],
            "expected_assignment_id": active_assignment_id,
        },
        headers=auth("operacional"),
    )
    assert denied.status_code == 403


def test_recuperacao_recusada_quando_medico_ainda_elegivel(client, auth, db, person):
    exam = _exam(client, auth, person)
    doctor, doctor_auth = _create_physician(db, suffix="ainda-elegivel")
    profile = _configure_and_verify(
        client, db, doctor.id, crm="90020", name="TESTE APAGAR Ainda Elegível"
    )
    document = _upload(client, auth, exam, profile, suffix="ainda-elegivel")
    document_id = document["id"]

    template = _approved_template(client, auth, "M24D_TEMPLATE_ELEGIVEL")
    composed = client.post(
        f"/api/v1/laudos/{document_id}/compor",
        json={
            "template_id": template["id"],
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 1,
            "placement": "topo",
        },
        headers=doctor_auth,
    )
    assert composed.status_code == 200, composed.text

    active_assignment_id = _operational_row(client, auth, document_id)[
        "assignment_id"
    ]
    other_doctor, _ = _create_physician(db, suffix="ainda-elegivel-outro")
    other_profile = _configure_and_verify(
        client, db, other_doctor.id, crm="90021", name="TESTE APAGAR Outro"
    )
    denied = client.post(
        f"/api/v1/laudos/{document_id}/recuperar-medico-suspenso",
        json={
            "physician_profile_id": other_profile["id"],
            "expected_assignment_id": active_assignment_id,
        },
        headers=auth("admin"),
    )
    assert denied.status_code == 409
    assert denied.json()["erro"]["codigo"] == "medico_ainda_elegivel"


# ---------------------------------------------------- aviso do piloto


def test_pdf_composto_em_piloto_carrega_aviso_congelado_no_snapshot(
    client, auth, db, person
):
    exam = _exam(client, auth, person)
    doctor, doctor_auth = _create_physician(db, suffix="watermark")
    profile = _configure_and_verify(
        client, db, doctor.id, crm="90030", name="TESTE APAGAR Watermark"
    )
    document = _upload(client, auth, exam, profile, suffix="watermark")
    composed = client.post(
        f"/api/v1/laudos/{document['id']}/compor",
        json={
            "interpretation_text": SYNTH_INTERPRETATION,
            "page_number": 1,
            "placement": "rodape",
        },
        headers=doctor_auth,
    )
    assert composed.status_code == 200, composed.text
    version = composed.json()["versoes"][0]
    assert version["footer_code_snapshot"] == "PILOTO_INTERNO_NAO_ASSINADO"
    assert (
        "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE"
        in version["footer_text_snapshot"]
    )
