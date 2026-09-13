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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import FiscalPolicy
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
    if mode & 0o077:
        return "restricted_certificate_path_permissions_too_open"
    return None


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

    # Always present, regardless of every other flag: no real tax
    # configuration (NationalDpsConfiguration) exists for any real document
    # yet — that is an explicit accounting/legal decision deferred past this
    # foundation (see the M27 report, "Pré-requisitos restantes" #4). Naming
    # it here keeps the blocker list exhaustive rather than silently stopping
    # at "everything above looks fine".
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
        production_gate_possible=False,
        fiscal_policy_ready=policy_ready,
        fiscal_policy_summary=policy_summary,
        artifact_storage_ready=storage_ready,
        artifact_storage_detail=storage_detail,
        last_validation_time=datetime.now(timezone.utc),
        blockers=blockers,
    )
