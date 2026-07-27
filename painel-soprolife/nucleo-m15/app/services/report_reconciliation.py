"""Read-only-by-default reconciliation of report rows and private storage."""

from __future__ import annotations

import hashlib
import os
import stat
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ReportDocument, ReportDocumentVersion
from .pdf_validation import InvalidPdfError, validate_pdf_bytes
from .report_storage import (
    PublishedReportFile,
    ReportStorageError,
    cleanup_published_file,
    version_storage_path,
)

DELETE_CONFIRMATION_PHRASE = "EXCLUIR APENAS PDFS ORFAOS CONFIRMADOS"


class ReportReconciliationError(RuntimeError):
    """Stable technical failure; messages must never contain a path."""


@dataclass(frozen=True)
class ReconciliationFinding:
    code: str
    finding_id: str
    version_id: str | None = None
    document_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "code": self.code,
            "finding_id": self.finding_id,
        }
        if self.version_id is not None:
            result["version_id"] = self.version_id
        if self.document_id is not None:
            result["document_id"] = self.document_id
        return result


@dataclass(frozen=True)
class ReconciliationResult:
    db_rows: int
    regular_files: int
    findings: tuple[ReconciliationFinding, ...]
    _orphan_tokens: tuple[PublishedReportFile, ...] = field(
        default=(),
        repr=False,
    )

    @property
    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(item.code for item in self.findings).items()))

    def as_dict(self) -> dict:
        return {
            "mode": "dry-run",
            "summary": {
                "database_rows": self.db_rows,
                "regular_files": self.regular_files,
                "findings": len(self.findings),
                "confirmed_orphans": len(self._orphan_tokens),
                "counts": self.counts,
            },
            "findings": [item.as_dict() for item in self.findings],
        }


@dataclass(frozen=True)
class ReconciliationDeletionResult:
    requested: int
    deleted: int
    skipped: int
    deleted_finding_ids: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "mode": "guarded-delete",
            "summary": {
                "requested": self.requested,
                "deleted": self.deleted,
                "skipped": self.skipped,
            },
            "deleted_finding_ids": list(self.deleted_finding_ids),
        }


def _finding_id(relative: Path, code: str) -> str:
    material = code.encode("ascii") + b"\0" + os.fsencode(relative.as_posix())
    return hashlib.sha256(material).hexdigest()[:20]


def _db_finding(
    code: str,
    version: ReportDocumentVersion,
    relative: Path,
) -> ReconciliationFinding:
    return ReconciliationFinding(
        code=code,
        finding_id=_finding_id(relative, code),
        version_id=version.id,
        document_id=version.report_document_id,
    )


def _safe_root(root: Path) -> Path:
    if not root.is_absolute():
        raise ReportReconciliationError("storage_root_not_absolute")
    try:
        root_mode = root.lstat().st_mode
        resolved = root.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise ReportReconciliationError("storage_root_unavailable") from None
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode) or resolved != root:
        raise ReportReconciliationError("storage_root_unsafe_type")
    return root


def _safe_relative(value: str) -> Path | None:
    try:
        relative = Path(value)
    except (TypeError, ValueError):
        return None
    if (
        not value
        or relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or relative.as_posix() != value
    ):
        return None
    return relative


def _scan_storage(
    root: Path,
) -> tuple[
    dict[Path, os.stat_result],
    list[ReconciliationFinding],
]:
    files: dict[Path, os.stat_result] = {}
    findings: list[ReconciliationFinding] = []
    stack: list[tuple[Path, Path]] = [(root, Path())]

    while stack:
        directory, relative_directory = stack.pop()
        try:
            directory_stat = directory.stat(follow_symlinks=False)
            entries = list(os.scandir(directory))
        except OSError:
            findings.append(
                ReconciliationFinding(
                    code="storage_entry_unreadable",
                    finding_id=_finding_id(relative_directory, "storage_entry_unreadable"),
                )
            )
            continue
        if stat.S_IMODE(directory_stat.st_mode) != 0o700:
            findings.append(
                ReconciliationFinding(
                    code="unsafe_permissions",
                    finding_id=_finding_id(relative_directory, "unsafe_permissions"),
                )
            )
        for entry in entries:
            relative = relative_directory / entry.name
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                findings.append(
                    ReconciliationFinding(
                        code="storage_entry_unreadable",
                        finding_id=_finding_id(relative, "storage_entry_unreadable"),
                    )
                )
                continue
            mode = entry_stat.st_mode
            if stat.S_ISLNK(mode):
                findings.append(
                    ReconciliationFinding(
                        code="symlink_or_unexpected_type",
                        finding_id=_finding_id(
                            relative,
                            "symlink_or_unexpected_type",
                        ),
                    )
                )
            elif stat.S_ISDIR(mode):
                stack.append((Path(entry.path), relative))
            elif stat.S_ISREG(mode):
                files[relative] = entry_stat
                if stat.S_IMODE(mode) != 0o600:
                    findings.append(
                        ReconciliationFinding(
                            code="unsafe_permissions",
                            finding_id=_finding_id(relative, "unsafe_permissions"),
                        )
                    )
            else:
                findings.append(
                    ReconciliationFinding(
                        code="symlink_or_unexpected_type",
                        finding_id=_finding_id(
                            relative,
                            "symlink_or_unexpected_type",
                        ),
                    )
                )
    return files, findings


def _read_scanned_regular(
    root: Path,
    relative: Path,
    scanned_stat: os.stat_result,
    *,
    max_size_bytes: int,
) -> tuple[bytes | None, int, str]:
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ReportReconciliationError("storage_entry_changed")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(root / relative, flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (scanned_stat.st_dev, scanned_stat.st_ino)
        ):
            raise ReportReconciliationError("storage_entry_changed")
        digest = hashlib.sha256()
        chunks: list[bytes] | None = []
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
            if chunks is not None:
                if total <= max_size_bytes:
                    chunks.append(chunk)
                else:
                    chunks = None
        return (
            b"".join(chunks) if chunks is not None else None,
            total,
            digest.hexdigest(),
        )
    finally:
        os.close(fd)


def reconcile_report_storage(
    db: Session,
    *,
    root: Path,
    max_size_bytes: int,
) -> ReconciliationResult:
    """Compare durable rows to storage without mutating either side."""

    root = _safe_root(root)
    scanned_files, findings = _scan_storage(root)
    rows = db.execute(
        select(ReportDocumentVersion, ReportDocument)
        .join(
            ReportDocument,
            ReportDocument.id == ReportDocumentVersion.report_document_id,
        )
        .order_by(ReportDocumentVersion.id)
    ).all()
    referenced: dict[Path, tuple[ReportDocumentVersion, ReportDocument]] = {}

    for version, document in rows:
        try:
            canonical_path = version_storage_path(
                root,
                exam_id=document.spirometry_exam_id,
                document_id=document.id,
                version_id=version.id,
            )
            canonical_relative = canonical_path.relative_to(root)
        except (ReportStorageError, ValueError):
            technical = Path("invalid") / version.id
            findings.append(
                _db_finding("database_storage_path_invalid", version, technical)
            )
            continue
        stored_relative = _safe_relative(version.storage_path)
        if stored_relative != canonical_relative:
            findings.append(
                _db_finding(
                    "database_storage_path_invalid",
                    version,
                    canonical_relative,
                )
            )
            continue
        referenced[canonical_relative] = (version, document)
        file_stat = scanned_files.get(canonical_relative)
        if file_stat is None:
            findings.append(
                _db_finding("database_row_missing_file", version, canonical_relative)
            )
            continue
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            findings.append(
                _db_finding("unsafe_permissions", version, canonical_relative)
            )

        # Unsafe modes are already an aggregate storage finding.  A regular
        # file can still be read locally to detect independent metadata drift.
        try:
            data, actual_size, actual_sha256 = _read_scanned_regular(
                root,
                canonical_relative,
                file_stat,
                max_size_bytes=max_size_bytes,
            )
        except (OSError, ReportReconciliationError):
            findings.append(
                _db_finding("storage_entry_unreadable", version, canonical_relative)
            )
            continue
        if actual_size != version.size_bytes:
            findings.append(
                _db_finding("size_mismatch", version, canonical_relative)
            )
        if actual_sha256 != version.sha256:
            findings.append(
                _db_finding("hash_mismatch", version, canonical_relative)
            )
        if data is None:
            findings.append(
                _db_finding("stored_pdf_invalid", version, canonical_relative)
            )
            continue
        try:
            validated = validate_pdf_bytes(
                data,
                max_size_bytes=max_size_bytes,
            )
        except InvalidPdfError:
            findings.append(
                _db_finding("stored_pdf_invalid", version, canonical_relative)
            )
        else:
            if validated.page_count != version.page_count:
                findings.append(
                    _db_finding("page_count_mismatch", version, canonical_relative)
                )

    orphan_tokens: list[PublishedReportFile] = []
    for relative, file_stat in scanned_files.items():
        if relative in referenced:
            continue
        findings.append(
            ReconciliationFinding(
                code="file_without_database_row",
                finding_id=_finding_id(relative, "file_without_database_row"),
            )
        )
        orphan_tokens.append(
            PublishedReportFile(
                root=root,
                relative_path=relative,
                device=file_stat.st_dev,
                inode=file_stat.st_ino,
            )
        )

    findings.sort(
        key=lambda item: (
            item.code,
            item.version_id or "",
            item.finding_id,
        )
    )
    return ReconciliationResult(
        db_rows=len(rows),
        regular_files=len(scanned_files),
        findings=tuple(findings),
        _orphan_tokens=tuple(orphan_tokens),
    )


def _fresh_referenced_paths(
    db: Session,
    root: Path,
) -> tuple[set[Path], set[Path]]:
    db.expire_all()
    rows = db.execute(
        select(ReportDocumentVersion, ReportDocument).join(
            ReportDocument,
            ReportDocument.id == ReportDocumentVersion.report_document_id,
        )
    ).all()
    all_paths: set[Path] = set()
    finalized_paths: set[Path] = set()
    for version, document in rows:
        candidates: set[Path] = set()
        stored = _safe_relative(version.storage_path)
        if stored is not None:
            candidates.add(stored)
        try:
            canonical = version_storage_path(
                root,
                exam_id=document.spirometry_exam_id,
                document_id=document.id,
                version_id=version.id,
            ).relative_to(root)
        except (ReportStorageError, ValueError):
            canonical = None
        if canonical is not None:
            candidates.add(canonical)
        all_paths.update(candidates)
        if version.kind == "finalizado":
            finalized_paths.update(candidates)
    return all_paths, finalized_paths


def delete_confirmed_orphans(
    db: Session,
    *,
    root: Path,
    dry_run: ReconciliationResult,
    explicit_delete: bool,
    confirmation_phrase: str,
    backup_postgresql_and_storage_confirmed: bool,
) -> ReconciliationDeletionResult:
    """Guarded deletion after a fresh DB and inode/type revalidation."""

    root = _safe_root(root)
    if not explicit_delete:
        raise ReportReconciliationError("explicit_delete_flag_required")
    if confirmation_phrase != DELETE_CONFIRMATION_PHRASE:
        raise ReportReconciliationError("confirmation_phrase_mismatch")
    if not backup_postgresql_and_storage_confirmed:
        raise ReportReconciliationError("coordinated_backup_confirmation_required")

    deleted_ids: list[str] = []
    skipped = 0
    for token in dry_run._orphan_tokens:
        # Requery immediately before each individual destructive operation;
        # a row created after the dry-run (or after a previous deletion) wins.
        referenced, finalized = _fresh_referenced_paths(db, root)
        relative = token.relative_path
        if (
            token.root != root
            or relative in referenced
            or relative in finalized
        ):
            skipped += 1
            continue
        finding_id = _finding_id(relative, "file_without_database_row")
        try:
            # cleanup_published_file revalidates containment, symlinks, regular
            # type, device and inode immediately before unlink, then fsyncs.
            removed = cleanup_published_file(token)
        except (ReportStorageError, OSError):
            skipped += 1
            continue
        if not removed:
            skipped += 1
            continue
        deleted_ids.append(finding_id)

    return ReconciliationDeletionResult(
        requested=len(dry_run._orphan_tokens),
        deleted=len(deleted_ids),
        skipped=skipped,
        deleted_finding_ids=tuple(sorted(deleted_ids)),
    )
