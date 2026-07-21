"""M15.7 — responsáveis legais, suplemento e financeiro (dados sintéticos)."""

from datetime import date

import pytest
from sqlalchemy import select

from app.migration.executor import build_execution_plan
from app.migration.multisheet import (
    MultiSheetError,
    decide_multi_sheet_review,
    multi_sheet_review_queue,
    run_multi_sheet_dry_run,
)
from app.migration.operational_completion import (
    ADMIN_TOTAL_CENTS,
    OperationalCompletionError,
    build_admin_supplement,
    build_financial_reconstruction,
    load_immutable_private_json,
    preview_admin_supplement,
    preview_financial_reconstruction,
    write_immutable_private_json,
)
from app.models import (
    AuditLog,
    Person,
    PersonContact,
    PersonRelationship,
    SpirometryExam,
)
from app.services.relationships import RelationshipError, create_relationship
from tests._multisheet_fixtures import synthetic_row, write_raw_envelope


def _create_person(client, auth, name: str, contacts=None, consent=None):
    payload = {"nome_completo": name, "contatos": contacts or []}
    if consent:
        payload["consentimento_whatsapp"] = consent
    response = client.post(
        "/api/v1/pessoas", json=payload, headers=auth("operacional")
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_minor_guardian_api_idempotent_authorized_and_contact_separate(
    client, auth, db,
):
    minor = _create_person(client, auth, "Synthetic Minor Alpha")
    guardian = _create_person(
        client,
        auth,
        "Synthetic Guardian Alpha",
        contacts=[{
            "tipo": "whatsapp",
            "valor": "(21) 0000-9301",
            "principal": True,
        }],
        consent="concedido",
    )
    url = f"/api/v1/pessoas/{minor['id']}/responsaveis"
    payload = {
        "guardian_person_id": guardian["id"],
        "relationship_type": "mother",
        "is_legal_guardian": True,
        "active": True,
    }
    assert client.post(url, json=payload, headers=auth("leitura")).status_code == 403
    first = client.post(url, json=payload, headers=auth("operacional"))
    replay = client.post(url, json=payload, headers=auth("operacional"))
    assert first.status_code == replay.status_code == 201
    assert first.json()["replay"] is False
    assert replay.json()["replay"] is True
    assert first.json()["id"] == replay.json()["id"]

    followup = client.post(
        "/api/v1/followups",
        json={
            "patient_person_id": minor["id"],
            "contact_person_id": guardian["id"],
            "tipo": "manual",
        },
        headers=auth("operacional"),
    )
    assert followup.status_code == 201, followup.text
    body = followup.json()
    assert body["patient_person_id"] == minor["id"]
    assert body["contact_person_id"] == guardian["id"]

    exam = client.post(
        "/api/v1/espirometrias",
        json={"person_id": minor["id"], "status": "Aguardando"},
        headers=auth("operacional"),
    )
    assert exam.status_code == 201, exam.text
    assert exam.json()["person_id"] == minor["id"]

    db.expire_all()
    assert not db.execute(
        select(PersonContact).where(PersonContact.person_id == minor["id"])
    ).scalars().all()
    assert len(db.execute(
        select(PersonContact).where(PersonContact.person_id == guardian["id"])
    ).scalars().all()) == 1
    assert len(db.execute(select(PersonRelationship)).scalars().all()) == 1
    assert len([
        item for item in db.execute(select(AuditLog)).scalars().all()
        if item.acao == "pessoa.relacionamento_criado"
    ]) == 1


def test_relationship_rejects_same_person_and_payload_conflict(db):
    person = Person(
        public_code="PES-900001",
        nome_completo="Synthetic Person",
        nome_normalizado="synthetic person",
    )
    guardian = Person(
        public_code="PES-900002",
        nome_completo="Synthetic Guardian",
        nome_normalizado="synthetic guardian",
    )
    db.add_all([person, guardian])
    db.flush()
    with pytest.raises(RelationshipError, match="minor_and_guardian_must_differ"):
        create_relationship(
            db,
            minor_person_id=person.id,
            guardian_person_id=person.id,
            relationship_type="other",
            is_legal_guardian=False,
        )
    create_relationship(
        db,
        minor_person_id=person.id,
        guardian_person_id=guardian.id,
        relationship_type="legal_guardian",
        is_legal_guardian=True,
    )
    with pytest.raises(RelationshipError, match="relationship_payload_conflict"):
        create_relationship(
            db,
            minor_person_id=person.id,
            guardian_person_id=guardian.id,
            relationship_type="legal_guardian",
            is_legal_guardian=False,
        )


def test_structured_minor_decision_builds_patient_guardian_and_exam(db, users, tmp_path):
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "crm_espirometria",
        "alias": "Synthetic Exams",
        "rows": [synthetic_row(
            "crm_espirometria",
            exame_id="EX-SYN-MINOR-001",
            primeiro_nome="Synthetic Minor Beta",
            nome_responsavel_legal="Synthetic Guardian Beta",
            telefone_responsavel_legal="(21) 0000-9302",
            tipo_relacao_responsavel="mother",
            data_exame="2026-07-15",
        )],
    }])
    dry = run_multi_sheet_dry_run(
        db, envelope, base_dir=tmp_path, user_id=users["admin"].id
    )
    queue = multi_sheet_review_queue(db, dry["batch_id"])
    item = next(value for value in queue["items"]
                if value["category"] == "exame_orfao")
    decide_multi_sheet_review(
        db,
        dry["batch_id"],
        item["private_reference_token"],
        "create_minor_patient_with_guardian",
        item["mapping_version"],
        users["admin"].id,
    )
    plan = build_execution_plan(
        db, envelope, dry["batch_id"], base_dir=tmp_path
    )
    assert plan.bloqueios == []
    assert plan.manifesto() == {
        "people": 2,
        "person_contacts": 1,
        "person_relationships": 1,
        "spirometry_exams": 1,
    }
    minor = next(op for op in plan.ops
                 if op.entidade == "people" and "minor-decision" in op.chave)
    guardian = next(op for op in plan.ops
                    if op.entidade == "people" and "guardian-decision" in op.chave)
    contact = next(op for op in plan.ops if op.entidade == "person_contacts")
    exam = next(op for op in plan.ops if op.entidade == "spirometry_exams")
    assert contact.refs["person_id"] == guardian.chave
    assert contact.refs["person_id"] != minor.chave
    assert exam.refs["person_id"] == minor.chave


def test_structured_minor_decision_rejected_for_incompatible_category(
    db, users, tmp_path,
):
    envelope = write_raw_envelope(tmp_path, [{
        "kind": "crm_espirometria",
        "rows": [synthetic_row(
            "crm_espirometria",
            exame_id="EX-SYN-DATE-001",
            primeiro_nome="Synthetic Date Person",
            data_exame="invalid-date",
        )],
    }])
    dry = run_multi_sheet_dry_run(db, envelope, base_dir=tmp_path)
    item = next(value for value in multi_sheet_review_queue(
        db, dry["batch_id"]
    )["items"] if value["category"] == "data_invalida_ativa")
    with pytest.raises(MultiSheetError) as exc:
        decide_multi_sheet_review(
            db,
            dry["batch_id"],
            item["private_reference_token"],
            "create_minor_patient_with_guardian",
            item["mapping_version"],
            users["admin"].id,
        )
    assert exc.value.codigo == "decisao_incompativel_com_categoria"


def test_financial_reconstruction_exact_integer_cents_and_pastore_separate():
    fixed = [
        {"examination_id": "EX-SYN-FIX-01", "amount_cents": 22_000},
        {"examination_id": "EX-SYN-FIX-02", "amount_cents": 22_000},
    ]
    remaining = [
        {
            "examination_id": f"EX-SYN-{index:02d}",
            "examination_date": date(2026, 7, index).isoformat(),
        }
        for index in range(1, 12)
    ]
    document = build_financial_reconstruction(
        version="synthetic-v1",
        fixed_exam_mappings=fixed,
        remaining_exam_mappings=list(reversed(remaining)),
        pastore_exam_ids=["EX-SYN-PASTORE-01"],
    )
    preview = preview_financial_reconstruction(document)
    assert preview["ok"] is True
    assert preview["assignment_count"] == 13
    assert preview["assigned_total_cents"] == ADMIN_TOTAL_CENTS
    assert [item["amount_cents"] for item in document["assignments"][2:]] == (
        [23_680] * 10 + [23_679]
    )
    assert preview["pastore_adjustment_count"] == 1
    assert preview["operational_writes"] == 0


def test_financial_reconstruction_blocks_missing_and_rejects_duplicates():
    blocked = build_financial_reconstruction(
        version="synthetic-v2",
        fixed_exam_mappings=[],
        remaining_exam_mappings=[],
    )
    assert preview_financial_reconstruction(blocked)["state"] == (
        "blocked_missing_mappings"
    )
    blocked["expected_total_cents"] += 1
    with pytest.raises(
        OperationalCompletionError,
        match="financial_document_fingerprint_mismatch",
    ):
        preview_financial_reconstruction(blocked)
    with pytest.raises(OperationalCompletionError, match="financial_mapping_duplicate"):
        build_financial_reconstruction(
            version="synthetic-v3",
            fixed_exam_mappings=[
                {"examination_id": "EX-DUP", "amount_cents": 22_000},
                {"examination_id": "EX-DUP", "amount_cents": 22_000},
            ],
            remaining_exam_mappings=[],
        )


def test_admin_supplement_deterministic_private_immutable(tmp_path, monkeypatch):
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    facts = {
        "name": "Synthetic Administrative Person",
        "procedure": "spirometry",
        "location": "synthetic-location",
        "examination_date": "2026-07-15",
        "received_amount_cents": 22_000,
        "payment_method": "Pix",
        "phone": None,
    }
    first = build_admin_supplement(version="synthetic-v1", records=[facts])
    second = build_admin_supplement(version="synthetic-v1", records=[facts])
    assert first == second
    preview = preview_admin_supplement(first)
    assert preview["record_count"] == 1
    assert preview["operational_writes"] == 0
    path = write_immutable_private_json("synthetic-supplement.json", first)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_immutable_private_json(path.name) == first
    with pytest.raises(OperationalCompletionError, match="immutable_private_file_exists"):
        write_immutable_private_json("synthetic-supplement.json", first)
