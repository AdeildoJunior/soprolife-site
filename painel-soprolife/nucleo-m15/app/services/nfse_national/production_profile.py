"""M60 — the PRODUCTION fiscal profile, derived from the proven homologation one.

WHY THIS IS A MODULE AND NOT A DATABASE SEED

``NationalDpsConfiguration`` rows are versioned and immutable once written
(``fiscal_config.create_version`` refuses to mutate a version that may already
have backed a signed DPS). A profile that will eventually authorise real
invoices therefore has to be reviewable BEFORE it is written anywhere: in a
diff, with provenance per field, under the same review as code. Writing it
straight into a database as a one-off script argument would make the values
themselves unreviewable — exactly the wrong property for the numbers that
decide how much tax the company declares.

So this module states the profile once, explains where every value comes
from, and ``scripts/nfse_m60_production_profile_apply.py`` is the only thing
that writes it. M60 does not run that script against any live database.

WHERE THE VALUES COME FROM

Every field below is copied from ``SOPROLIFE-M43-GOLDEN-v1/v2``, the
validated restricted profile that actually issued DPS #10 — a real NFS-e
accepted by SEFIN on 2026-09-20 under tpAmb=2. They are the company's own
tax parameters, checked by its accountant and then proven end to end against
the government's own validation. Nothing here is invented.

WHAT DELIBERATELY DOES NOT CARRY OVER

``tp_amb`` is the one fiscal value that MUST differ (2 -> 1); M59 made the
environment its source of truth and the builder now refuses a profile that
disagrees with the environment it is used in.

``version`` and ``validation_reference`` are provenance, not tax parameters,
and are restated truthfully: this profile is DERIVED from the homologation
evidence and has NOT itself been proven in production, because nothing has.
Calling it "GOLDEN" would claim a validation that does not exist yet.

WHAT IS CARRIED OVER BUT WORTH A HUMAN'S EYES

``layout_version='restricted-v1.01-20260727'`` reads like an environment
marker and is not one: it identifies the vendored national schema set
(DPS_v1.01 / NFSe_v1.01), which is the same document layout in both
environments, and ``Settings.nfse_restricted_layout_version`` types it as the
single supported value. Carrying it over is correct; the misleading name is
noted rather than renamed, since renaming it is a settings-and-migration
change with no fiscal effect.

``issuer_inscricao_municipal=None`` is schema-optional and was accepted by
SEFIN throughout the restricted cycle. An homologation acceptance is not
proof about production registration, so this is flagged in the M60 report as
a point for the accountant to confirm — not guessed at here, and not a
blocker either, since the field is optional and the proven combination omits
it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .config import NationalDpsConfiguration

# The restricted profile every value below is taken from. Named so a reader
# can go and diff the two.
DERIVED_FROM_VERSION = "SOPROLIFE-M43-GOLDEN-v1"

PRODUCTION_PROFILE_VERSION = "SOPROLIFE-PRODUCTION-v1"

# Deliberately NOT "today". A fiscal configuration's effective_from decides
# which profile a document of a given COMPETENCE resolves, so backdating it
# to the start of the fiscal year keeps a historical redo resolving the same
# way it would have. It is not a licence to issue for past competences —
# eligibility is a separate gate.
PRODUCTION_EFFECTIVE_FROM = date(2026, 1, 1)


def production_configuration() -> NationalDpsConfiguration:
    """The production profile, validated by the same schema as every other.

    Built fresh on each call so a caller cannot mutate a shared instance.
    """
    return NationalDpsConfiguration(
        version=PRODUCTION_PROFILE_VERSION,
        # Provenance, stated honestly: derived from the homologation-proven
        # profile, not itself proven in production.
        validation_reference=f"DERIVED-FROM-{DERIVED_FROM_VERSION}-NOT-YET-PRODUCTION-PROVEN",

        # --- the one value that must differ -------------------------------
        tp_amb=1,   # Produção. M59: the environment is the source of truth
                    # and the builder refuses any disagreement.

        # --- schema set (same document layout in both environments) -------
        layout_version="restricted-v1.01-20260727",

        # --- issuer: SoproLife's own registration -------------------------
        issuer_cnpj="63544026000110",
        issuer_name="SoproLife Diagnósticos e Soluções em Saúde LTDA",
        issuer_municipio_ibge="3304557",          # Rio de Janeiro/RJ
        issuer_inscricao_municipal=None,          # see module docstring
        issuer_op_simp_nac=3,                     # Simples Nacional
        issuer_reg_ap_trib_sn=1,                  # apuração federal+municipal pelo SN
        issuer_reg_esp_trib=0,                    # nenhum regime especial

        # --- service classification (LC 116/2003 and municipal) -----------
        codigo_tributacao_nacional="040201",
        codigo_tributacao_municipal="001",
        codigo_nbs="123019900",

        # --- ISSQN --------------------------------------------------------
        trib_issqn=1,
        tp_ret_issqn=1,
        aliquota_percentual=None,   # not applicable under this SN regime

        # --- total tax burden (TCTribTotal xs:choice) ---------------------
        ind_tot_trib=0,
        p_tot_trib_sn=Decimal("6.00"),

        # --- PIS/COFINS ---------------------------------------------------
        pis_cofins_cst="00",
        pis_cofins_tp_ret=0,

        # --- monetary and competence contract -----------------------------
        amount_basis="financial_entry.valor",
        competence_rule="service_date",
        own_revenue_confirmed=True,
    )


# The fields that are pure provenance rather than tax parameters. Everything
# else must be byte-identical to the derived-from profile, which
# ``test_nfse_m60_production_readiness.py`` checks against the real stored
# configuration rather than against a copy of it.
PROVENANCE_FIELDS = frozenset({"version", "validation_reference", "tp_amb"})


# ===========================================================================
# M60 — the PRODUCTION fiscal policies, one per flow.
#
# ``readiness`` requires a VALIDATED policy for BOTH flows before a provider
# resolves; M59 found that out by assembling a fixture and being refused.
# Same provenance rule as the profile above: every value is taken from
# ``SOPROLIFE-M31-{HOME,DIRECT}-v1``, the validated restricted policies that
# backed DPS #10. Those two are fiscally IDENTICAL to each other — they
# differ only in ``flow`` — so the production pair is built from one shared
# body rather than two copies that could drift apart.
#
# The fiscal invariants these policies exist to keep are unchanged and are
# not restated as new rules here, because they live in ``nfse.evaluate()``
# and are already pinned by the M26 foundation tests: FinancialEntry is the
# only revenue source; exactly one eligible received entry; physician
# transfer/cost is never revenue and is never subtracted from gross; SPLIT /
# Pastore stays blocked; the service must be performed; competence is the
# service date; the municipality is explicit with no silent Rio default; and
# idempotency is preserved. M60 adds a production environment to that
# machinery — it does not soften any of it.
# ===========================================================================

PRODUCTION_POLICY_VERSIONS = {
    "HOME": "SOPROLIFE-PRODUCTION-HOME-v1",
    "DIRECT": "SOPROLIFE-PRODUCTION-DIRECT-v1",
}

# Same window as the restricted pair. A policy is resolved by the document's
# competence, so the window has to cover the fiscal year, not just today.
PRODUCTION_POLICY_EFFECTIVE_FROM = date(2026, 1, 1)
PRODUCTION_POLICY_EFFECTIVE_TO = date(2026, 12, 31)

DERIVED_FROM_POLICY_VERSIONS = ("SOPROLIFE-M31-HOME-v1", "SOPROLIFE-M31-DIRECT-v1")

# Every field of ``TaxConfiguration``, stated explicitly. A validated policy
# may have no missing field, so there is nothing optional to leave out — and
# spelling them all out is what makes this reviewable as tax data.
_PRODUCTION_TAX_CONFIGURATION = {
    "national_service_code": "040201",
    "municipal_service_code": "001",
    "service_list_item": "04.02",
    "tax_rate": "6.00",
    "tax_regime": "SIMPLES_NACIONAL",
    "withholding": False,
    "enforceability": "NORMAL",
    "incidence": "RIO_DE_JANEIRO_RJ",
    "municipality": "3304557",
    "ibs_cbs_treatment": "LAYOUT_V1_01",
    "amount_basis": "financial_entry.valor",
    "competence_rule": "service_date",
    "issuer": "SOPROLIFE",
    "recipient": "service_person",
    "own_revenue_confirmed": True,
    # Provenance, restated truthfully — see the module docstring.
    "validation_reference": "DERIVED-FROM-SOPROLIFE-M31-GOLDEN",
}

# Only these may differ from the restricted policy they derive from.
POLICY_PROVENANCE_FIELDS = frozenset({"validation_reference"})


def production_policy(flow: str) -> "PolicyCreate":
    """The production policy for one flow, validated by the same schema.

    ``validation_state='validated'`` is deliberate and is not a shortcut: the
    schema refuses to validate a policy with any missing field, so this only
    succeeds because every field above is stated. It also does not authorise
    anything by itself — a validated policy is one of the readiness gates,
    not permission to send.
    """
    from ...fiscal_schemas import PolicyCreate

    if flow not in PRODUCTION_POLICY_VERSIONS:
        raise ValueError(f"Fluxo desconhecido: {flow!r}. Conhecidos: "
                         f"{', '.join(sorted(PRODUCTION_POLICY_VERSIONS))}.")
    return PolicyCreate.model_validate({
        "version": PRODUCTION_POLICY_VERSIONS[flow],
        "environment": "production",
        "flow": flow,
        "service": "spirometry",
        "effective_from": PRODUCTION_POLICY_EFFECTIVE_FROM,
        "effective_to": PRODUCTION_POLICY_EFFECTIVE_TO,
        "validation_state": "validated",
        "configuration": dict(_PRODUCTION_TAX_CONFIGURATION),
    })


def production_policies() -> list["PolicyCreate"]:
    """Both flows. Readiness requires both, so they are produced together."""
    return [production_policy(flow) for flow in sorted(PRODUCTION_POLICY_VERSIONS)]
