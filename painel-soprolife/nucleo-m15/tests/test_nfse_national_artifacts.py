"""M27 — armazenamento privado de artefatos fiscais (DPS/NFS-e XML)."""
import hashlib
import stat

import pytest

from app.services.nfse_national.artifacts import (
    FiscalArtifactStorageError,
    UnsafeArtifactIdError,
    artifact_relative_path,
    read_artifact,
    write_artifact,
)

DOC_ID = "11111111-1111-1111-1111-111111111111"
ATTEMPT_ID = "22222222-2222-2222-2222-222222222222"


def test_write_creates_private_dirs_and_0600_file(tmp_path):
    stored = write_artifact(tmp_path, document_id=DOC_ID, attempt_id=ATTEMPT_ID,
                            kind="dps_signed_xml", data=b"<DPS/>")
    path = tmp_path / stored.relative_path
    assert path.read_bytes() == b"<DPS/>"
    mode = path.stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
    for parent in [path.parent, path.parent.parent, tmp_path / "fiscal"]:
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stored.sha256 == hashlib.sha256(b"<DPS/>").hexdigest()
    assert stored.size_bytes == len(b"<DPS/>")


def test_write_refuses_overwrite(tmp_path):
    write_artifact(tmp_path, document_id=DOC_ID, attempt_id=ATTEMPT_ID,
                   kind="dps_signed_xml", data=b"first")
    with pytest.raises(FileExistsError):
        write_artifact(tmp_path, document_id=DOC_ID, attempt_id=ATTEMPT_ID,
                       kind="dps_signed_xml", data=b"second")


def test_read_returns_exact_bytes(tmp_path):
    write_artifact(tmp_path, document_id=DOC_ID, attempt_id=ATTEMPT_ID,
                   kind="nfse_xml", data=b"<NFSe/>")
    path = artifact_relative_path(document_id=DOC_ID, attempt_id=ATTEMPT_ID, kind="nfse_xml")
    assert read_artifact(tmp_path, path) == b"<NFSe/>"


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(FiscalArtifactStorageError):
        write_artifact(tmp_path, document_id=DOC_ID, attempt_id=ATTEMPT_ID,
                       kind="not_a_real_kind", data=b"x")


def test_non_uuid_ids_rejected(tmp_path):
    with pytest.raises(UnsafeArtifactIdError):
        write_artifact(tmp_path, document_id="../escape", attempt_id=ATTEMPT_ID,
                       kind="dps_signed_xml", data=b"x")
    with pytest.raises(UnsafeArtifactIdError):
        artifact_relative_path(document_id=DOC_ID, attempt_id="not-a-uuid", kind="dps_signed_xml")


def test_read_rejects_path_escaping_root(tmp_path):
    from pathlib import Path
    write_artifact(tmp_path, document_id=DOC_ID, attempt_id=ATTEMPT_ID,
                   kind="dps_signed_xml", data=b"x")
    with pytest.raises(FiscalArtifactStorageError):
        read_artifact(tmp_path, Path("../outside.xml"))


def test_danfse_pdf_uses_pdf_extension():
    path = artifact_relative_path(document_id=DOC_ID, attempt_id=ATTEMPT_ID, kind="danfse_pdf")
    assert path.suffix == ".pdf"
