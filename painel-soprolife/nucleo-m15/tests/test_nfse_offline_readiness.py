"""Offline automation foundation — provider readiness + production activation
structure. Nothing here ever opens a real certificate or touches a socket;
every certificate is generated on the fly by
``nfse_national.signer.generate_synthetic_test_certificate``.
"""
import os

from app.config import Settings
from app.services import nfse
from app.services.nfse_national.production_gates import compute_production_readiness
from app.services.nfse_national.readiness import compute_provider_readiness
from app.services.nfse_national.signer import generate_synthetic_test_certificate
from tests.test_nfse_foundation import policy_payload


def restricted_policy_payload(flow):
    return policy_payload(version=f'SYNTH-RESTRICTED-{flow}', environment='restricted', flow=flow)


def test_readiness_all_blocked_by_default(db):
    settings = Settings()
    result = compute_provider_readiness(db, settings, environment='restricted')
    assert result.certificate_configured is False
    assert result.certificate_syntactically_valid is None
    assert result.secret_configured is False
    assert result.restricted_network_gate_enabled is False
    assert result.production_gate_possible is False
    assert result.fiscal_policy_ready is False
    assert result.artifact_storage_ready is False
    assert 'restricted_certificate_path_missing' in result.blockers
    assert 'restricted_certificate_password_missing' in result.blockers
    assert 'restricted_base_url_missing' in result.blockers
    assert 'restricted_network_gate_disabled' in result.blockers
    assert 'fiscal_policy_incomplete_for_environment' in result.blockers
    assert 'artifact_storage_not_ready' in result.blockers
    assert 'national_dps_configuration_not_defined_for_any_real_document' in result.blockers
    # Never a secret leak in the safe dict form either (blocker CODE names
    # like "restricted_certificate_password_missing" are fine — an actual
    # password value is not).
    assert result.as_dict()['secret_configured'] is False


def test_readiness_fiscal_policy_ready_when_both_flows_validated(db, users):
    nfse.create_policy(db, restricted_policy_payload('DIRECT'), users['admin'].id)
    nfse.create_policy(db, restricted_policy_payload('HOME'), users['admin'].id)
    result = compute_provider_readiness(db, Settings(), environment='restricted')
    assert result.fiscal_policy_ready is True
    assert result.fiscal_policy_summary['validated_flow_coverage'] == {'DIRECT': True, 'HOME': True}
    assert 'fiscal_policy_incomplete_for_environment' not in result.blockers


def test_readiness_artifact_storage_ready_with_configured_dir(db, tmp_path):
    settings = Settings(nfse_fiscal_artifacts_dir=tmp_path / 'fiscal-artifacts')
    result = compute_provider_readiness(db, settings, environment='restricted')
    assert result.artifact_storage_ready is True
    assert 'artifact_storage_not_ready' not in result.blockers


def test_readiness_certificate_valid_reports_safe_summary_only(db, tmp_path):
    p12_bytes, password = generate_synthetic_test_certificate(common_name='SoproLife Synthetic Readiness')
    cert_path = tmp_path / 'synthetic.p12'
    cert_path.write_bytes(p12_bytes)
    os.chmod(cert_path, 0o600)
    settings = Settings(nfse_restricted_certificate_path=cert_path,
                        nfse_restricted_certificate_password=password)
    result = compute_provider_readiness(db, settings, environment='restricted')
    assert result.certificate_configured is True
    assert result.secret_configured is True
    assert result.certificate_syntactically_valid is True
    assert result.certificate_summary is not None
    assert result.certificate_summary.subject_common_name == 'SoproLife Synthetic Readiness'
    assert result.certificate_summary.expired is False
    dumped = result.as_dict()
    assert password not in str(dumped)


def test_readiness_flags_insecure_certificate_permissions(db, tmp_path):
    p12_bytes, password = generate_synthetic_test_certificate()
    cert_path = tmp_path / 'synthetic.p12'
    cert_path.write_bytes(p12_bytes)
    os.chmod(cert_path, 0o644)  # world-readable — must be flagged
    settings = Settings(nfse_restricted_certificate_path=cert_path,
                        nfse_restricted_certificate_password=password)
    result = compute_provider_readiness(db, settings, environment='restricted')
    assert 'restricted_certificate_path_permissions_too_open' in result.blockers


def test_readiness_wrong_password_is_unreadable_not_a_crash(db, tmp_path):
    p12_bytes, _password = generate_synthetic_test_certificate()
    cert_path = tmp_path / 'synthetic.p12'
    cert_path.write_bytes(p12_bytes)
    os.chmod(cert_path, 0o600)
    settings = Settings(nfse_restricted_certificate_path=cert_path,
                        nfse_restricted_certificate_password='wrong-password-entirely')
    result = compute_provider_readiness(db, settings, environment='restricted')
    assert result.certificate_syntactically_valid is False
    assert 'restricted_certificate_unreadable' in result.blockers


def test_schema_fingerprint_is_stable_and_present(db):
    settings = Settings()
    a = compute_provider_readiness(db, settings, environment='restricted')
    b = compute_provider_readiness(db, settings, environment='restricted')
    assert a.schema_fingerprint is not None
    assert a.schema_fingerprint == b.schema_fingerprint


# -------------------------------------------------------- production gates


def test_production_readiness_never_all_satisfied_without_real_credential(db):
    result = compute_production_readiness(db, Settings())
    assert result.all_satisfied is False
    by_name = {g.name: g for g in result.gates}
    assert by_name['production_endpoint_correct'].satisfied is False
    assert by_name['verified_real_credential'].satisfied is False
    assert by_name['restricted_validation_successful'].satisfied is False
    assert by_name['explicit_human_production_authorization'].satisfied is False


def test_production_endpoint_gate_is_structurally_impossible_even_with_everything_else(db, users, tmp_path):
    """Even if every other gate is satisfied AND a human explicitly confirms
    both restricted validation and production authorization, production must
    still be impossible — because no production transport exists in code."""
    nfse.create_policy(db, restricted_policy_payload('DIRECT'), users['admin'].id)
    nfse.create_policy(db, restricted_policy_payload('HOME'), users['admin'].id)
    p12_bytes, password = generate_synthetic_test_certificate()
    cert_path = tmp_path / 'synthetic.p12'
    cert_path.write_bytes(p12_bytes)
    os.chmod(cert_path, 0o600)
    settings = Settings(
        nfse_restricted_certificate_path=cert_path, nfse_restricted_certificate_password=password,
        nfse_restricted_base_url='https://restrito.example.gov.br',
        nfse_restricted_network_enabled=True,
        nfse_fiscal_artifacts_dir=tmp_path / 'fiscal-artifacts',
    )
    result = compute_production_readiness(
        db, settings, restricted_validation_confirmed_by_human=True,
        explicit_human_production_authorization=True,
    )
    by_name = {g.name: g for g in result.gates}
    assert by_name['production_endpoint_correct'].satisfied is False
    assert result.all_satisfied is False


def test_production_gates_default_to_false_never_true_by_omission(db):
    result = compute_production_readiness(db, Settings())
    by_name = {g.name: g for g in result.gates}
    assert by_name['restricted_validation_successful'].satisfied is False
    assert by_name['explicit_human_production_authorization'].satisfied is False
