"""Production activation STRUCTURE only — this module cannot activate anything.

Multiple independently-named gates, each with its own reason. There is no
single boolean anywhere that, if accidentally flipped, would make production
possible: ``production_endpoint_correct`` is hard-coded ``False`` because no
production transport implementation exists in this codebase (see
``transport.ProductionTransport`` — it always raises), and
``verified_real_credential``/``restricted_validation_successful``/
``explicit_human_production_authorization`` can only become ``True`` via an
explicit argument a human supplies out-of-band — never inferred from
database state, and never defaulted to ``True``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ...config import Settings
from .readiness import compute_provider_readiness


@dataclass(frozen=True)
class ProductionGate:
    name: str
    satisfied: bool
    detail: str


@dataclass(frozen=True)
class ProductionReadiness:
    gates: list[ProductionGate] = field(default_factory=list)

    @property
    def all_satisfied(self) -> bool:
        return bool(self.gates) and all(g.satisfied for g in self.gates)

    def as_dict(self) -> dict:
        return {
            "all_satisfied": self.all_satisfied,
            "gates": [{"name": g.name, "satisfied": g.satisfied, "detail": g.detail}
                      for g in self.gates],
        }


def compute_production_readiness(db: Session, settings: Settings, *,
                                 restricted_validation_confirmed_by_human: bool = False,
                                 explicit_human_production_authorization: bool = False,
                                 ) -> ProductionReadiness:
    """Both keyword arguments default to False and MUST be supplied explicitly
    by a caller acting on a documented human decision — there is no
    configuration flag or environment variable that sets them, on purpose."""
    restricted = compute_provider_readiness(db, settings, environment="restricted")
    cert_ok = (restricted.certificate_syntactically_valid is True and
               not (restricted.certificate_summary and restricted.certificate_summary.expired))
    gates = [
        ProductionGate(
            "provider_ready", not restricted.blockers,
            "Sem bloqueios remanescentes no provedor restrito." if not restricted.blockers
            else f"{len(restricted.blockers)} bloqueio(s) restrito(s) pendente(s): "
                 + ", ".join(restricted.blockers),
        ),
        ProductionGate(
            "valid_fiscal_policy", restricted.fiscal_policy_ready,
            "Política fiscal validada para DIRECT e HOME." if restricted.fiscal_policy_ready
            else "Política fiscal incompleta para o ambiente restrito.",
        ),
        ProductionGate(
            "verified_real_credential", cert_ok,
            "Certificado configurado, aberto e dentro da validade."
            if cert_ok else "Certificado ausente, ilegível, expirado ou não verificado.",
        ),
        ProductionGate(
            "private_storage_ready", restricted.artifact_storage_ready,
            restricted.artifact_storage_detail,
        ),
        ProductionGate(
            "restricted_validation_successful", restricted_validation_confirmed_by_human,
            "Confirmado explicitamente por decisão humana." if restricted_validation_confirmed_by_human
            else "Requer confirmação humana explícita de uma chamada restrita bem-sucedida "
                 "— nunca inferido do banco de dados.",
        ),
        ProductionGate(
            "production_endpoint_correct", False,
            "Não existe transporte de produção implementado nesta fundação "
            "(ausência estrutural — ver transport.ProductionTransport — não uma configuração).",
        ),
        ProductionGate(
            "explicit_human_production_authorization", explicit_human_production_authorization,
            "Autorizado explicitamente." if explicit_human_production_authorization
            else "Requer autorização humana explícita e documentada — nunca um valor padrão.",
        ),
    ]
    return ProductionReadiness(gates=gates)
