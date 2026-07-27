#!/usr/bin/env python3
"""M24D — manifesto de backup do piloto interno de laudos.

Escrito para ser chamado por ``backup-reports-pilot.sh`` DEPOIS que o dump
PostgreSQL e o arquivo do storage já foram criados e verificados
(``pg_restore --list`` / ``tar tvf``). Este módulo não cria dump nem tar;
apenas calcula hashes reais dos artefatos já existentes e grava o manifesto
que ``scripts/reports_go_live_gate.py`` exige antes de habilitar o piloto.

Fail-closed: qualquer artefato ausente, symlink, ou não-arquivo-regular
recusa a escrita do manifesto — nunca inventa um hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class BackupManifestError(RuntimeError):
    pass


def _sha256_of_regular_file(path: Path) -> str:
    try:
        file_stat = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise BackupManifestError(f"artefato ausente: {path}") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise BackupManifestError(f"artefato não pode ser symlink: {path}")
    if not stat.S_ISREG(file_stat.st_mode):
        raise BackupManifestError(f"artefato precisa ser arquivo regular: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    *,
    postgresql_dump_path: Path,
    storage_archive_path: Path,
    report_documents: int,
    report_document_versions: int,
    physician_profiles: int,
    created_at: datetime | None = None,
) -> dict:
    if not postgresql_dump_path.is_absolute() or not storage_archive_path.is_absolute():
        raise BackupManifestError("os caminhos de backup precisam ser absolutos.")
    for count_name, count_value in (
        ("report_documents", report_documents),
        ("report_document_versions", report_document_versions),
        ("physician_profiles", physician_profiles),
    ):
        if count_value < 0:
            raise BackupManifestError(f"contagem negativa: {count_name}")
    stamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "created_at": stamp.isoformat(),
        "postgresql_dump_path": str(postgresql_dump_path),
        "postgresql_dump_sha256": _sha256_of_regular_file(postgresql_dump_path),
        "storage_archive_path": str(storage_archive_path),
        "storage_archive_sha256": _sha256_of_regular_file(storage_archive_path),
        "counts": {
            "report_documents": report_documents,
            "report_document_versions": report_document_versions,
            "physician_profiles": physician_profiles,
        },
    }


def write_manifest_atomic(manifest_path: Path, manifest: dict) -> None:
    """Grava o manifesto de forma atômica e com modo 0600 — nunca sobrescreve
    um manifesto existente silenciosamente (rotação/nome é responsabilidade
    de quem chama, tipicamente com timestamp no nome do arquivo)."""

    if not manifest_path.is_absolute():
        raise BackupManifestError("o caminho do manifesto precisa ser absoluto.")
    if manifest_path.exists():
        raise BackupManifestError(f"manifesto já existe: {manifest_path}")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(manifest_path.parent), prefix=".manifest-", suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(tmp_name, manifest_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--pg-dump", required=True, type=Path)
    parser.add_argument("--storage-archive", required=True, type=Path)
    parser.add_argument("--count-report-documents", required=True, type=int)
    parser.add_argument("--count-report-document-versions", required=True, type=int)
    parser.add_argument("--count-physician-profiles", required=True, type=int)
    args = parser.parse_args(argv[1:])
    try:
        manifest = build_manifest(
            postgresql_dump_path=args.pg_dump,
            storage_archive_path=args.storage_archive,
            report_documents=args.count_report_documents,
            report_document_versions=args.count_report_document_versions,
            physician_profiles=args.count_physician_profiles,
        )
        write_manifest_atomic(args.manifest, manifest)
    except BackupManifestError as exc:
        print(f"ERRO BACKUP MANIFEST (fail-closed): {exc}", file=sys.stderr)
        return 1
    print(str(args.manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
