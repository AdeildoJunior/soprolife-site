"""Permissões, contenção e integridade do storage privado M24A."""

import io
import os
import stat
import uuid
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.config import Settings
from app.services.pdf_validation import validate_pdf_bytes
from app.services.report_storage import (
    ReportStorageError,
    StoredPdfIntegrityError,
    atomic_write_new_file,
    read_and_validate_stored_pdf,
    version_storage_path,
)

MAX_BYTES = 10 * 1024 * 1024


def _pdf(*, pages: int = 1, width: int = 595, height: int = 842) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _stored_file(tmp_path: Path):
    root = _private_root(tmp_path)
    ids = [str(uuid.uuid4()) for _ in range(3)]
    path = version_storage_path(
        root,
        exam_id=ids[0],
        document_id=ids[1],
        version_id=ids[2],
    )
    data = _pdf()
    metadata = validate_pdf_bytes(data, max_size_bytes=MAX_BYTES)
    atomic_write_new_file(path, data, root=root)
    return root, path, data, metadata


def test_diretorios_0700_e_arquivo_0600_sob_umask_022(tmp_path):
    root_path = tmp_path / "private" / "reports"
    old_umask = os.umask(0o022)
    try:
        root = Settings(reports_storage_dir=root_path).resolved_reports_storage_dir()
        ids = [str(uuid.uuid4()) for _ in range(3)]
        path = version_storage_path(
            root,
            exam_id=ids[0],
            document_id=ids[1],
            version_id=ids[2],
        )
        atomic_write_new_file(path, _pdf(), root=root)
    finally:
        os.umask(old_umask)

    current = path.parent
    while current != root.parent:
        assert stat.S_IMODE(current.stat().st_mode) == 0o700
        if current == root:
            break
        current = current.parent
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_raiz_preexistente_com_grupo_ou_mundo_e_recusada(tmp_path):
    root = tmp_path / "reports"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    with pytest.raises(ValueError, match="permissões"):
        Settings(reports_storage_dir=root).resolved_reports_storage_dir()


def test_ancestral_symlink_e_resolvido_antes_da_contencao_git(tmp_path):
    workspace = Path(__file__).resolve().parents[2]
    link = tmp_path / "escape"
    link.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        Settings(
            reports_storage_dir=link / "private-reports"
        ).resolved_reports_storage_dir()


def test_diretorio_interno_permissivo_falha_fechado(tmp_path):
    root, path, _data, metadata = _stored_file(tmp_path)
    internal = root / "laudos"
    os.chmod(internal, 0o750)
    with pytest.raises(ReportStorageError, match="permissões inseguras"):
        read_and_validate_stored_pdf(
            path,
            root=root,
            expected_sha256=metadata.sha256,
            expected_size_bytes=metadata.size_bytes,
            expected_page_count=metadata.page_count,
            max_size_bytes=MAX_BYTES,
        )


def test_falha_de_chmod_nao_e_engolida(tmp_path, monkeypatch):
    root = tmp_path / "reports"

    def fail_chmod(*_args, **_kwargs):
        raise PermissionError("falha sintetica")

    monkeypatch.setattr(os, "chmod", fail_chmod)
    with pytest.raises(PermissionError, match="falha sintetica"):
        Settings(reports_storage_dir=root).resolved_reports_storage_dir()


def test_falha_de_mkdir_nao_e_engolida(tmp_path, monkeypatch):
    root = tmp_path / "reports"

    def fail_mkdir(*_args, **_kwargs):
        raise PermissionError("mkdir sintetico")

    monkeypatch.setattr(os, "mkdir", fail_mkdir)
    with pytest.raises(PermissionError, match="mkdir sintetico"):
        Settings(reports_storage_dir=root).resolved_reports_storage_dir()


def test_substituicao_por_outro_pdf_valido_falha_no_hash(tmp_path):
    root, path, _data, metadata = _stored_file(tmp_path)
    path.write_bytes(_pdf(width=400, height=400))
    os.chmod(path, 0o600)
    with pytest.raises(StoredPdfIntegrityError) as caught:
        read_and_validate_stored_pdf(
            path,
            root=root,
            expected_sha256=metadata.sha256,
            expected_size_bytes=metadata.size_bytes,
            expected_page_count=metadata.page_count,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "hash_armazenado_divergente"


def test_substituicao_por_lixo_falha_estruturalmente(tmp_path):
    root, path, _data, metadata = _stored_file(tmp_path)
    path.write_bytes(b"NAO E PDF")
    os.chmod(path, 0o600)
    with pytest.raises(StoredPdfIntegrityError) as caught:
        read_and_validate_stored_pdf(
            path,
            root=root,
            expected_sha256=metadata.sha256,
            expected_size_bytes=metadata.size_bytes,
            expected_page_count=metadata.page_count,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "pdf_armazenado_invalido"


def test_divergencia_de_tamanho_falha_fechado(tmp_path):
    root, path, _data, metadata = _stored_file(tmp_path)
    with pytest.raises(StoredPdfIntegrityError) as caught:
        read_and_validate_stored_pdf(
            path,
            root=root,
            expected_sha256=metadata.sha256,
            expected_size_bytes=metadata.size_bytes + 1,
            expected_page_count=metadata.page_count,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "tamanho_armazenado_divergente"


def test_divergencia_de_paginas_falha_fechado(tmp_path):
    root, path, _data, metadata = _stored_file(tmp_path)
    with pytest.raises(StoredPdfIntegrityError) as caught:
        read_and_validate_stored_pdf(
            path,
            root=root,
            expected_sha256=metadata.sha256,
            expected_size_bytes=metadata.size_bytes,
            expected_page_count=metadata.page_count + 1,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "paginas_armazenadas_divergentes"


def test_divergencia_explicita_de_hash_falha_fechado(tmp_path):
    root, path, _data, metadata = _stored_file(tmp_path)
    with pytest.raises(StoredPdfIntegrityError) as caught:
        read_and_validate_stored_pdf(
            path,
            root=root,
            expected_sha256="0" * 64,
            expected_size_bytes=metadata.size_bytes,
            expected_page_count=metadata.page_count,
            max_size_bytes=MAX_BYTES,
        )
    assert caught.value.codigo == "hash_armazenado_divergente"
