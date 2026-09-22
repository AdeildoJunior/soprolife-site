"""Production activation gates. This module cannot activate anything.

Independently-named gates, each with its own reason, each evaluating a
condition that is really verifiable rather than a placeholder. There is no
single boolean anywhere that, if accidentally flipped, would make production
possible, and three of the gates cannot be satisfied by configuration at
all: ``restricted_validation_successful`` and ``explicit_human_authorization``
only ever become ``True`` via an explicit argument a human supplies
out-of-band, and ``signed_preflight_passed`` requires a real, technically
green ``ProductionPreflightResult`` to be handed in — never inferred from
database state, never defaulted to ``True``.

What changed in M56, and what deliberately did not:

- ``production_endpoint_correct`` was hard-coded ``False`` because no
  production transport existed. One exists now
  (``transport.HttpxProductionTransport``), so the gate became a real
  check: the running environment must BE production and the endpoint it
  would use must be the official allowlisted HTTPS URL. Under any non-
  production configuration it is still ``False`` — the pre-M56 assertions
  about it hold unchanged, for a better reason.
- No existing gate was removed or weakened. ``provider_ready``,
  ``valid_fiscal_policy``, ``verified_real_credential``,
  ``private_storage_ready`` and ``restricted_validation_successful`` keep
  exactly their previous meanings; five new gates were added beside them.
- ``explicit_human_authorization`` is the pre-M56
  ``explicit_human_production_authorization`` under the name the mission
  specifies. Same refusing default, same keyword argument.

Because that last gate has no configuration path, ``all_satisfied`` is
false by construction for as long as nobody passes it — which is the state
M56 leaves the system in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ...config import Settings
from . import clock as clock_module
from .certificate_guard import evaluate_certificate_margin
from .production_preflight import ProductionPreflightResult
from .readiness import compute_provider_readiness
from .transport import PRODUCTION_BASE_URL, NetworkGateClosedError, assert_production_base_url


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


def _endpoint_gate(settings: Settings) -> ProductionGate:
    """Correct endpoint means BOTH: production is the selected environment,
    and the URL a production POST would use passes the same allowlist the
    transport enforces per call. Under mock/restricted there is no selected
    production endpoint, so the gate is false — never vacuously true."""
    if settings.nfse_environment != "production":
        return ProductionGate(
            "production_endpoint_correct", False,
            f"Ambiente selecionado é '{settings.nfse_environment}': nenhum endpoint de "
            "produção está em uso, portanto não há endpoint correto a confirmar.")
    try:
        url = assert_production_base_url(PRODUCTION_BASE_URL)
    except NetworkGateClosedError as exc:
        return ProductionGate("production_endpoint_correct", False,
                              f"URL de produção recusada pela allowlist: {exc}.")
    return ProductionGate("production_endpoint_correct", True,
                          f"Endpoint oficial HTTPS confirmado por allowlist de host: {url}.")


def compute_production_readiness(db: Session, settings: Settings, *,
                                 restricted_validation_confirmed_by_human: bool = False,
                                 explicit_human_production_authorization: bool = False,
                                 signed_preflight: ProductionPreflightResult | None = None,
                                 ) -> ProductionReadiness:
    """All three keyword flags default to refusing and MUST be supplied
    explicitly by a caller acting on a documented human decision or on a
    real preflight run — there is no configuration flag or environment
    variable that sets any of them, on purpose."""
    restricted = compute_provider_readiness(db, settings, environment="restricted")
    cert_ok = (restricted.certificate_syntactically_valid is True and
               not (restricted.certificate_summary and restricted.certificate_summary.expired))
    margin = evaluate_certificate_margin(
        restricted.certificate_summary,
        min_days_remaining=settings.nfse_production_certificate_min_days)
    clock = clock_module.read_clock_status()
    environment_is_production = settings.nfse_environment == "production"
    network_gate_enabled = bool(settings.nfse_production_network_enabled)
    preflight_ok = signed_preflight is not None and signed_preflight.technical_ready

    gates = [
        ProductionGate(
            "environment_is_production", environment_is_production,
            "M15_NFSE_ENVIRONMENT=production." if environment_is_production
            else f"Ambiente configurado é '{settings.nfse_environment}', não 'production'.",
        ),
        _endpoint_gate(settings),
        ProductionGate(
            "network_gate_enabled", network_gate_enabled,
            "M15_NFSE_PRODUCTION_NETWORK_ENABLED=true (portão próprio, independente do restrito)."
            if network_gate_enabled
            else "M15_NFSE_PRODUCTION_NETWORK_ENABLED está desligado. Ligar o portão restrito "
                 "não tem efeito algum aqui: são variáveis e transportes separados.",
        ),
        ProductionGate(
            "signed_preflight_passed", preflight_ok,
            "Preflight OFFLINE de produção executado e tecnicamente aprovado."
            if preflight_ok
            else "Requer um ProductionPreflightResult real com technical_ready=true "
                 "(XSD, XMLDSig, dhEmi, prefixos de namespace, configuração fiscal) — "
                 "nunca inferido, nunca presumido.",
        ),
        ProductionGate(
            "certificate_valid", margin.valid,
            f"Certificado válido com {margin.days_remaining} dia(s) de margem "
            f"(mínimo exigido: {margin.required_days})." if margin.valid
            else f"Certificado reprovado para produção: {margin.reason} "
                 f"(dias restantes: {margin.days_remaining}, mínimo: {margin.required_days}). "
                 "O A1 atual vence em 2026-11-07 e deve ser renovado antes da primeira "
                 "emissão de produção.",
        ),
        ProductionGate(
            "clock_synchronized", clock.synchronized,
            f"Relógio do host disciplinado por NTP (erro máximo {clock.max_error_seconds}s)."
            if clock.synchronized
            else f"Relógio do host não sincronizado: {clock.reason}. dhEmi sai deste relógio "
                 "e um desvio reproduz a rejeição E0008 que derrubou a DPS #9.",
        ),
        ProductionGate(
            "explicit_human_authorization", explicit_human_production_authorization,
            "Autorizado explicitamente." if explicit_human_production_authorization
            else "Requer autorização humana explícita e documentada — nunca um valor padrão, "
                 "nunca uma variável de ambiente, nunca inferida do banco.",
        ),
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
    ]
    return ProductionReadiness(gates=gates)
