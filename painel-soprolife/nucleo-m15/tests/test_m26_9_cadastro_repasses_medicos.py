"""M26.9 — cadastro administrativo e repasses, somente fixtures sintéticas."""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models import (
    AuditLog,
    FinancialEntry,
    PartnerSettlement,
    Person,
    PhysicianProfile,
    PhysicianTransfer,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.security import get_role, hash_password, issue_token

API = "/api/v1"


def _user(db, email: str, name: str, role: str) -> tuple[User, dict]:
    user = User(email=email, nome=name, password_hash=hash_password("senha-sintetica-123"))
    user.roles.append(get_role(db, role))
    db.add(user)
    db.commit()
    return user, {"Authorization": f"Bearer {issue_token(user.id, user.password_hash)}"}


def _person(client, auth, name="Paciente Sintética M269 Silva"):
    response = client.post(
        f"{API}/pessoas",
        json={
            "nome_completo": name,
            "data_nascimento": "1980-01-02",
            "sexo": "feminino",
            "contatos": [
                {"tipo": "whatsapp", "valor": "(21) 0000-9269", "principal": True},
                {"tipo": "email", "valor": "paciente.m269@example.invalid"},
            ],
        },
        headers=auth("operacional"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_edita_sobrenome_e_auditoria_guarda_antes_depois(client, auth, db):
    person = _person(client, auth)
    response = client.patch(
        f"{API}/pessoas/{person['id']}/cadastro",
        json={"nome_completo": "Paciente Sintética M269 Souza"},
        headers=auth("admin"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["nome_completo"].endswith("Souza")

    row = db.execute(
        select(AuditLog).where(
            AuditLog.entidade_id == person["id"],
            AuditLog.acao == "pessoa.cadastro_campo_alterado",
        )
    ).scalar_one()
    assert row.user_id is not None
    assert row.ts_utc is not None
    assert row.detalhes == {
        "campo": "nome_completo",
        "valor_anterior": "Paciente Sintética M269 Silva",
        "valor_novo": "Paciente Sintética M269 Souza",
    }


def test_luiz_admin_e_conta_institucional_podem_editar(client, auth, db):
    person = _person(client, auth, "Paciente Sintética M269 Permissões")
    _luiz, luiz_auth = _user(db, "luiz.m269@example.invalid", "Luiz M269", "admin")
    response = client.patch(
        f"{API}/pessoas/{person['id']}/cadastro",
        json={"telefone": "(21) 0000-9270"},
        headers=luiz_auth,
    )
    assert response.status_code == 200
    assert client.get(
        f"{API}/financeiro/repasses-medicos?competencia=2026-09",
        headers=luiz_auth,
    ).status_code == 200

    _contact, contact_auth = _user(
        db, "contato@soprolife.com.br", "Contato SoproLife", "leitura"
    )
    response = client.patch(
        f"{API}/pessoas/{person['id']}/cadastro",
        json={"email": "corrigido.m269@example.invalid"},
        headers=contact_auth,
    )
    assert response.status_code == 200


def test_medica_nao_edita_cadastro_nem_acessa_financeiro(client, auth, db):
    person = _person(client, auth, "Paciente Sintética M269 Protegida")
    _doctor, doctor_auth = _user(db, "medica.m269@example.invalid", "Médica M269", "medico")
    assert client.patch(
        f"{API}/pessoas/{person['id']}/cadastro",
        json={"nome_completo": "Alteração Proibida"},
        headers=doctor_auth,
    ).status_code == 403
    assert client.get(
        f"{API}/financeiro/repasses-medicos", headers=doctor_auth
    ).status_code == 403


def test_edicao_nao_altera_ids_exame_laudo_ou_pdf(client, auth, db):
    created = _person(client, auth, "Paciente Sintética M269 Imutável")
    person = db.get(Person, created["id"])
    exam = SpirometryExam(
        public_code="ESP-992691",
        person_id=person.id,
        status="Realizado",
        data_exame=date(2026, 9, 1),
    )
    db.add(exam)
    db.flush()
    report = ReportDocument(
        public_code="LAU-992691",
        spirometry_exam_id=exam.id,
        status="atribuido",
        created_by_user_id=db.execute(select(User).where(User.email == "admin@teste.local")).scalar_one().id,
    )
    db.add(report)
    db.flush()
    version = ReportDocumentVersion(
        report_document_id=report.id,
        kind="original",
        version_number=1,
        storage_path="synthetic/m269.pdf",
        sha256="a" * 64,
        size_bytes=123,
        page_count=1,
        created_by_user_id=report.created_by_user_id,
    )
    db.add(version)
    db.commit()
    before = (person.id, exam.id, report.id, version.id, version.sha256, report.status)

    response = client.patch(
        f"{API}/pessoas/{person.id}/cadastro",
        json={"nome_completo": "Paciente Sintética M269 Corrigida"},
        headers=auth("admin"),
    )
    assert response.status_code == 200
    db.expire_all()
    after = (
        db.get(Person, person.id).id,
        db.get(SpirometryExam, exam.id).id,
        db.get(ReportDocument, report.id).id,
        db.get(ReportDocumentVersion, version.id).id,
        db.get(ReportDocumentVersion, version.id).sha256,
        db.get(ReportDocument, report.id).status,
    )
    assert after == before


def _doctor_profile(db, suffix: str = "1") -> tuple[User, PhysicianProfile]:
    user, _headers = _user(
        db, f"medica-repasse-{suffix}@example.invalid", f"Médica Repasse {suffix}", "medico"
    )
    profile = PhysicianProfile(
        user_id=user.id,
        professional_name=f"Dra. Sintética M269 {suffix}",
        crm_number=f"9269{suffix}",
        crm_state="RJ",
        active=True,
        verification_status="pending",
    )
    db.add(profile)
    db.commit()
    return user, profile


def _exam_and_document(
    db,
    profile: PhysicianProfile,
    creator: User,
    suffix: str,
    *,
    status: str,
    released_at: datetime | None,
) -> ReportDocument:
    person = Person(
        public_code=f"PES-8{suffix.zfill(5)}",
        nome_completo=f"Paciente Sintética Repasse {suffix}",
        nome_normalizado=f"paciente sintetica repasse {suffix}",
    )
    db.add(person)
    db.flush()
    exam = SpirometryExam(
        public_code=f"ESP-8{suffix.zfill(5)}",
        person_id=person.id,
        status="Realizado",
        data_exame=date(2026, 9, 1),
    )
    db.add(exam)
    db.flush()
    kwargs = {}
    if status == "em_elaboracao":
        kwargs["clinical_started_at"] = datetime(2026, 9, 1, tzinfo=timezone.utc)
    elif status == "assinatura_pendente":
        kwargs.update(
            clinical_started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            ready_for_signature_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            signature_status="assinatura_pendente",
            validation_code=f"M269P{suffix}",
        )
    elif released_at is not None:
        kwargs.update(
            clinical_started_at=released_at,
            ready_for_signature_at=released_at,
            signature_status="liberada_institucional",
            released_at=released_at,
            released_by_user_id=creator.id,
            released_physician_profile_id=profile.id,
            validation_code=f"M269R{suffix}",
        )
    report = ReportDocument(
        public_code=f"LAU-8{suffix.zfill(5)}",
        spirometry_exam_id=exam.id,
        status=status,
        created_by_user_id=creator.id,
        **kwargs,
    )
    db.add(report)
    db.commit()
    return report


def _eligible_case(db, users):
    _doctor, profile = _doctor_profile(db)
    creator = users["admin"]
    # 31/08 23:59 em Brasília: competência agosto.
    _exam_and_document(
        db, profile, creator, "01", status="liberado",
        released_at=datetime(2026, 9, 1, 2, 59, tzinfo=timezone.utc),
    )
    # 01/09 00:00 em Brasília: competência setembro.
    eligible = _exam_and_document(
        db, profile, creator, "02", status="liberado",
        released_at=datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc),
    )
    _exam_and_document(db, profile, creator, "03", status="atribuido", released_at=None)
    _exam_and_document(db, profile, creator, "04", status="em_elaboracao", released_at=None)
    _exam_and_document(db, profile, creator, "05", status="assinatura_pendente", released_at=None)
    return profile, eligible


def test_contagem_usa_released_at_e_nao_status_pre_assinatura(client, auth, db, users):
    profile, eligible = _eligible_case(db, users)
    response = client.get(
        f"{API}/financeiro/repasses-medicos?competencia=2026-09",
        headers=auth("gestor"),
    )
    assert response.status_code == 200, response.text
    row = response.json()["medicas"][0]
    assert row["physician_profile_id"] == profile.id
    assert row["quantidade_laudos_elegiveis"] == 1

    # A consulta não contém filtro de status: evolução documental não remove
    # o laudo cuja conclusão/released_at já existe.
    from app.services import medical_transfers
    import inspect
    source = inspect.getsource(medical_transfers.eligible_report_count)
    assert "ReportDocument.status" not in source
    assert "released_at" in source
    assert eligible.released_at is not None


def test_quantidade_vezes_unitario_registro_pagamento_duplicidade_e_historico(
    client, auth, db, users
):
    profile, _eligible = _eligible_case(db, users)
    profile_id = profile.id
    before_finance = len(db.execute(select(FinancialEntry)).scalars().all())
    before_pastore = len(db.execute(select(PartnerSettlement)).scalars().all())
    db.rollback()  # libera a leitura SQLite antes da sessão usada pela API
    payload = {
        "physician_profile_id": profile_id,
        "competencia": "2026-09",
        "expected_eligible_report_count": 1,
        "unit_amount": "37.50",
        "paid_amount": "0.00",
        "payment_date": None,
    }
    created = client.post(
        f"{API}/financeiro/repasses-medicos", json=payload, headers=auth("gestor")
    )
    assert created.status_code == 201, created.text
    row = created.json()["medicas"][0]
    assert row["total_calculado"] == "37.50"
    assert row["status"] == "Pendente"
    assert client.post(
        f"{API}/financeiro/repasses-medicos", json=payload, headers=auth("admin")
    ).status_code == 409

    paid = client.patch(
        f"{API}/financeiro/repasses-medicos/{row['id']}/pagamento",
        json={"paid_amount": "36.00", "payment_date": "2026-10-05"},
        headers=auth("admin"),
    )
    assert paid.status_code == 200, paid.text
    paid_row = paid.json()["medicas"][0]
    assert paid_row["status"] == "Pago"
    assert paid_row["valor_pago"] == "36.00"
    assert paid_row["data_pagamento"] == "2026-10-05"
    assert paid.json()["historico"][0]["id"] == row["id"]
    assert db.scalar(select(PhysicianTransfer.eligible_report_count)) == 1
    assert len(db.execute(select(FinancialEntry)).scalars().all()) == before_finance
    assert len(db.execute(select(PartnerSettlement)).scalars().all()) == before_pastore


def test_quantidade_desatualizada_e_recusada(client, auth, db, users):
    profile, _eligible = _eligible_case(db, users)
    response = client.post(
        f"{API}/financeiro/repasses-medicos",
        json={
            "physician_profile_id": profile.id,
            "competencia": "2026-09",
            "expected_eligible_report_count": 2,
            "unit_amount": "40.00",
        },
        headers=auth("gestor"),
    )
    assert response.status_code == 409
    assert db.execute(select(PhysicianTransfer)).scalars().all() == []


def test_tela_expoe_acoes_e_nao_inventa_valor():
    html = open("../index.html", encoding="utf-8").read()
    js = open("../js/medical-transfers.js", encoding="utf-8").read()
    assert "Editar cadastro" in open("../js/central-cadastros.js", encoding="utf-8").read()
    assert "Repasses médicos" in html and "Registrar repasse" in js
    assert "Informe o valor" in js
    assert "valor_unitario_padrao" not in js.lower()
