"""M69 — the A1 permission guard vs. a real systemd credential.

On the VPS (systemd 255) the worker's $CREDENTIALS_DIRECTORY/nfse-a1.pfx is
root:root, st_mode 0440, POSIX ACL user_obj::r, user:<worker uid>:r,
group_obj::-, mask::r, other::-, on a read-only tmpfs mounted at
/run/credentials/<unit>. The 0440 is the ACL mask, not a readable group, and
the guard used to refuse it. These tests pin that exact shape as accepted and
every neighbour of it — and every conventional file that is not 0600/0400 —
as refused.

Owner, ACL and mount cannot be produced without root, so those three lookups
are replaced by the values measured on the VPS; the checks around them run
unchanged against real files.
"""
from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.services.nfse_national import readiness
from app.services.nfse_national.readiness import compute_provider_readiness
from app.services.nfse_national.signer import generate_synthetic_test_certificate

TOO_OPEN = "restricted_certificate_path_permissions_too_open"
U_OBJ, USER, G_OBJ, GROUP, MASK, OTHER = 1, 2, 4, 8, 16, 32
NO_ID = 0xFFFFFFFF


def _observed_acl(entry_perm=4):
    """The ACL systemd 255 put on the credential file (dir: perm 5)."""
    return [(U_OBJ, entry_perm, NO_ID), (USER, entry_perm, os.geteuid()),
            (G_OBJ, 0, NO_ID), (MASK, entry_perm, NO_ID), (OTHER, 0, NO_ID)]


@pytest.fixture
def cert():
    return generate_synthetic_test_certificate(common_name="SoproLife M69 Synthetic")


@pytest.fixture
def systemd(monkeypatch, tmp_path, cert):
    """A credentials directory shaped like the one measured on the VPS."""
    root = tmp_path / "run-credentials"
    root.mkdir()
    creds = root / "soprolife-nfse-production-worker.service"
    creds.mkdir()
    pfx = creds / "nfse-a1.pfx"
    pfx.write_bytes(cert[0])
    pfx.chmod(0o440)
    creds.chmod(0o550)
    acl = {str(creds): _observed_acl(5), str(pfx): _observed_acl(4)}
    mount = {str(creds): ("tmpfs", {"ro", "nosuid", "nodev", "noexec", "relatime", "nosymfollow"})}
    monkeypatch.setattr(readiness, "_CREDENTIALS_ROOT", str(root))
    monkeypatch.setattr(readiness, "_CREDENTIAL_OWNER_UID", os.getuid())
    monkeypatch.setattr(readiness, "_read_posix_acl", lambda p: acl.get(os.fspath(p)))
    monkeypatch.setattr(readiness, "_mountinfo_entry", lambda p: mount.get(p))
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    yield {"root": root, "creds": creds, "pfx": pfx, "acl": acl, "mount": mount}
    creds.chmod(0o700)


def _blockers(db, path, password):
    settings = Settings(nfse_restricted_certificate_path=path,
                        nfse_restricted_certificate_password=password)
    return compute_provider_readiness(db, settings, environment="restricted")


# ------------------------------------------------------------ conventional files

@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_a_conventional_owner_only_certificate_is_accepted(db, tmp_path, cert, mode, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    path = tmp_path / "a1.pfx"
    path.write_bytes(cert[0])
    path.chmod(mode)
    result = _blockers(db, path, cert[1])
    assert TOO_OPEN not in result.blockers
    assert result.certificate_syntactically_valid is True


@pytest.mark.parametrize("mode", [0o640, 0o440, 0o644, 0o660, 0o666, 0o604])
def test_a_conventional_certificate_readable_by_others_is_refused(db, tmp_path, cert, mode,
                                                                  monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    path = tmp_path / "a1.pfx"
    path.write_bytes(cert[0])
    path.chmod(mode)
    assert TOO_OPEN in _blockers(db, path, cert[1]).blockers


def test_the_real_host_lookups_never_vouch_for_a_plain_temp_file(tmp_path, cert, monkeypatch):
    """No stubs at all: a user-owned 0440 file in an ordinary directory, even
    with CREDENTIALS_DIRECTORY pointing at it, gets no exception."""
    creds = tmp_path / "creds"
    creds.mkdir()
    path = creds / "nfse-a1.pfx"
    path.write_bytes(cert[0])
    path.chmod(0o440)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    assert readiness._permissions_blocker(path) == TOO_OPEN


# ------------------------------------------------------------ the systemd credential

def test_the_systemd_credential_measured_on_the_vps_is_accepted(db, systemd, cert):
    result = _blockers(db, systemd["pfx"], cert[1])
    assert TOO_OPEN not in result.blockers
    assert result.certificate_syntactically_valid is True
    assert result.certificate_summary.subject_common_name == "SoproLife M69 Synthetic"
    assert result.certificate_summary.expired is False


def test_a_symlink_in_the_credentials_directory_is_refused(systemd, tmp_path):
    link = systemd["creds"] / "link.pfx"
    systemd["creds"].chmod(0o750)
    link.symlink_to(systemd["pfx"])
    systemd["creds"].chmod(0o550)
    systemd["acl"][str(link)] = _observed_acl(4)
    assert readiness._permissions_blocker(link) == TOO_OPEN


def test_a_symlinked_credentials_directory_is_refused(systemd, monkeypatch):
    alias = systemd["root"] / "alias.service"
    alias.symlink_to(systemd["creds"], target_is_directory=True)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(alias))
    systemd["mount"][str(alias)] = systemd["mount"][str(systemd["creds"])]
    systemd["acl"][str(alias)] = _observed_acl(5)
    systemd["acl"][str(alias / "nfse-a1.pfx")] = _observed_acl(4)
    assert readiness._permissions_blocker(alias / "nfse-a1.pfx") == TOO_OPEN


def test_the_same_file_outside_the_credentials_directory_gets_no_exception(systemd, tmp_path, cert):
    outside = tmp_path / "nfse-a1.pfx"
    outside.write_bytes(cert[0])
    outside.chmod(0o440)
    systemd["acl"][str(outside)] = _observed_acl(4)
    assert readiness._permissions_blocker(outside) == TOO_OPEN


@pytest.mark.parametrize("override", ["elsewhere", "dotdot", "nested", "root_itself"])
def test_a_manipulated_credentials_directory_variable_gets_no_exception(systemd, monkeypatch,
                                                                         tmp_path, override):
    creds = systemd["creds"]
    value = {"elsewhere": str(tmp_path),
             "dotdot": f"{creds}/../{creds.name}",
             "nested": str(creds / "sub"),
             "root_itself": str(systemd["root"])}[override]
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", value)
    assert readiness._permissions_blocker(systemd["pfx"]) == TOO_OPEN


@pytest.mark.parametrize("breakage", [
    "acl_other_user", "acl_group_obj_read", "acl_other_read", "acl_named_group",
    "acl_write", "acl_missing", "dir_acl_other_user", "mount_not_read_only",
    "mount_not_memory_fs", "not_a_mount_point", "owner_not_root", "file_other_bits",
    "file_write_bit", "dir_other_bits",
])
def test_every_weaker_neighbour_of_the_systemd_shape_is_refused(systemd, breakage):
    creds, pfx = str(systemd["creds"]), str(systemd["pfx"])
    acl, mount = systemd["acl"], systemd["mount"]
    other_uid = os.geteuid() + 1
    if breakage == "acl_other_user":
        acl[pfx] = acl[pfx] + [(USER, 4, other_uid)]
    elif breakage == "acl_group_obj_read":
        acl[pfx] = [(t, 4 if t == G_OBJ else p, i) for t, p, i in acl[pfx]]
    elif breakage == "acl_other_read":
        acl[pfx] = [(t, 4 if t == OTHER else p, i) for t, p, i in acl[pfx]]
    elif breakage == "acl_named_group":
        acl[pfx] = acl[pfx] + [(GROUP, 4, 1000)]
    elif breakage == "acl_write":
        acl[pfx] = [(t, 6 if t == USER else p, i) for t, p, i in acl[pfx]]
    elif breakage == "acl_missing":
        del acl[pfx]
    elif breakage == "dir_acl_other_user":
        acl[creds] = acl[creds] + [(USER, 5, other_uid)]
    elif breakage == "mount_not_read_only":
        mount[creds] = ("tmpfs", {"rw", "nosuid", "nodev", "noexec"})
    elif breakage == "mount_not_memory_fs":
        mount[creds] = ("ext4", {"ro"})
    elif breakage == "not_a_mount_point":
        del mount[creds]
    elif breakage == "owner_not_root":
        readiness._CREDENTIAL_OWNER_UID = os.getuid() + 1  # restored by monkeypatch
    elif breakage == "file_other_bits":
        systemd["pfx"].chmod(0o444)
    elif breakage == "file_write_bit":
        systemd["creds"].chmod(0o750)
        systemd["pfx"].chmod(0o640)
        systemd["creds"].chmod(0o550)
    elif breakage == "dir_other_bits":
        systemd["creds"].chmod(0o555)
    assert readiness._permissions_blocker(pfx) == TOO_OPEN


def test_the_acl_reader_decodes_the_kernel_format(monkeypatch):
    """The xattr layout (version 2 header, then tag/perm/id) as the kernel
    writes it, with the entries measured on the VPS (worker uid 999)."""
    import struct
    measured = [(U_OBJ, 4, NO_ID), (USER, 4, 999), (G_OBJ, 0, NO_ID), (MASK, 4, NO_ID),
                (OTHER, 0, NO_ID)]
    raw = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in measured)
    entries = readiness._parse_posix_acl(raw)
    assert entries == measured
    assert readiness._parse_posix_acl(struct.pack("<I", 1) + raw[4:]) is None
    assert readiness._parse_posix_acl(raw[:-1]) is None
    monkeypatch.setattr(readiness.os, "geteuid", lambda: 999)
    assert readiness._acl_is_private(entries, 4) is True
    monkeypatch.setattr(readiness.os, "geteuid", lambda: 1000)
    assert readiness._acl_is_private(entries, 4) is False
