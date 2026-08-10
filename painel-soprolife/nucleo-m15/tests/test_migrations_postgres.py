"""Migração e concorrência em PostgreSQL 16 REAL.

Pulado apenas quando não há runtime/serviço disponível — defina
M15_TEST_POSTGRES_URL (ex.: postgresql+psycopg://postgres:senha@127.0.0.1:55432/m15_teste).
Execute o ciclo oficial com `bash scripts/test-postgres-efemero.sh`; ele usa e
remove exclusivamente o container descartável `m15-pg-teste`.
"""

import os
import pathlib
import threading
import time
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ids import allocate_public_code, new_uuid
from app.models import (
    FinancialEntry,
    Person,
    PhysicianProfile,
    ReportAssignment,
    ReportAssignmentEvent,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.normalize import normalize_name
from app.security import ROLE_MEDICO, ensure_roles_exist, get_role
from app.services.followup import schedule_followup
from app.services.idempotency import idempotent_create, payload_fingerprint

PG_URL = os.environ.get("M15_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="PostgreSQL de teste indisponível — defina M15_TEST_POSTGRES_URL",
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    return cfg


@pytest.fixture(scope="module")
def pg_engine():
    os.environ.pop("M15_DATABASE_URL", None)
    cfg = _alembic_config()
    command.downgrade(cfg, "base")   # estado limpo
    command.upgrade(cfg, "head")
    engine = create_engine(PG_URL)
    yield engine
    engine.dispose()


def test_ciclo_upgrade_downgrade_upgrade_pg(pg_engine):
    cfg = _alembic_config()
    command.downgrade(cfg, "base")
    engine = create_engine(PG_URL)
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set()
    command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables.keys())
    # alembic check: nenhum drift entre modelos e migração
    command.check(cfg)
    engine.dispose()


def test_fk_ciclica_e_preseed_pg(pg_engine):
    insp = inspect(pg_engine)
    fks = {fk["name"] for fk in insp.get_foreign_keys("partner_referrals")}
    assert "fk_partner_referrals_financial_entry_id_financial_entries" in fks
    with pg_engine.connect() as conn:
        prefixos = sorted(
            r[0] for r in conn.execute(text("SELECT prefix FROM code_sequences"))
        )
    # M25.20 — ancorado em `PREFIXES` e não num número fixo: a asserção passa
    # a provar que TODO prefixo emitido pela aplicação tem sequência
    # preseedada, em vez de apenas contar linhas. Uma entidade nova que
    # esqueça a migration falha aqui, e não na primeira alocação real.
    from app.ids import PREFIXES

    assert prefixos == sorted(PREFIXES.values())


def test_audit_append_only_trigger_pg(pg_engine):
    with pg_engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO audit_logs (ts_utc, acao) VALUES (now(), 'teste-trigger')"
        ))
        conn.commit()
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("UPDATE audit_logs SET acao='x' WHERE acao='teste-trigger'"))
        conn.rollback()
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("DELETE FROM audit_logs WHERE acao='teste-trigger'"))
        conn.rollback()


def test_codigos_publicos_concorrentes_pg(pg_engine):
    """4 threads × 5 alocações simultâneas -> 20 códigos únicos."""
    SessionLocal = sessionmaker(bind=pg_engine)
    results: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            for _ in range(5):
                code = allocate_public_code(session, "interactions")
                session.commit()
                results.append(code)
        except Exception as exc:  # pragma: no cover - diagnóstico
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(results) == 20
    assert len(set(results)) == 20


def test_idempotency_key_concorrente_pg(pg_engine):
    """Duas transações com a MESMA chave: uma cria, a outra recebe a existente."""
    SessionLocal = sessionmaker(bind=pg_engine)
    payload = {"tipo": "receita", "valor": "123.00"}
    key = "LAN-RACE-000001"
    outcome: list[tuple[str, bool]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)

            def factory(k, fingerprint):
                entry = FinancialEntry(
                    public_code=allocate_public_code(session, "financial_entries"),
                    tipo="receita", valor=123, moeda="BRL",
                    data_competencia_dia_assumido=False,
                    status="Pendente",
                    idempotency_key=k, idempotency_fingerprint=fingerprint,
                )
                session.add(entry)
                session.flush()
                return entry

            obj, ja_existia = idempotent_create(
                session, FinancialEntry, key, payload, factory)
            session.commit()
            outcome.append((obj.id, ja_existia))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(outcome) == 2
    ids = {o[0] for o in outcome}
    assert len(ids) == 1  # mesmo registro para as duas
    assert sorted(o[1] for o in outcome) == [False, True]
    assert payload_fingerprint(payload)  # fingerprint estável


def test_followup_nulo_concorrente_pg(pg_engine):
    """Duas transações criando follow-up manual (origem NULL) -> um só pendente."""
    SessionLocal = sessionmaker(bind=pg_engine)
    setup = SessionLocal()
    person = Person(
        public_code=allocate_public_code(setup, "people"),
        nome_completo="Corrida Followup PG",
        nome_normalizado=normalize_name("Corrida Followup PG"),
    )
    setup.add(person)
    setup.commit()
    person_id = person.id
    setup.close()

    outcome: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            p = session.get(Person, person_id)
            fup, motivo = schedule_followup(session, p, "manual")
            session.commit()
            outcome.append(f"{fup.id}:{motivo}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(outcome) == 2
    ids = {o.split(":")[0] for o in outcome}
    assert len(ids) == 1
    motivos = sorted(o.split(":")[1] for o in outcome)
    assert motivos == ["criado", "ja_existente"]


def _setup_report_concurrency(pg_engine, suffix: str, *, finalized: bool = False):
    session = sessionmaker(bind=pg_engine, expire_on_commit=False)()
    user = User(
        email=f"m24a-concurrency-{suffix}@teste.local",
        nome=f"Teste M24A {suffix}",
        password_hash="hash-sintetico-nao-utilizavel",
    )
    person = Person(
        public_code=allocate_public_code(session, "people"),
        nome_completo=f"Pessoa Sintetica M24A {suffix}",
        nome_normalizado=f"pessoa sintetica m24a {suffix}",
    )
    session.add_all([user, person])
    session.flush()
    exam = SpirometryExam(
        public_code=allocate_public_code(session, "spirometry_exams"),
        person_id=person.id,
    )
    session.add(exam)
    session.flush()
    document = ReportDocument(
        public_code=allocate_public_code(session, "report_documents"),
        spirometry_exam_id=exam.id,
        status="finalizado" if finalized else "rascunho",
        origin_type="coworking",
        signature_status="assinatura_pendente" if finalized else None,
        created_by_user_id=user.id,
    )
    session.add(document)
    session.flush()
    original = ReportDocumentVersion(
        report_document_id=document.id,
        kind="original",
        version_number=1,
        storage_path=f"laudos/{exam.id}/{document.id}/{new_uuid()}.pdf",
        sha256="a" * 64,
        size_bytes=1,
        page_count=1,
        created_by_user_id=user.id,
    )
    session.add(original)
    session.flush()
    document.current_version_id = original.id
    session.commit()
    result = (document.id, exam.id, user.id)
    session.close()
    return result


def test_numeros_de_versao_concorrentes_sao_serializados_pg(pg_engine):
    """O mesmo lock usado por /compor produz v2 e v3, nunca duas v2."""

    document_id, exam_id, user_id = _setup_report_concurrency(
        pg_engine, new_uuid()
    )
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    barrier = threading.Barrier(2)
    created_numbers: list[int] = []
    errors: list[Exception] = []

    def worker(marker: str):
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            session.execute(
                select(ReportDocument)
                .where(ReportDocument.id == document_id)
                .with_for_update()
            ).scalar_one()
            current = session.execute(
                select(func.max(ReportDocumentVersion.version_number)).where(
                    ReportDocumentVersion.report_document_id == document_id
                )
            ).scalar_one()
            # Mantém o primeiro lock por um instante para tornar a disputa
            # explícita; o segundo worker precisa aguardar a transação.
            time.sleep(0.15)
            version = ReportDocumentVersion(
                report_document_id=document_id,
                kind="rascunho",
                version_number=int(current) + 1,
                storage_path=f"laudos/{exam_id}/{document_id}/{new_uuid()}.pdf",
                sha256=marker * 64,
                size_bytes=1,
                page_count=1,
                created_by_user_id=user_id,
            )
            session.add(version)
            session.commit()
            created_numbers.append(version.version_number)
        except Exception as exc:  # pragma: no cover - diagnóstico
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    threads = [
        threading.Thread(target=worker, args=("b",)),
        threading.Thread(target=worker, args=("c",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    assert sorted(created_numbers) == [2, 3]


def test_predecessor_finalizado_tem_um_unico_sucessor_concorrente_pg(pg_engine):
    """Duas corretivas simultâneas: uma cria e a outra encontra a existente."""

    predecessor_id, exam_id, user_id = _setup_report_concurrency(
        pg_engine, new_uuid(), finalized=True
    )
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[Exception] = []

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            predecessor = session.execute(
                select(ReportDocument)
                .where(ReportDocument.id == predecessor_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
            if predecessor.superseded_by_id:
                outcomes.append("ja_existente")
                session.rollback()
                return
            successor = ReportDocument(
                public_code=allocate_public_code(
                    session, "report_documents"
                ),
                spirometry_exam_id=exam_id,
                status="rascunho",
                origin_type="coworking",
                corrects_document_id=predecessor_id,
                created_by_user_id=user_id,
            )
            session.add(successor)
            session.flush()
            predecessor.superseded_by_id = successor.id
            time.sleep(0.15)
            session.commit()
            outcomes.append("criado")
        except Exception as exc:  # pragma: no cover - diagnóstico
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    assert sorted(outcomes) == ["criado", "ja_existente"]

    session = SessionLocal()
    try:
        successors = session.execute(
            select(ReportDocument).where(
                ReportDocument.corrects_document_id == predecessor_id
            )
        ).scalars().all()
        assert len(successors) == 1
        predecessor = session.get(ReportDocument, predecessor_id)
        assert predecessor.superseded_by_id == successors[0].id
    finally:
        session.close()


def _setup_m24c_assignment_context(
    pg_engine, *, tag: str, crm_numbers: tuple[str, str]
) -> dict[str, str]:
    session = sessionmaker(bind=pg_engine, expire_on_commit=False)()
    ensure_roles_exist(session)
    verifier = User(
        email=f"verifier-{tag}@teste.local",
        nome=f"TESTE APAGAR Verificador {tag}",
        password_hash="hash-sintetico-nao-utilizavel",
    )
    first_user = User(
        email=f"medico-a-{tag}@teste.local",
        nome=f"TESTE APAGAR Médico A {tag}",
        password_hash="hash-sintetico-nao-utilizavel",
    )
    second_user = User(
        email=f"medico-b-{tag}@teste.local",
        nome=f"TESTE APAGAR Médico B {tag}",
        password_hash="hash-sintetico-nao-utilizavel",
    )
    physician_role = get_role(session, ROLE_MEDICO)
    first_user.roles.append(physician_role)
    second_user.roles.append(physician_role)
    person = Person(
        public_code=allocate_public_code(session, "people"),
        nome_completo=f"Pessoa Sintética M24C {tag}",
        nome_normalizado=f"pessoa sintetica m24c {tag}",
    )
    session.add_all([verifier, first_user, second_user, person])
    session.flush()
    exam = SpirometryExam(
        public_code=allocate_public_code(session, "spirometry_exams"),
        person_id=person.id,
    )
    session.add(exam)
    session.flush()
    document = ReportDocument(
        public_code=allocate_public_code(session, "report_documents"),
        spirometry_exam_id=exam.id,
        status="atribuido",
        origin_type="coworking",
        created_by_user_id=verifier.id,
    )
    now = datetime.now(timezone.utc)
    first_profile = PhysicianProfile(
        user_id=first_user.id,
        professional_name=f"TESTE APAGAR Profissional A {tag}",
        crm_number=crm_numbers[0],
        crm_state="AC",
        active=True,
        verification_status="verified",
        verified_at=now,
        verified_by_user_id=verifier.id,
        verification_reference=f"CRM-VERIF-TESTE-{tag}-A",
    )
    second_profile = PhysicianProfile(
        user_id=second_user.id,
        professional_name=f"TESTE APAGAR Profissional B {tag}",
        crm_number=crm_numbers[1],
        crm_state="AC",
        active=True,
        verification_status="verified",
        verified_at=now,
        verified_by_user_id=verifier.id,
        verification_reference=f"CRM-VERIF-TESTE-{tag}-B",
    )
    session.add_all([document, first_profile, second_profile])
    session.commit()
    result = {
        "verifier_id": verifier.id,
        "document_id": document.id,
        "first_profile_id": first_profile.id,
        "second_profile_id": second_profile.id,
        "first_user_id": first_user.id,
        "second_user_id": second_user.id,
    }
    session.close()
    return result


def test_uma_atribuicao_ativa_por_documento_sob_concorrencia_pg(pg_engine):
    context = _setup_m24c_assignment_context(
        pg_engine,
        tag="assignment-race",
        crm_numbers=("710001", "710002"),
    )
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    barrier = threading.Barrier(2)
    created: list[str] = []
    errors: list[Exception] = []

    def worker(profile_id: str):
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            assignment = ReportAssignment(
                report_document_id=context["document_id"],
                physician_profile_id=profile_id,
                active=True,
                assigned_by_user_id=context["verifier_id"],
                reason_code="initial_assignment",
            )
            session.add(assignment)
            session.commit()
            created.append(assignment.id)
        except Exception as exc:
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    threads = [
        threading.Thread(
            target=worker, args=(context["first_profile_id"],)
        ),
        threading.Thread(
            target=worker, args=(context["second_profile_id"],)
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(created) == 1
    assert len(errors) == 1
    session = SessionLocal()
    try:
        active_count = session.scalar(
            select(func.count())
            .select_from(ReportAssignment)
            .where(
                ReportAssignment.report_document_id
                == context["document_id"],
                ReportAssignment.active.is_(True),
            )
        )
        assert active_count == 1
    finally:
        session.close()


def test_crm_uf_ativo_unico_sob_concorrencia_pg(pg_engine):
    session = sessionmaker(bind=pg_engine, expire_on_commit=False)()
    ensure_roles_exist(session)
    verifier = User(
        email="verifier-crm-race@teste.local",
        nome="TESTE APAGAR Verificador CRM",
        password_hash="hash-sintetico-nao-utilizavel",
    )
    users = [
        User(
            email=f"crm-race-{index}@teste.local",
            nome=f"TESTE APAGAR Médico CRM {index}",
            password_hash="hash-sintetico-nao-utilizavel",
        )
        for index in range(2)
    ]
    physician_role = get_role(session, ROLE_MEDICO)
    for user in users:
        user.roles.append(physician_role)
    session.add_all([verifier, *users])
    session.commit()
    verifier_id = verifier.id
    user_ids = [user.id for user in users]
    session.close()

    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    barrier = threading.Barrier(2)
    created: list[str] = []
    errors: list[Exception] = []

    def worker(index: int):
        worker_session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            profile = PhysicianProfile(
                user_id=user_ids[index],
                professional_name=f"TESTE APAGAR CRM Concorrente {index}",
                crm_number="720001",
                crm_state="AL",
                active=True,
                verification_status="verified",
                verified_at=datetime.now(timezone.utc),
                verified_by_user_id=verifier_id,
                verification_reference=f"CRM-VERIF-TESTE-CONCORRENTE-{index}",
            )
            worker_session.add(profile)
            worker_session.commit()
            created.append(profile.id)
        except Exception as exc:
            worker_session.rollback()
            errors.append(exc)
        finally:
            worker_session.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(created) == 1
    assert len(errors) == 1


def test_postgres_recusa_atribuicao_inativa_e_crm_nao_normalizado(pg_engine):
    context = _setup_m24c_assignment_context(
        pg_engine,
        tag="profile-guard",
        crm_numbers=("730001", "730002"),
    )
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        profile = session.get(
            PhysicianProfile, context["first_profile_id"]
        )
        profile.active = False
        session.commit()
        session.add(
            ReportAssignment(
                report_document_id=context["document_id"],
                physician_profile_id=profile.id,
                active=True,
                assigned_by_user_id=context["verifier_id"],
                reason_code="initial_assignment",
            )
        )
        with pytest.raises(Exception, match="active verified explicit"):
            session.commit()
        session.rollback()

        invalid_user = User(
            email="crm-invalid-pg@teste.local",
            nome="TESTE APAGAR CRM Inválido",
            password_hash="hash-sintetico-nao-utilizavel",
        )
        session.add(invalid_user)
        session.flush()
        session.add(
            PhysicianProfile(
                user_id=invalid_user.id,
                professional_name="TESTE APAGAR CRM Inválido",
                crm_number="CRM-123",
                crm_state="AC",
                active=False,
                verification_status="pending",
            )
        )
        with pytest.raises(Exception, match="digits only"):
            session.commit()
        session.rollback()
    finally:
        session.close()


def test_evidencia_m24c_e_append_only_no_postgres(pg_engine):
    context = _setup_m24c_assignment_context(
        pg_engine,
        tag="immutability",
        crm_numbers=("740001", "740002"),
    )
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    session = SessionLocal()
    assignment = ReportAssignment(
        report_document_id=context["document_id"],
        physician_profile_id=context["first_profile_id"],
        active=True,
        assigned_by_user_id=context["verifier_id"],
        reason_code="initial_assignment",
    )
    session.add(assignment)
    session.flush()
    event = ReportAssignmentEvent(
        report_document_id=context["document_id"],
        assignment_id=assignment.id,
        event_type="assigned",
        physician_profile_id=context["first_profile_id"],
        reason_code="initial_assignment",
        performed_by_user_id=context["verifier_id"],
    )
    version = ReportDocumentVersion(
        report_document_id=context["document_id"],
        kind="original",
        version_number=1,
        storage_path=(
            f"laudos/{context['document_id']}/{new_uuid()}.pdf"
        ),
        sha256="d" * 64,
        size_bytes=1,
        page_count=1,
        physician_profile_id_snapshot=context["first_profile_id"],
        physician_name_snapshot="TESTE APAGAR Snapshot",
        physician_crm_number_snapshot="740001",
        physician_crm_state_snapshot="AC",
        origin_type_snapshot="coworking",
        created_by_user_id=context["verifier_id"],
    )
    session.add_all([event, version])
    session.commit()
    assignment_id = assignment.id
    event_id = event.id
    version_id = version.id
    session.close()

    with pg_engine.connect() as connection:
        with pytest.raises(Exception, match="immutable clinical evidence"):
            connection.execute(
                text(
                    "UPDATE report_document_versions "
                    "SET sha256=:hash WHERE id=:id"
                ),
                {"hash": "e" * 64, "id": version_id},
            )
        connection.rollback()
        with pytest.raises(Exception, match="immutable clinical evidence"):
            connection.execute(
                text(
                    "UPDATE report_assignment_events "
                    "SET reason_code='assignment_correction' WHERE id=:id"
                ),
                {"id": event_id},
            )
        connection.rollback()
        connection.execute(
            text(
                "UPDATE report_assignments "
                "SET active=false, ended_at=now() WHERE id=:id"
            ),
            {"id": assignment_id},
        )
        connection.commit()
        with pytest.raises(Exception, match="invalid assignment rewrite"):
            connection.execute(
                text(
                    "UPDATE report_assignments "
                    "SET reason_code='assignment_correction' WHERE id=:id"
                ),
                {"id": assignment_id},
            )
        connection.rollback()
        with pytest.raises(Exception, match="assignment cannot be deleted"):
            connection.execute(
                text("DELETE FROM report_assignments WHERE id=:id"),
                {"id": assignment_id},
            )
        connection.rollback()


def test_estados_origem_e_assinatura_incompleta_falham_no_postgres(pg_engine):
    context = _setup_m24c_assignment_context(
        pg_engine,
        tag="state-guards",
        crm_numbers=("750001", "750002"),
    )
    with pg_engine.connect() as connection:
        for suffix, status, origin, signature_status, signed_at in (
            ("status", "estado_inventado", "coworking", None, None),
            ("origin", "atribuido", "origem_livre", None, None),
            ("signed", "assinado", "assinatura_pendente", None, None),
            (
                "fake-sig",
                "atribuido",
                "coworking",
                "assinada",
                None,
            ),
            (
                "pending",
                "assinatura_pendente",
                "coworking",
                "assinatura_pendente",
                None,
            ),
        ):
            with pytest.raises(Exception):
                connection.execute(
                    text(
                        """
                        INSERT INTO report_documents (
                            id, public_code, spirometry_exam_id, status,
                            origin_type, signature_status, signed_at,
                            created_by_user_id, created_at, updated_at
                        )
                        SELECT
                            :id, :code, spirometry_exam_id, :status,
                            :origin, :signature_status, :signed_at,
                            created_by_user_id, now(), now()
                        FROM report_documents WHERE id=:source
                        """
                    ),
                    {
                        "id": new_uuid(),
                        "code": f"LAU-{suffix.upper()}",
                        "status": status,
                        "origin": origin,
                        "signature_status": signature_status,
                        "signed_at": signed_at,
                        "source": context["document_id"],
                    },
                )
            connection.rollback()
