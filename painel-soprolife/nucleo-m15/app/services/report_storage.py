"""Armazenamento privado de PDFs de laudo, com integridade fail-closed."""

from __future__ import annotations

import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from .pdf_validation import InvalidPdfError, ValidatedPdf, validate_pdf_bytes

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ReportStorageError(RuntimeError):
    """Falha esperada de armazenamento, segura para mapeamento de domínio."""

    codigo = "armazenamento_laudos_indisponivel"
    mensagem = "Armazenamento de laudos indisponível."


class UnsafeStorageIdError(ReportStorageError):
    codigo = "identificador_armazenamento_invalido"
    mensagem = "Identificador interno de armazenamento inválido."


class StoredPdfMissingError(ReportStorageError):
    codigo = "arquivo_laudo_ausente"
    mensagem = "Arquivo do laudo não está disponível no armazenamento."


class StoredPdfIntegrityError(ReportStorageError):
    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class StoredPdf:
    data: bytes
    validated: ValidatedPdf


def _assert_safe_id(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise UnsafeStorageIdError(f"{label} inválido.")
    return value


def _assert_private_directory(path: Path) -> None:
    mode = path.stat(follow_symlinks=False).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ReportStorageError("Diretório interno inválido.")
    if stat.S_IMODE(mode) & 0o077:
        raise ReportStorageError("Diretório interno possui permissões inseguras.")


def _relative_to_root(path: Path, root: Path) -> tuple[Path, Path]:
    resolved_root = root.resolve(strict=True)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ReportStorageError("Caminho interno fora da raiz privada.") from exc
    if relative.is_absolute() or ".." in relative.parts:
        raise ReportStorageError("Caminho interno fora da raiz privada.")
    return resolved_root, relative


def _assert_no_internal_symlink(root: Path, relative: Path) -> None:
    current = root
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ReportStorageError("Symlink interno recusado.")
        # Todos os componentes intermediários existentes são diretórios
        # internos e precisam permanecer 0700 também durante releituras. O
        # último componente pode ser o PDF regular, verificado separadamente.
        if index < len(relative.parts) - 1 or stat.S_ISDIR(mode):
            _assert_private_directory(current)


def _ensure_private_directory_chain(root: Path, directory: Path) -> None:
    """Cria cada diretório interno com modo efetivo 0700, sem depender do umask."""
    resolved_root, relative = _relative_to_root(directory, root)
    _assert_private_directory(resolved_root)
    _assert_no_internal_symlink(resolved_root, relative)

    current = resolved_root
    for component in relative.parts:
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            os.chmod(current, 0o700)
            _assert_private_directory(current)
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ReportStorageError("Diretório interno inválido.")
        _assert_private_directory(current)

    # Repete contenção e symlink depois de toda criação.
    _assert_no_internal_symlink(resolved_root, relative)
    resolved_directory = directory.resolve(strict=True)
    try:
        resolved_directory.relative_to(resolved_root)
    except ValueError as exc:
        raise ReportStorageError("Diretório interno escapou da raiz privada.") from exc
    _assert_private_directory(resolved_directory)


def version_storage_path(
    root: Path, *, exam_id: str, document_id: str, version_id: str
) -> Path:
    """Caminho determinístico composto exclusivamente por UUIDs internos."""
    _assert_safe_id(exam_id, label="exam_id")
    _assert_safe_id(document_id, label="document_id")
    _assert_safe_id(version_id, label="version_id")
    path = root / "laudos" / exam_id / document_id / f"{version_id}.pdf"
    _relative_to_root(path, root)
    return path


def atomic_write_new_file(path: Path, data: bytes, *, root: Path) -> None:
    """Publica um arquivo novo de forma atômica, sempre 0600, sem overwrite."""
    resolved_root, relative = _relative_to_root(path, root)
    _ensure_private_directory_chain(resolved_root, path.parent)
    _assert_no_internal_symlink(resolved_root, relative)

    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError("Arquivo de destino já existe; overwrite recusado.")

    tmp_path = path.parent / f".tmp-{uuid.uuid4().hex}"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp_path, flags, 0o600)
    published = False
    try:
        os.fchmod(fd, 0o600)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise ReportStorageError("Arquivo temporário não ficou com modo 0600.")
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp_path, path, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError(
                "Arquivo de destino já existe; overwrite recusado."
            ) from None
        published = True
        os.chmod(path, 0o600, follow_symlinks=False)
        file_mode = path.stat(follow_symlinks=False).st_mode
        if not stat.S_ISREG(file_mode) or stat.S_IMODE(file_mode) != 0o600:
            raise ReportStorageError("Arquivo publicado não ficou com modo 0600.")
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        if published:
            # Repete contenção, symlink e modos depois da publicação.
            _assert_no_internal_symlink(resolved_root, relative)
            resolved_path = path.resolve(strict=True)
            try:
                resolved_path.relative_to(resolved_root)
            except ValueError as exc:
                raise ReportStorageError(
                    "Arquivo publicado escapou da raiz privada."
                ) from exc
            _assert_private_directory(path.parent)


def _read_stored_pdf_bytes(path: Path, *, root: Path) -> bytes:
    resolved_root, relative = _relative_to_root(path, root)
    _assert_private_directory(resolved_root)
    _assert_no_internal_symlink(resolved_root, relative)
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except FileNotFoundError as exc:
        raise StoredPdfMissingError() from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise StoredPdfIntegrityError(
            "arquivo_laudo_tipo_invalido",
            "Arquivo do laudo possui tipo de armazenamento inválido.",
        )
    if stat.S_IMODE(mode) != 0o600:
        raise StoredPdfIntegrityError(
            "arquivo_laudo_permissao_invalida",
            "Arquivo do laudo possui permissões inválidas.",
        )
    resolved_path = path.resolve(strict=True)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise StoredPdfIntegrityError(
            "arquivo_laudo_fora_da_raiz",
            "Arquivo do laudo não pertence à raiz privada.",
        ) from exc
    try:
        return resolved_path.read_bytes()
    except FileNotFoundError as exc:
        raise StoredPdfMissingError() from exc


def read_and_validate_stored_pdf(
    path: Path,
    *,
    root: Path,
    expected_sha256: str,
    expected_size_bytes: int,
    expected_page_count: int,
    max_size_bytes: int,
) -> StoredPdf:
    """Único caminho seguro de releitura: bytes + estrutura + metadados.

    Corrupção, substituição, alteração de tamanho/páginas/hash ou conteúdo
    ativo nunca são aceitos silenciosamente.
    """
    data = _read_stored_pdf_bytes(path, root=root)
    try:
        validated = validate_pdf_bytes(data, max_size_bytes=max_size_bytes)
    except InvalidPdfError as exc:
        raise StoredPdfIntegrityError(
            "pdf_armazenado_invalido",
            "PDF armazenado está corrompido ou viola a política de segurança.",
        ) from exc

    if validated.size_bytes != expected_size_bytes:
        raise StoredPdfIntegrityError(
            "tamanho_armazenado_divergente",
            "Tamanho do PDF armazenado diverge dos metadados.",
        )
    if validated.page_count != expected_page_count:
        raise StoredPdfIntegrityError(
            "paginas_armazenadas_divergentes",
            "Número de páginas do PDF armazenado diverge dos metadados.",
        )
    if validated.sha256 != expected_sha256:
        raise StoredPdfIntegrityError(
            "hash_armazenado_divergente",
            "Hash do PDF armazenado diverge dos metadados.",
        )
    return StoredPdf(data=data, validated=validated)
