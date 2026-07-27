"""Safe, technical-only report storage reconciliation and guarded deletion."""

import io
import json
import os
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app import cli
from app.config import get_settings
from app.models import (
    Person,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
    User,
)
from app.services.pdf_validation import validate_pdf_bytes
from app.services.report_reconciliation import (
    DELETE_CONFIRMATION_PHRASE,
    ReportReconciliationError,
    delete_confirmed_orphans,
    reconcile_report_storage,
)
from app.services.report_storage import atomic_write_new_file, version_storage_path

MAX_BYTES = 10 * 1024 * 1024


def _pdf(*, pages=1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture()
def reconciliation_context(db, tmp_path, monkeypatch):
    root = tmp_path / "private-reports"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(root))
    get_settings.cache_clear()

    user = User(
        email="reconcile@example.invalid",
        nome="Reconcile Test",
        password_hash="unused",
    )
    person = Person(
        public_code="PES-980001",
        nome_completo="Nome Paciente Nao Pode Vazar",
        nome_normalizado="nome paciente nao pode vazar",
    )
    db.add_all([user, person])
    db.flush()
    exam = SpirometryExam(
        public_code="ESP-980001",
        person_id=person.id,
        status="Concluido",
    )
    db.add(exam)
    db.flush()
    document = ReportDocument(
        spirometry_exam_id=exam.id,
        created_by_user_id=user.id,
    )
    db.add(document)
    db.flush()
    return {
        "root": root,
        "user": user,
        "person": person,
        "exam": exam,
        "document": document,
    }


def _add_version(
    db,
    context,
    *,
    number,
    data,
    write_file=True,
    kind="rascunho",
    metadata_overrides=None,
):
    from app.ids import new_uuid

    version_id = new_uuid()
    root = context["root"]
    document = context["document"]
    path = version_storage_path(
        root,
        exam_id=context["exam"].id,
        document_id=document.id,
        version_id=version_id,
    )
    metadata = validate_pdf_bytes(data, max_size_bytes=MAX_BYTES)
    if write_file:
        atomic_write_new_file(path, data, root=root)
    values = {
        "sha256": metadata.sha256,
        "size_bytes": metadata.size_bytes,
        "page_count": metadata.page_count,
    }
    values.update(metadata_overrides or {})
    version = ReportDocumentVersion(
        id=version_id,
        report_document_id=document.id,
        kind=kind,
        version_number=number,
        storage_path=str(path.relative_to(root)),
        sha256=values["sha256"],
        size_bytes=values["size_bytes"],
        page_count=values["page_count"],
        created_by_user_id=context["user"].id,
    )
    db.add(version)
    db.flush()
    return version, path


def test_dry_run_detecta_todas_as_classes_sem_expor_nome_ou_caminho(
    db, reconciliation_context
):
    context = reconciliation_context
    valid, valid_path = _add_version(
        db,
        context,
        number=1,
        data=_pdf(),
    )
    os.chmod(valid_path, 0o640)
    missing, _missing_path = _add_version(
        db,
        context,
        number=2,
        data=_pdf(),
        write_file=False,
    )
    mismatch, _mismatch_path = _add_version(
        db,
        context,
        number=3,
        data=_pdf(),
        metadata_overrides={
            "sha256": "0" * 64,
            "size_bytes": 1,
            "page_count": 2,
        },
    )
    orphan = context["root"] / "patient-name-orphan.pdf"
    orphan.write_bytes(_pdf())
    os.chmod(orphan, 0o600)
    symlink = context["root"] / "patient-name-link.pdf"
    symlink.symlink_to("/etc/passwd")
    fifo = context["root"] / "patient-name-fifo"
    os.mkfifo(fifo, 0o600)
    db.commit()

    result = reconcile_report_storage(
        db,
        root=context["root"],
        max_size_bytes=MAX_BYTES,
    )
    codes = {finding.code for finding in result.findings}
    assert {
        "database_row_missing_file",
        "file_without_database_row",
        "hash_mismatch",
        "size_mismatch",
        "page_count_mismatch",
        "unsafe_permissions",
        "symlink_or_unexpected_type",
    } <= codes
    assert result.db_rows == 3
    assert result.regular_files == 3
    assert result.counts["file_without_database_row"] == 1

    rendered = json.dumps(result.as_dict(), ensure_ascii=False)
    assert context["person"].nome_completo not in rendered
    assert str(context["root"]) not in rendered
    assert orphan.name not in rendered
    assert symlink.name not in rendered
    assert fifo.name not in rendered
    assert ".pdf" not in rendered
    assert valid.id in rendered
    assert missing.id in rendered
    assert mismatch.id in rendered


@pytest.mark.parametrize(
    "kwargs,code",
    [
        (
            {
                "explicit_delete": False,
                "confirmation_phrase": DELETE_CONFIRMATION_PHRASE,
                "backup_postgresql_and_storage_confirmed": True,
            },
            "explicit_delete_flag_required",
        ),
        (
            {
                "explicit_delete": True,
                "confirmation_phrase": "frase incorreta",
                "backup_postgresql_and_storage_confirmed": True,
            },
            "confirmation_phrase_mismatch",
        ),
        (
            {
                "explicit_delete": True,
                "confirmation_phrase": DELETE_CONFIRMATION_PHRASE,
                "backup_postgresql_and_storage_confirmed": False,
            },
            "coordinated_backup_confirmation_required",
        ),
    ],
)
def test_exclusao_exige_flag_frase_exata_e_backup(
    db, reconciliation_context, kwargs, code
):
    dry_run = reconcile_report_storage(
        db,
        root=reconciliation_context["root"],
        max_size_bytes=MAX_BYTES,
    )
    with pytest.raises(ReportReconciliationError) as caught:
        delete_confirmed_orphans(
            db,
            root=reconciliation_context["root"],
            dry_run=dry_run,
            **kwargs,
        )
    assert str(caught.value) == code


def test_exclusao_guardada_remove_somente_orfao_regular_confirmado(
    db, reconciliation_context
):
    context = reconciliation_context
    version, valid_path = _add_version(
        db,
        context,
        number=1,
        data=_pdf(),
        kind="finalizado",
    )
    valid_bytes = valid_path.read_bytes()
    orphan = context["root"] / "technical-orphan.bin"
    orphan.write_bytes(b"orphan")
    os.chmod(orphan, 0o600)
    symlink = context["root"] / "technical-symlink"
    symlink.symlink_to(valid_path)
    db.commit()

    dry_run = reconcile_report_storage(
        db,
        root=context["root"],
        max_size_bytes=MAX_BYTES,
    )
    deleted = delete_confirmed_orphans(
        db,
        root=context["root"],
        dry_run=dry_run,
        explicit_delete=True,
        confirmation_phrase=DELETE_CONFIRMATION_PHRASE,
        backup_postgresql_and_storage_confirmed=True,
    )
    assert deleted.deleted == 1
    assert not orphan.exists()
    assert valid_path.read_bytes() == valid_bytes
    assert symlink.is_symlink()
    assert db.get(ReportDocumentVersion, version.id) is not None


def test_revalidacao_imediata_impede_apagar_arquivo_que_passou_a_ter_linha(
    db, reconciliation_context
):
    context = reconciliation_context
    data = _pdf()
    from app.ids import new_uuid

    version_id = new_uuid()
    path = version_storage_path(
        context["root"],
        exam_id=context["exam"].id,
        document_id=context["document"].id,
        version_id=version_id,
    )
    atomic_write_new_file(path, data, root=context["root"])
    dry_run = reconcile_report_storage(
        db,
        root=context["root"],
        max_size_bytes=MAX_BYTES,
    )
    assert dry_run.as_dict()["summary"]["confirmed_orphans"] == 1

    metadata = validate_pdf_bytes(data, max_size_bytes=MAX_BYTES)
    row = ReportDocumentVersion(
        id=version_id,
        report_document_id=context["document"].id,
        kind="finalizado",
        version_number=1,
        storage_path=str(path.relative_to(context["root"])),
        sha256=metadata.sha256,
        size_bytes=metadata.size_bytes,
        page_count=metadata.page_count,
        created_by_user_id=context["user"].id,
    )
    db.add(row)
    db.commit()

    deleted = delete_confirmed_orphans(
        db,
        root=context["root"],
        dry_run=dry_run,
        explicit_delete=True,
        confirmation_phrase=DELETE_CONFIRMATION_PHRASE,
        backup_postgresql_and_storage_confirmed=True,
    )
    assert deleted.deleted == 0
    assert deleted.skipped == 1
    assert path.is_file()


def test_cli_e_dry_run_por_padrao_e_nao_imprime_raiz_ou_filename(
    db, reconciliation_context, monkeypatch, capsys
):
    context = reconciliation_context
    orphan = context["root"] / "sensitive-patient-name.pdf"
    orphan.write_bytes(_pdf())
    os.chmod(orphan, 0o600)

    class SyntheticSettings:
        reports_storage_dir = context["root"]
        reports_max_upload_bytes = MAX_BYTES

        @staticmethod
        def resolved_reports_storage_dir():
            return context["root"]

    import app.config as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: SyntheticSettings())
    monkeypatch.setattr(cli, "_session", lambda: db)
    assert cli.main(["reconciliar-laudos", "--json"]) == 0
    captured = capsys.readouterr()
    assert orphan.is_file()
    assert str(context["root"]) not in captured.out
    assert orphan.name not in captured.out
    assert captured.err == ""


def test_cli_destrutivo_exige_e_recebe_frase_interativa_exata(
    db, reconciliation_context, monkeypatch, capsys
):
    context = reconciliation_context
    orphan = context["root"] / "another-sensitive-name.pdf"
    orphan.write_bytes(_pdf())
    os.chmod(orphan, 0o600)

    class SyntheticSettings:
        reports_storage_dir = context["root"]
        reports_max_upload_bytes = MAX_BYTES

        @staticmethod
        def resolved_reports_storage_dir():
            return context["root"]

    import app.config as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: SyntheticSettings())
    monkeypatch.setattr(cli, "_session", lambda: db)
    monkeypatch.setattr("builtins.input", lambda _prompt: DELETE_CONFIRMATION_PHRASE)
    result = cli.main(
        [
            "reconciliar-laudos",
            "--delete-orphans",
            "--backup-postgresql-e-storage-confirmado",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert result == 0
    assert not orphan.exists()
    assert orphan.name not in captured.out
    assert str(context["root"]) not in captured.out
