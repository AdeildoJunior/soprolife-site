"""Private storage for fiscal XML/PDF artifacts (DPS, NFS-e, events, DANFSe).

Mirrors the safety properties already established by
``app.services.report_storage`` for laudo PDFs (private 0700 directories,
0600 regular files, no overwrite, containment/symlink checks on every path),
kept as an independent module so the M27 fiscal path never shares mutable
state or a failure mode with the M24A report pipeline.

Never write under the public/static site tree or inside the Git worktree —
enforced by ``Settings.resolved_fiscal_artifacts_storage_dir()``, which
reuses the exact same fail-closed checks as
``resolved_reports_storage_dir()``.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_ALLOWED_KINDS = {
    "dps_unsigned_xml", "dps_signed_xml", "nfse_xml", "event_xml", "danfse_pdf",
}


class FiscalArtifactStorageError(RuntimeError):
    codigo = "armazenamento_fiscal_indisponivel"


class UnsafeArtifactIdError(FiscalArtifactStorageError):
    codigo = "identificador_artefato_invalido"


@dataclass(frozen=True)
class StoredArtifact:
    relative_path: Path
    sha256: str
    size_bytes: int


def _assert_uuid(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise UnsafeArtifactIdError(f"{label} inválido.")
    return value


def _assert_kind(kind: str) -> str:
    if kind not in _ALLOWED_KINDS:
        raise FiscalArtifactStorageError(f"Tipo de artefato fiscal desconhecido: {kind!r}.")
    return kind


def _assert_private_directory(path: Path) -> None:
    mode = path.stat(follow_symlinks=False).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise FiscalArtifactStorageError("Diretório interno inválido.")
    if stat.S_IMODE(mode) & 0o077:
        raise FiscalArtifactStorageError("Diretório interno com permissões inseguras.")


def artifact_relative_path(*, document_id: str, attempt_id: str, kind: str) -> Path:
    """Deterministic path built exclusively from internal UUIDs + a closed
    kind vocabulary — never from an external filename or request input."""
    _assert_uuid(document_id, label="document_id")
    _assert_uuid(attempt_id, label="attempt_id")
    _assert_kind(kind)
    extension = "pdf" if kind == "danfse_pdf" else "xml"
    return Path("fiscal") / document_id / attempt_id / f"{kind}.{extension}"


def _ensure_private_directory_chain(root: Path, directory: Path) -> None:
    resolved_root = root.resolve(strict=True)
    _assert_private_directory(resolved_root)
    relative = directory.relative_to(root)
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
            raise FiscalArtifactStorageError("Diretório interno inválido.")
        _assert_private_directory(current)


def write_artifact(root: Path, *, document_id: str, attempt_id: str, kind: str,
                    data: bytes) -> StoredArtifact:
    """Publish one new 0600 file under ``root``; refuses to overwrite."""
    relative = artifact_relative_path(document_id=document_id, attempt_id=attempt_id, kind=kind)
    path = root / relative
    _ensure_private_directory_chain(root, path.parent)

    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError("Artefato fiscal já existe; overwrite recusado (evidência append-only).")

    tmp_path = path.parent / f".tmp-{uuid.uuid4().hex}"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp_path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp_path, path, follow_symlinks=False)
        os.chmod(path, 0o600, follow_symlinks=False)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    file_stat = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise FiscalArtifactStorageError("Artefato publicado não ficou com modo 0600.")
    return StoredArtifact(relative_path=relative, sha256=hashlib.sha256(data).hexdigest(),
                          size_bytes=len(data))


def read_artifact(root: Path, relative_path: Path) -> bytes:
    resolved_root = root.resolve(strict=True)
    path = root / relative_path
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise FiscalArtifactStorageError("Caminho de artefato fora da raiz privada.")
    resolved_path = path.resolve(strict=True)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise FiscalArtifactStorageError("Artefato fora da raiz privada.") from exc
    mode = path.stat(follow_symlinks=False).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise FiscalArtifactStorageError("Artefato com tipo de armazenamento inválido.")
    return resolved_path.read_bytes()
