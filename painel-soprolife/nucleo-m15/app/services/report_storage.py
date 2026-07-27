"""Armazenamento privado de PDFs de laudo, com integridade fail-closed."""

from __future__ import annotations

import errno
import logging
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from .pdf_validation import InvalidPdfError, ValidatedPdf, validate_pdf_bytes

logger = logging.getLogger(__name__)

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


class ReportCleanupError(ReportStorageError):
    """Refusal or failure while removing one exact unpublished file."""


@dataclass(frozen=True)
class StoredPdf:
    data: bytes
    validated: ValidatedPdf


@dataclass(frozen=True)
class PublishedReportFile:
    """Identity-bound handle for exactly one file created by this operation."""

    root: Path
    relative_path: Path
    device: int
    inode: int

    @property
    def path(self) -> Path:
        return self.root / self.relative_path


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


def _fsync_directory(directory_fd: int) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        if exc.errno in {
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
            errno.EROFS,
        }:
            return
        raise


def cleanup_published_file(publication: PublishedReportFile) -> bool:
    """Remove only the identity-bound regular file represented by ``publication``."""

    root = publication.root
    relative = publication.relative_path
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ReportCleanupError("Identidade de publicação inválida.")
    resolved_root = root.resolve(strict=True)
    if resolved_root != root:
        raise ReportCleanupError("Raiz da publicação mudou.")
    path = root / relative
    checked_root, checked_relative = _relative_to_root(path, root)
    if checked_root != root or checked_relative != relative:
        raise ReportCleanupError("Publicação fora da raiz privada.")
    _assert_private_directory(root)
    _assert_no_internal_symlink(root, relative)
    _assert_private_directory(path.parent)

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_fd = os.open(path.parent, directory_flags)
    try:
        try:
            current = os.stat(
                path.name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise ReportCleanupError("Publicação deixou de ser arquivo regular.")
        if (current.st_dev, current.st_ino) != (
            publication.device,
            publication.inode,
        ):
            raise ReportCleanupError("Identidade da publicação mudou.")

        # Revalidation immediately before the destructive call.
        revalidated = os.stat(
            path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(revalidated.st_mode)
            or (revalidated.st_dev, revalidated.st_ino)
            != (publication.device, publication.inode)
        ):
            raise ReportCleanupError("Publicação mudou durante a limpeza.")
        os.unlink(path.name, dir_fd=directory_fd)
        _fsync_directory(directory_fd)
        return True
    finally:
        os.close(directory_fd)


def _log_cleanup_failure(error: BaseException) -> None:
    """Log only a stable event and exception class; never paths or PDF metadata."""

    logger.error(
        "report_publication_cleanup_failed",
        extra={
            "event": "report_publication_cleanup_failed",
            "error_type": type(error).__name__,
        },
    )


def atomic_write_new_file(
    path: Path, data: bytes, *, root: Path
) -> PublishedReportFile:
    """Publish one new 0600 file and return its identity; never overwrite."""

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
    publication: PublishedReportFile | None = None
    try:
        os.fchmod(fd, 0o600)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise ReportStorageError("Arquivo temporário não ficou com modo 0600.")
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_stat = os.fstat(handle.fileno())
        try:
            os.link(tmp_path, path, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError(
                "Arquivo de destino já existe; overwrite recusado."
            ) from None
        publication = PublishedReportFile(
            root=resolved_root,
            relative_path=relative,
            device=temporary_stat.st_dev,
            inode=temporary_stat.st_ino,
        )
        os.chmod(path, 0o600, follow_symlinks=False)
        file_stat = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise ReportStorageError("Arquivo publicado não ficou com modo 0600.")
        if (file_stat.st_dev, file_stat.st_ino) != (
            publication.device,
            publication.inode,
        ):
            raise ReportStorageError("Arquivo publicado mudou de identidade.")
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            _fsync_directory(directory_fd)
        finally:
            os.close(directory_fd)
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
        return publication
    except BaseException:
        if publication is not None:
            try:
                cleanup_published_file(publication)
            except BaseException as cleanup_error:
                _log_cleanup_failure(cleanup_error)
        raise
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


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
