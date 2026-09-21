"""Certificate validity guard, with a renewal margin for production only.

The real SoproLife A1 certificate expires 2026-11-07. Produção Restrita is
correctly satisfied by "not expired": a homologation document that fails
because the credential died mid-cycle costs nothing but a retry. A
production cycle is different — an issuance refused halfway through leaves
a real fiscal obligation unmet, and the renewal itself takes days of
calendar time with a certifying authority.

So production adds a MARGIN on top of the same check: the certificate must
not merely be valid today, it must stay valid for a configurable number of
days ahead. The margin is a production-only concept and is never consulted
by ``readiness.compute_provider_readiness`` — Produção Restrita keeps
behaving exactly as it did before this module existed.

This module never renews, never downloads and never contacts a certifying
authority. It reads the already-loaded certificate metadata that
``readiness.CertificateSummary`` already exposes (subject CN and validity
window) and answers one question: is there enough runway left to start a
production cycle?
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .readiness import CertificateSummary

# Conservative by design. 30 days is longer than a typical A1 issuance
# appointment plus installation, so hitting this guard still leaves room to
# renew without an emergency. Overridable per deployment
# (``M15_NFSE_PRODUCTION_CERTIFICATE_MIN_DAYS``), never downward by accident:
# the value is read from settings, not inferred.
DEFAULT_PRODUCTION_MIN_DAYS_REMAINING = 30


@dataclass(frozen=True)
class CertificateMargin:
    """No key material, no password, no raw bytes — same discipline as
    ``CertificateSummary``, which is this type's only input."""
    valid: bool
    reason: str
    days_remaining: int | None = None
    required_days: int | None = None
    not_after: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "days_remaining": self.days_remaining,
            "required_days": self.required_days,
            "not_after": self.not_after.isoformat() if self.not_after else None,
        }


def evaluate_certificate_margin(summary: CertificateSummary | None, *,
                                min_days_remaining: int = DEFAULT_PRODUCTION_MIN_DAYS_REMAINING,
                                now: datetime | None = None) -> CertificateMargin:
    """Fail-closed: an absent or unreadable certificate is never "valid".

    ``now`` exists so tests can pin a date without touching the host clock;
    every real caller omits it.
    """
    if summary is None:
        return CertificateMargin(False, "certificate_not_available", required_days=min_days_remaining)
    if summary.not_after is None:
        return CertificateMargin(False, "certificate_validity_window_unknown",
                                 required_days=min_days_remaining)
    moment = now or datetime.now(timezone.utc)
    if summary.not_before is not None and moment < summary.not_before:
        return CertificateMargin(False, "certificate_not_yet_valid",
                                 required_days=min_days_remaining, not_after=summary.not_after)
    # Whole days only, rounded DOWN: a certificate with 29 hours left has
    # one day of runway, never two.
    days_remaining = (summary.not_after - moment).days
    if summary.expired or moment > summary.not_after:
        return CertificateMargin(False, "certificate_expired", days_remaining,
                                 min_days_remaining, summary.not_after)
    if days_remaining < min_days_remaining:
        return CertificateMargin(False, "certificate_renewal_margin_insufficient",
                                 days_remaining, min_days_remaining, summary.not_after)
    return CertificateMargin(True, "certificate_valid_with_margin", days_remaining,
                             min_days_remaining, summary.not_after)
