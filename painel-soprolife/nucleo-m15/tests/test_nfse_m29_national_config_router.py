"""M29 — /fiscal/configuracao-nacional admin API: RBAC, versioning, and safe display.

Never displays a secret (this table never holds one), but the request/
response shapes are checked explicitly so a future field addition cannot
silently leak something unrelated.
"""
import tests.test_nfse_foundation as _foundation

fiscal_enabled = _foundation.fiscal_enabled  # pytest fixture reuse


def synthetic_configuration_payload(**overrides):
    payload = {
        "environment": "restricted",
        "effective_from": "2026-01-01",
        "validation_state": "validated",
        "configuration": {
            "version": "SOPROLIFE-M29-ROUTER-v1", "layout_version": "restricted-v1.01-20260727",
            "issuer_cnpj": "63544026000110",
            "issuer_name": "SoproLife Diagnósticos e Soluções em Saúde LTDA",
            "issuer_municipio_ibge": "3304557", "issuer_op_simp_nac": 3,
            "issuer_reg_ap_trib_sn": 1, "issuer_reg_esp_trib": 0,
            "codigo_tributacao_nacional": "040201", "codigo_tributacao_municipal": "001",
            "codigo_nbs": "123019900", "municipio_prestacao_ibge": "3304557",
            "trib_issqn": 1, "tp_ret_issqn": 1, "p_tot_trib_sn": "6.00",
            "amount_basis": "financial_entry.valor", "competence_rule": "service_date",
            "own_revenue_confirmed": True, "validation_reference": "SOPROLIFE-M29-ROUTER-TEST",
        },
    }
    payload.update(overrides)
    return payload


def test_rbac_only_admin_can_create_a_version(fiscal_enabled, client, auth):
    for role in ["leitura", "operacional", "gestor"]:
        resp = client.post("/api/v1/fiscal/configuracao-nacional",
                           json=synthetic_configuration_payload(), headers=auth(role))
        assert resp.status_code == 403, (role, resp.text)
    resp = client.post("/api/v1/fiscal/configuracao-nacional",
                       json=synthetic_configuration_payload(), headers=auth("admin"))
    assert resp.status_code == 201, resp.text


def test_rbac_leitura_can_read_lists_and_active(fiscal_enabled, client, auth):
    client.post("/api/v1/fiscal/configuracao-nacional",
               json=synthetic_configuration_payload(), headers=auth("admin"))
    for role in ["leitura", "operacional", "gestor", "admin"]:
        assert client.get("/api/v1/fiscal/configuracao-nacional",
                          headers=auth(role)).status_code == 200
        assert client.get("/api/v1/fiscal/configuracao-nacional/ativa?environment=restricted",
                          headers=auth(role)).status_code == 200


def test_medical_role_cannot_read_fiscal_configuration(fiscal_enabled, client, db):
    from app.security import issue_token
    from tests.conftest import _make_user

    doctor = _make_user(db, "physician-m29-config@synthetic.invalid", "medico")
    headers = {"Authorization": "Bearer " + issue_token(doctor.id, doctor.password_hash)}
    assert client.get("/api/v1/fiscal/configuracao-nacional", headers=headers).status_code == 403
    assert client.post("/api/v1/fiscal/configuracao-nacional",
                       json=synthetic_configuration_payload(), headers=headers).status_code == 403


def test_create_response_never_leaks_unexpected_fields(fiscal_enabled, client, auth):
    resp = client.post("/api/v1/fiscal/configuracao-nacional",
                       json=synthetic_configuration_payload(), headers=auth("admin"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] == "SOPROLIFE-M29-ROUTER-v1"
    assert body["environment"] == "restricted"
    assert body["validation_state"] == "validated"
    assert body["configuration"]["p_tot_trib_sn"] == "6.00"
    assert body["configuration"]["issuer_cnpj"] == "63544026000110"
    # Never any secret/certificate/password-shaped key anywhere in the payload.
    flat = str(body).lower()
    assert "senha" not in flat and "password" not in flat and "pfx" not in flat and "p12" not in flat


def test_active_endpoint_404s_when_nothing_validated_yet(fiscal_enabled, client, auth):
    resp = client.get("/api/v1/fiscal/configuracao-nacional/ativa?environment=production",
                      headers=auth())
    assert resp.status_code == 404
    assert resp.json()["erro"]["mensagem"]["codigo"] == "national_dps_configuration_not_defined_for_any_real_document"


def test_same_version_different_payload_is_refused(fiscal_enabled, client, auth):
    client.post("/api/v1/fiscal/configuracao-nacional",
               json=synthetic_configuration_payload(), headers=auth("admin"))
    changed = synthetic_configuration_payload()
    changed["configuration"]["p_tot_trib_sn"] = "7.00"
    resp = client.post("/api/v1/fiscal/configuracao-nacional", json=changed, headers=auth("admin"))
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "national_dps_configuration_version_immutable"


def test_same_version_identical_payload_is_idempotent(fiscal_enabled, client, auth):
    first = client.post("/api/v1/fiscal/configuracao-nacional",
                        json=synthetic_configuration_payload(), headers=auth("admin"))
    second = client.post("/api/v1/fiscal/configuracao-nacional",
                         json=synthetic_configuration_payload(), headers=auth("admin"))
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
