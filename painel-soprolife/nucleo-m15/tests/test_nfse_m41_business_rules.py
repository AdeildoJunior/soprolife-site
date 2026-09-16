"""M41 — SEFIN DPS business-rule fixes, cited from the official
``ANEXO_I-SEFIN_ADN-DPS_NFSe-SNNFSe-PRODREST-v1.01-20260209.xlsx`` (the same
version already vendored/used to build the XSD package for this layout).

Context: DPS #4 and #5 both reached the confirmed SEFIN Nacional endpoint
with a valid XSD/signature and were still REJECTED with HTTP 400 — proof
that XSD validity alone does not guarantee business-rule compliance. This
file proves two concrete, previously-unproven violations are now fixed:

- E0121: ``prest/xNome`` must never be informed when ``tpEmit=1`` (the
  provider issues its own DPS) — our builder always used tpEmit=1 but
  unconditionally emitted xNome anyway.
- E0166/E0162/E0712/E0175/E0174: cross-field invariants for a Simples
  Nacional (ME/EPP, opSimpNac=3) issuer, now enforced at configuration
  validation time so a non-compliant config can never be created.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from lxml import etree
from pydantic import ValidationError

from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import DpsInput, Recipient, build_dps_element
from app.services.nfse_national.identifiers import DpsIdComponents
from app.services.nfse_national.xsd_validation import validate_dps_xml

NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"


def synthetic_config(**changes) -> NationalDpsConfiguration:
    data = dict(
        version="SYNTH-M41-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="63544026000110", issuer_name="SoproLife Diagnósticos e Soluções em Saúde LTDA",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3,
        issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="040201", codigo_tributacao_municipal="001",
        codigo_nbs="123019900",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SOPROLIFE-M41-GOLDEN",
        p_tot_trib_sn=Decimal("6.00"),
    )
    data.update(changes)
    return NationalDpsConfiguration.model_validate(data)


def synthetic_dps_input(**changes) -> DpsInput:
    dps_id = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                             inscricao_federal="63544026000110", serie_dps="00001",
                             numero_dps="000000000000006")
    data = dict(
        config=synthetic_config(), dps_id=dps_id,
        dh_emi=datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc),
        ver_aplic="soprolife-m41-0.1", numero_dps_display="6", serie_dps_display="1",
        competencia=date(2026, 9, 15),
        tomador=Recipient(nome="Paciente Sintetico M41", cpf="11144477735"),
        descricao_servico="Realização de exame de espirometria sem broncodilatador em 15/09/2026.",
        valor_servico=Decimal("360.00"),
        municipio_prestacao_ibge="3304557",
    )
    data.update(changes)
    return DpsInput(**data)


def _find(root, path):
    return root.find(path.replace("/", f"/{{{NFSE_NS}}}").replace("^", f"{{{NFSE_NS}}}"))


# ============================================================ E0121 — prest/xNome forbidden


def test_tpEmit_is_always_1():
    root = build_dps_element(synthetic_dps_input())
    tp_emit = _find(root, "^infDPS/tpEmit")
    assert tp_emit is not None and tp_emit.text == "1"


def test_prest_xNome_absent_when_tpEmit_1():
    """E0121: 'Se o emitente da DPS for o prestador de serviço (tpEmit for
    igual a 1), então o nome ou razão social não deve ser informado.'"""
    root = build_dps_element(synthetic_dps_input())
    prest = _find(root, "^infDPS/prest")
    assert prest is not None
    x_nome = prest.find(f"{{{NFSE_NS}}}xNome")
    assert x_nome is None, "prest/xNome must never be emitted when tpEmit=1 (E0121)"


def test_prest_cnpj_still_present():
    root = build_dps_element(synthetic_dps_input())
    cnpj = _find(root, "^infDPS/prest/CNPJ")
    assert cnpj is not None and cnpj.text == "63544026000110"


def test_prest_address_group_never_emitted():
    """E0128: provider address forbidden when tpEmit=1 — analogous
    'must-not-be-informed' rule audited alongside E0121. This builder never
    had an address-emission code path at all, so this is a standing
    invariant, not a new fix."""
    root = build_dps_element(synthetic_dps_input())
    prest = _find(root, "^infDPS/prest")
    assert prest.find(f"{{{NFSE_NS}}}end") is None


def test_prest_nif_and_cNaoNIF_never_emitted():
    """E0112/E0114: NIF/cNaoNIF forbidden for the provider when tpEmit=1 —
    the builder only ever emits CNPJ for prest, so these can never appear."""
    root = build_dps_element(synthetic_dps_input())
    prest = _find(root, "^infDPS/prest")
    assert prest.find(f"{{{NFSE_NS}}}NIF") is None
    assert prest.find(f"{{{NFSE_NS}}}cNaoNIF") is None


def test_output_is_still_schema_valid_after_removing_xNome():
    root = build_dps_element(synthetic_dps_input())
    from app.services.nfse_national.dps_builder import serialize_dps
    validate_dps_xml(serialize_dps(root))


# ============================================================ E0166/E0162 — regApTribSN


def test_e0166_regApTribSN_required_when_opSimpNac_3():
    with pytest.raises(ValidationError, match="E0166"):
        synthetic_config(issuer_reg_ap_trib_sn=None)


def test_e0162_regApTribSN_forbidden_when_opSimpNac_1():
    with pytest.raises(ValidationError, match="E0162"):
        synthetic_config(issuer_op_simp_nac=1, issuer_reg_ap_trib_sn=1,
                         p_tot_trib_sn=None)


def test_e0162_regApTribSN_forbidden_when_opSimpNac_2_mei():
    with pytest.raises(ValidationError, match="E0162"):
        synthetic_config(issuer_op_simp_nac=2, issuer_reg_ap_trib_sn=1,
                         p_tot_trib_sn=None)


def test_opSimpNac_1_without_regApTribSN_is_valid():
    cfg = synthetic_config(issuer_op_simp_nac=1, issuer_reg_ap_trib_sn=None,
                           p_tot_trib_sn=None)
    assert cfg.issuer_reg_ap_trib_sn is None


# ============================================================ E0712 — indTotTrib forbidden for ME/EPP


def test_e0712_p_tot_trib_sn_required_when_opSimpNac_3():
    with pytest.raises(ValidationError, match="E0712"):
        synthetic_config(p_tot_trib_sn=None)


def test_e0712_builder_never_falls_back_to_indTotTrib_for_current_profile():
    """With a valid opSimpNac=3 config (p_tot_trib_sn always required by the
    E0712 guard above), the builder must choose pTotTribSN, never
    indTotTrib — the forbidden choice for ME/EPP."""
    root = build_dps_element(synthetic_dps_input())
    tot_trib = _find(root, "^infDPS/valores/trib/totTrib")
    assert tot_trib.find(f"{{{NFSE_NS}}}indTotTrib") is None
    p_tot_trib_sn = tot_trib.find(f"{{{NFSE_NS}}}pTotTribSN")
    assert p_tot_trib_sn is not None and p_tot_trib_sn.text == "6.00"


# ============================================================ E0175/E0174 — regEspTrib


def test_e0175_regEspTrib_must_be_zero_when_regApTribSN_1():
    with pytest.raises(ValidationError, match="E0175"):
        synthetic_config(issuer_reg_esp_trib=1)  # regApTribSN=1 from the base config


def test_e0174_regEspTrib_must_be_zero_when_mei():
    with pytest.raises(ValidationError, match="E0174"):
        synthetic_config(issuer_op_simp_nac=2, issuer_reg_ap_trib_sn=None,
                         p_tot_trib_sn=None, issuer_reg_esp_trib=1)


# ============================================================ cross-check against the REAL active profile


def test_actual_soprolife_golden_profile_satisfies_every_new_invariant():
    """The exact field values of the real, already-versioned
    SOPROLIFE-M31-GOLDEN-v1 configuration (used by DPS #1-#5) — proves the
    new invariants do not require ANY change to the already-correct active
    configuration, only that the builder/model now enforce them."""
    cfg = NationalDpsConfiguration(
        version="SOPROLIFE-M31-GOLDEN-v1", layout_version="restricted-v1.01-20260727",
        tp_amb=2, issuer_cnpj="63544026000110",
        issuer_name="SoproLife Diagnósticos e Soluções em Saúde LTDA",
        issuer_municipio_ibge="3304557", issuer_inscricao_municipal=None,
        issuer_op_simp_nac=3, issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="040201", codigo_tributacao_municipal="001",
        codigo_nbs="123019900", trib_issqn=1, tp_ret_issqn=1,
        aliquota_percentual=None, ind_tot_trib=0, p_tot_trib_sn=Decimal("6.00"),
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SOPROLIFE-M31-GOLDEN",
    )
    assert cfg.issuer_reg_ap_trib_sn == 1
    assert cfg.p_tot_trib_sn == Decimal("6.00")
    assert cfg.issuer_reg_esp_trib == 0
