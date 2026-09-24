"""Read-only provider readiness computation for the restricted foundation.

Composes what M27 already built (config/signer/xsd_validation/artifacts) plus
the M26 fiscal domain (FiscalPolicy) into one deterministic snapshot: what is
configured, what is missing, and why real issuance is not possible yet.

This module never activates anything. It has no side effect on network gates,
never opens a real certificate unless a path AND a password are both present
(and even then only to read safe metadata — subject/validity dates — never
the key material), and never returns the password, private key or raw
certificate bytes. A caller that wants THE reason nothing can be sent yet
reads ``blockers`` here; nothing here is a switch.
"""
from __future__ import annotations

import hashlib
import os
import stat
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import FiscalPolicy
from . import fiscal_config
from .transport import PRODUCTION_BASE_URL, NetworkGateClosedError, assert_production_base_url
from .signer import SignatureError, load_pkcs12_certificate
from .xsd_validation import DPS_SCHEMA_PATH, NFSE_SCHEMA_PATH


@dataclass(frozen=True)
class CertificateSummary:
    """Safe-to-display metadata only. No key material, no password, no raw bytes."""
    subject_common_name: str | None
    not_before: datetime | None
    not_after: datetime | None
    expired: bool | None


@dataclass(frozen=True)
class ProviderReadiness:
    environment: str
    provider_implementation_available: bool
    layout_version: str
    schema_version: str
    schema_fingerprint: str | None
    certificate_configured: bool
    certificate_syntactically_valid: bool | None
    certificate_summary: CertificateSummary | None
    secret_configured: bool
    restricted_network_gate_enabled: bool
    production_gate_possible: bool  # always False — structural absence, not a flag
    fiscal_policy_ready: bool
    fiscal_policy_summary: dict
    artifact_storage_ready: bool
    artifact_storage_detail: str
    national_dps_configuration_ready: bool
    national_dps_configuration_summary: dict | None
    last_validation_time: datetime
    blockers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = {
            "environment": self.environment,
            "provider_implementation_available": self.provider_implementation_available,
            "layout_version": self.layout_version,
            "schema_version": self.schema_version,
            "schema_fingerprint": self.schema_fingerprint,
            "certificate_configured": self.certificate_configured,
            "certificate_syntactically_valid": self.certificate_syntactically_valid,
            "certificate_summary": None,
            "secret_configured": self.secret_configured,
            "restricted_network_gate_enabled": self.restricted_network_gate_enabled,
            "production_gate_possible": self.production_gate_possible,
            "fiscal_policy_ready": self.fiscal_policy_ready,
            "fiscal_policy_summary": self.fiscal_policy_summary,
            "artifact_storage_ready": self.artifact_storage_ready,
            "artifact_storage_detail": self.artifact_storage_detail,
            "national_dps_configuration_ready": self.national_dps_configuration_ready,
            "national_dps_configuration_summary": self.national_dps_configuration_summary,
            "last_validation_time": self.last_validation_time.isoformat(),
            "blockers": self.blockers,
        }
        if self.certificate_summary is not None:
            data["certificate_summary"] = {
                "subject_common_name": self.certificate_summary.subject_common_name,
                "not_before": self.certificate_summary.not_before.isoformat()
                if self.certificate_summary.not_before else None,
                "not_after": self.certificate_summary.not_after.isoformat()
                if self.certificate_summary.not_after else None,
                "expired": self.certificate_summary.expired,
            }
        return data


_schema_fingerprint_cache: dict = {}


def _schema_fingerprint() -> str | None:
    """Stable sha256 across both vendored restricted schemas. Cached by mtime
    pair so a hot-reloading dev server still notices a vendored schema change,
    without hashing the files on every single request."""
    try:
        paths = [DPS_SCHEMA_PATH, NFSE_SCHEMA_PATH]
        key = tuple((p, p.stat().st_mtime_ns) for p in paths)
    except OSError:
        return None
    cached = _schema_fingerprint_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    value = digest.hexdigest()
    _schema_fingerprint_cache.clear()  # only one live key needed: paths never change at runtime
    _schema_fingerprint_cache[key] = value
    return value


def _certificate_readiness(settings: Settings) -> tuple[bool, CertificateSummary | None, str | None]:
    """Returns (syntactically_valid_or_None, summary_or_None, blocker_or_None).

    ``None`` for syntactically_valid means "cannot be determined" (path or
    password missing) rather than "invalid" — the two must never be
    conflated in the UI.
    """
    path = settings.nfse_restricted_certificate_path
    password = settings.nfse_restricted_certificate_password
    if not path:
        return None, None, None  # certificate_configured already covers this
    if not os.path.isfile(path):
        return False, None, "restricted_certificate_path_not_a_file"
    if not password:
        return None, None, "restricted_certificate_password_missing"
    try:
        data = Path(path).read_bytes()
        loaded = load_pkcs12_certificate(data, password)
    except SignatureError:
        return False, None, "restricted_certificate_unreadable"
    except OSError:
        return False, None, "restricted_certificate_path_unreadable"
    cert = loaded.certificate
    not_before = cert.not_valid_before_utc if hasattr(cert, "not_valid_before_utc") else cert.not_valid_before.replace(tzinfo=timezone.utc)
    not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    expired = now > not_after or now < not_before
    cn = None
    try:
        from cryptography.x509.oid import NameOID
        attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        cn = attrs[0].value if attrs else None
    except Exception:
        cn = None
    summary = CertificateSummary(subject_common_name=cn, not_before=not_before,
                                 not_after=not_after, expired=expired)
    blocker = "restricted_certificate_expired" if expired else None
    return True, summary, blocker


def _permissions_blocker(path: str | os.PathLike) -> str | None:
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None
    if mode & 0o077 and not _is_private_systemd_credential(path):
        return "restricted_certificate_path_permissions_too_open"
    return None


# M69 — systemd (>= 250) hands a LoadCredential[Encrypted]= file to a unit
# with User= as root:root 0400 plus a POSIX ACL `user:<unit uid>:r--` on a
# read-only tmpfs mounted at /run/credentials/<unit>. With an ACL present the
# group bits of st_mode show the ACL MASK, so the file stats as 0440 although
# the root group has no access at all (measured on the VPS, systemd 255:
# group_obj::0, other::0). The exception below recognises exactly that shape
# and nothing else; any conventional file keeps the plain `mode & 0o077` rule.
_CREDENTIALS_ROOT = "/run/credentials"
_CREDENTIAL_OWNER_UID = 0
_CREDENTIAL_FILESYSTEMS = frozenset({"tmpfs", "ramfs"})
_ACL_USER_OBJ, _ACL_USER, _ACL_GROUP_OBJ, _ACL_GROUP, _ACL_MASK, _ACL_OTHER = 1, 2, 4, 8, 16, 32
_ACL_READ, _ACL_EXECUTE = 4, 1


def _read_posix_acl(path: str) -> list[tuple[int, int, int]] | None:
    """(tag, perm, id) entries of the access ACL, or None if there is none."""
    try:
        raw = os.getxattr(path, "system.posix_acl_access", follow_symlinks=False)
    except OSError:
        return None
    return _parse_posix_acl(raw)


def _parse_posix_acl(raw: bytes) -> list[tuple[int, int, int]] | None:
    # Kernel xattr layout: u32 version (2), then u16 tag, u16 perm, u32 id.
    if len(raw) < 4 or (len(raw) - 4) % 8 or struct.unpack("<I", raw[:4])[0] != 2:
        return None
    return [struct.unpack("<HHI", raw[i:i + 8]) for i in range(4, len(raw), 8)]


def _mountinfo_entry(mount_point: str) -> tuple[str, set[str]] | None:
    """(fstype, per-mount options) of the topmost mount exactly at mount_point."""
    found = None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            for line in fh:
                pre, sep, post = line.partition(" - ")
                fields = pre.split()
                if not sep or len(fields) < 6:
                    continue
                point = fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
                if point == mount_point:
                    found = (post.split()[0], set(fields[5].split(",")))
    except OSError:
        return None
    return found


def _acl_is_private(entries: list[tuple[int, int, int]] | None, allowed: int) -> bool:
    """Only the owner (root) and THIS process's uid may have any access."""
    if not entries:
        return False
    tags = {tag for tag, _perm, _id in entries}
    if not {_ACL_USER_OBJ, _ACL_GROUP_OBJ, _ACL_OTHER} <= tags:
        return False
    for tag, perm, ident in entries:
        if tag in (_ACL_GROUP_OBJ, _ACL_OTHER) and perm:
            return False
        if tag == _ACL_GROUP:
            return False
        if tag == _ACL_USER and ident != os.geteuid():
            return False
        if tag in (_ACL_USER_OBJ, _ACL_USER, _ACL_MASK) and perm & ~allowed:
            return False
    return True


def _is_private_systemd_credential(path: str | os.PathLike) -> bool:
    creds = os.environ.get("CREDENTIALS_DIRECTORY", "")
    root = _CREDENTIALS_ROOT
    if (not creds or os.path.normpath(creds) != creds
            or os.path.dirname(creds) != root or not os.path.basename(creds)
            or os.path.basename(creds) in (".", "..")):
        return False
    path = os.fspath(path)
    if os.path.dirname(path) != creds or os.path.basename(path) in ("", ".", ".."):
        return False
    try:
        dir_st = os.lstat(creds)
        file_st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(dir_st.st_mode) or not stat.S_ISREG(file_st.st_mode):
        return False  # a symlink (either level) never gets the exception
    if dir_st.st_uid != _CREDENTIAL_OWNER_UID or file_st.st_uid != _CREDENTIAL_OWNER_UID:
        return False
    if stat.S_IMODE(dir_st.st_mode) & 0o027 or stat.S_IMODE(file_st.st_mode) & 0o237:
        return False  # no write for anyone, nothing for "other" at all
    if file_st.st_dev != dir_st.st_dev:
        return False
    mount = _mountinfo_entry(creds)
    if mount is None or mount[0] not in _CREDENTIAL_FILESYSTEMS or "ro" not in mount[1]:
        return False
    return (_acl_is_private(_read_posix_acl(creds), _ACL_READ | _ACL_EXECUTE)
            and _acl_is_private(_read_posix_acl(path), _ACL_READ))


def _fiscal_policy_summary(db: Session, environment: str) -> tuple[bool, dict]:
    policies = db.scalars(select(FiscalPolicy).where(FiscalPolicy.environment == environment)).all()
    flows = {"DIRECT", "HOME"}
    validated_by_flow = {
        flow: any(p.flow == flow and p.validation_state == "validated" for p in policies)
        for flow in flows
    }
    ready = all(validated_by_flow.values())
    return ready, {
        "environment": environment,
        "total_versions": len(policies),
        "validated_flow_coverage": validated_by_flow,
    }


def _artifact_storage_readiness(settings: Settings) -> tuple[bool, str]:
    if not settings.nfse_fiscal_artifacts_dir:
        return False, "nfse_fiscal_artifacts_dir_not_configured"
    try:
        settings.resolved_fiscal_artifacts_storage_dir()
    except Exception as exc:  # fail-closed: any structural problem is a blocker, never silently ignored
        return False, f"artifact_storage_unavailable: {exc}"
    return True, "ok"


def compute_provider_readiness(db: Session, settings: Settings, *,
                               environment: str = "restricted") -> ProviderReadiness:
    blockers: list[str] = []

    cert_configured = bool(settings.nfse_restricted_certificate_path)
    secret_configured = bool(settings.nfse_restricted_certificate_password)
    cert_valid, cert_summary, cert_blocker = _certificate_readiness(settings)
    if not cert_configured:
        blockers.append("restricted_certificate_path_missing")
    if not secret_configured:
        blockers.append("restricted_certificate_password_missing")
    if cert_blocker:
        blockers.append(cert_blocker)
    if cert_configured:
        perm_blocker = _permissions_blocker(settings.nfse_restricted_certificate_path)
        if perm_blocker:
            blockers.append(perm_blocker)

    # M59 — endpoint and network gate are per environment. Before M59 this
    # function always read the restricted ones, which was harmless only
    # because production could not be resolved at all; now that it can, a
    # production readiness check that consulted the restricted flag would be
    # the worst kind of wrong — it would report ready for the wrong reason.
    if environment == "production":
        # There is no `nfse_production_base_url` by design: the endpoint is
        # the allowlisted constant, so the only thing to verify is that the
        # constant itself still passes the same allowlist the transport
        # applies per call.
        try:
            assert_production_base_url(PRODUCTION_BASE_URL)
        except NetworkGateClosedError as exc:
            blockers.append(f"production_endpoint_invalid:{exc}")
        if not settings.nfse_production_network_enabled:
            blockers.append("production_network_gate_disabled")
    else:
        if not settings.nfse_restricted_base_url:
            blockers.append("restricted_base_url_missing")
        if not settings.nfse_restricted_network_enabled:
            blockers.append("restricted_network_gate_disabled")

    policy_ready, policy_summary = _fiscal_policy_summary(db, environment)
    if not policy_ready:
        blockers.append("fiscal_policy_incomplete_for_environment")

    storage_ready, storage_detail = _artifact_storage_readiness(settings)
    if not storage_ready:
        blockers.append("artifact_storage_not_ready")

    # M29: a real, versioned NationalDpsConfiguration table now exists
    # (app/services/nfse_national/fiscal_config.py). Checked against TODAY's
    # date so this readiness view always reflects "would a document dated
    # today build". A historical document still resolves the configuration
    # that was effective on ITS OWN competence date — never this snapshot.
    today = datetime.now(timezone.utc).date()
    active_row = fiscal_config.resolve_active_version(db, environment=environment, as_of=today)
    national_config_ready = active_row is not None
    national_config_summary = fiscal_config.safe_summary(active_row) if active_row else None
    if not national_config_ready:
        blockers.append("national_dps_configuration_not_defined_for_any_real_document")

    return ProviderReadiness(
        environment=environment,
        provider_implementation_available=True,
        layout_version=settings.nfse_restricted_layout_version,
        schema_version="DPS_v1.01 / NFSe_v1.01",
        schema_fingerprint=_schema_fingerprint(),
        certificate_configured=cert_configured,
        certificate_syntactically_valid=cert_valid,
        certificate_summary=cert_summary,
        secret_configured=secret_configured,
        restricted_network_gate_enabled=settings.nfse_restricted_network_enabled,
        # M59 — still False, and still for a structural reason rather than a
        # flag: resolving a production provider is now possible, but sending
        # remains blocked by the transport's own per-call gate and by the M56
        # activation gates, one of which (explicit_human_authorization) has
        # no configuration path at all.
        production_gate_possible=False,
        fiscal_policy_ready=policy_ready,
        fiscal_policy_summary=policy_summary,
        artifact_storage_ready=storage_ready,
        artifact_storage_detail=storage_detail,
        national_dps_configuration_ready=national_config_ready,
        national_dps_configuration_summary=national_config_summary,
        last_validation_time=datetime.now(timezone.utc),
        blockers=blockers,
    )
