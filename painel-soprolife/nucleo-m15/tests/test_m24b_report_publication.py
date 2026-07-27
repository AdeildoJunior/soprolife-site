"""M24B transaction/storage atomicity for every report-version publication."""

import inspect
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.errors import ReportDomainError
from app.models import (
    Person,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.routers import reports as reports_router
from app.services.report_publication import report_publication_transaction
from app.services.report_storage import (
    ReportCleanupError,
    atomic_write_new_file,
    cleanup_published_file,
    version_storage_path,
)


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture()
def publication_context(db, tmp_path, monkeypatch):
    root = tmp_path / "private-reports"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(root))
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    get_settings.cache_clear()

    user = User(
        email="publication@example.invalid",
        nome="Publication Test",
        password_hash="unused",
    )
    person = Person(
        public_code="PES-990001",
        nome_completo="Pessoa Sintetica",
        nome_normalizado="pessoa sintetica",
    )
    db.add_all([user, person])
    db.flush()
    exam = SpirometryExam(
        public_code="ESP-990001",
        person_id=person.id,
        status="Concluido",
    )
    db.add(exam)
    db.flush()
    document = ReportDocument(
        public_code="LAU-990001",
        spirometry_exam_id=exam.id,
        status=reports_router.STATUS_ATRIBUIDO,
        origin_type="coworking",
        created_by_user_id=user.id,
    )
    db.add(document)
    db.commit()
    return SimpleNamespace(
        root=root,
        user=user,
        exam=exam,
        document=document,
        data=_pdf(),
    )


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _store(db, context, publication, *, document=None):
    document = document or context.document
    return reports_router._store_new_version(
        db,
        publication=publication,
        document=document,
        exam_id=context.exam.id,
        kind=reports_router.KIND_ORIGINAL,
        data=context.data,
        created_by_user_id=context.user.id,
    )


def test_forced_db_flush_failure_rolls_back_row_and_exact_file(
    db, publication_context, monkeypatch
):
    real_flush = db.flush

    def fail_version_flush(*args, **kwargs):
        if any(isinstance(value, ReportDocumentVersion) for value in db.new):
            raise IntegrityError("synthetic", {}, RuntimeError("flush"))
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", fail_version_flush)
    with pytest.raises(ReportDomainError) as caught:
        with report_publication_transaction(db) as publication:
            _store(db, publication_context, publication)
            publication.commit()
    assert caught.value.codigo == "numero_versao_concorrente"
    assert db.scalar(select(func.count()).select_from(ReportDocumentVersion)) == 0
    assert _files(publication_context.root) == []


def test_forced_db_commit_failure_preserves_original_error_and_removes_file(
    db, publication_context, monkeypatch
):
    original_error = RuntimeError("synthetic commit failure")

    def fail_commit():
        raise original_error

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError) as caught:
        with report_publication_transaction(db) as publication:
            _store(db, publication_context, publication)
            publication.commit()
    assert caught.value is original_error
    assert db.scalar(select(func.count()).select_from(ReportDocumentVersion)) == 0
    assert _files(publication_context.root) == []


def test_duplicate_version_race_never_removes_preexisting_valid_version(
    db, publication_context, monkeypatch
):
    with report_publication_transaction(db) as publication:
        first = _store(db, publication_context, publication)
        publication.commit()
    first_path = publication_context.root / first.storage_path
    first_bytes = first_path.read_bytes()

    real_flush = db.flush

    def fail_second_version(*args, **kwargs):
        pending_versions = [
            value for value in db.new if isinstance(value, ReportDocumentVersion)
        ]
        if pending_versions:
            raise IntegrityError("synthetic duplicate", {}, RuntimeError("race"))
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", fail_second_version)
    with pytest.raises(ReportDomainError) as caught:
        with report_publication_transaction(db) as publication:
            _store(db, publication_context, publication)
            publication.commit()
    assert caught.value.codigo == "numero_versao_concorrente"
    assert first_path.read_bytes() == first_bytes
    assert _files(publication_context.root) == [first_path]
    assert db.scalar(select(func.count()).select_from(ReportDocumentVersion)) == 1


def test_cleanup_success_fsyncs_parent_and_removes_exact_file(
    tmp_path, monkeypatch
):
    from app.services import report_storage

    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    path = version_storage_path(
        root,
        exam_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        version_id="00000000-0000-0000-0000-000000000003",
    )
    publication = atomic_write_new_file(path, _pdf(), root=root)
    calls = 0
    real_fsync = report_storage._fsync_directory

    def counted_fsync(directory_fd):
        nonlocal calls
        calls += 1
        return real_fsync(directory_fd)

    monkeypatch.setattr(report_storage, "_fsync_directory", counted_fsync)
    cleanup_published_file(publication)
    assert not path.exists()
    assert calls == 1


def test_cleanup_failure_is_safe_logged_and_does_not_replace_original_error(
    db, publication_context, monkeypatch
):
    import app.services.report_publication as publication_service

    original_error = RuntimeError("original transaction error")

    def cleanup_failure(_publication):
        raise PermissionError(
            "/absolute/private/patient-name-sensitive-file.pdf"
        )

    monkeypatch.setattr(
        publication_service,
        "cleanup_published_file",
        cleanup_failure,
    )
    records = []

    def capture_log(message, *args, **kwargs):
        records.append((message, args, kwargs))

    monkeypatch.setattr(publication_service.logger, "error", capture_log)
    with pytest.raises(RuntimeError) as caught:
        with report_publication_transaction(db) as publication:
            _store(db, publication_context, publication)
            raise original_error
    assert caught.value is original_error
    assert len(records) == 1
    assert records[0][0] == "report_publication_cleanup_failed"
    assert records[0][2]["extra"] == {
        "event": "report_publication_cleanup_failed",
        "error_type": "PermissionError",
    }
    text = repr(records)
    assert "patient-name" not in text
    assert ".pdf" not in text
    assert str(publication_context.root) not in text


def test_identity_change_or_preexisting_destination_is_never_removed(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    path = version_storage_path(
        root,
        exam_id="10000000-0000-0000-0000-000000000001",
        document_id="10000000-0000-0000-0000-000000000002",
        version_id="10000000-0000-0000-0000-000000000003",
    )
    publication = atomic_write_new_file(path, _pdf(), root=root)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        atomic_write_new_file(path, b"replacement", root=root)
    assert path.read_bytes() == original

    path.unlink()
    path.write_bytes(_pdf())
    os.chmod(path, 0o600)
    replacement = path.read_bytes()
    with pytest.raises(ReportCleanupError):
        cleanup_published_file(publication)
    assert path.read_bytes() == replacement


def test_success_has_no_database_row_with_missing_file(db, publication_context):
    with report_publication_transaction(db) as publication:
        version = _store(db, publication_context, publication)
        publication.commit()
    durable = db.get(ReportDocumentVersion, version.id)
    assert durable is not None
    assert (publication_context.root / durable.storage_path).is_file()


@pytest.mark.parametrize(
    "route",
    [
        reports_router.upload_report_document,
        reports_router.compose_report_document,
        reports_router.prepare_report_signature,
        reports_router.open_corrective_document,
    ],
)
def test_all_file_publishing_routes_use_the_same_transaction_contract(route):
    assert "with report_publication_transaction(db)" in inspect.getsource(route)
