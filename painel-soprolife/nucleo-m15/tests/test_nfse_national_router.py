"""M27 — /fiscal/status expõe a prontidão do provedor restrito, só leitura."""
import tests.test_nfse_foundation as _foundation

fiscal_enabled = _foundation.fiscal_enabled  # pytest fixture reuse


def test_status_reports_restricted_readiness_all_missing_by_default(fiscal_enabled, client, auth):
    response = client.get('/api/v1/fiscal/status', headers=auth())
    assert response.status_code == 200, response.text
    block = response.json()['restricted_provider_foundation']
    assert block['layout_version'] == 'restricted-v1.01-20260727'
    assert block['network_gate_enabled'] is False
    assert block['operational_network_possible'] is False
    assert set(block['missing_configuration']) == {
        'restricted_base_url', 'restricted_certificate_path',
        'restricted_certificate_password', 'restricted_network_gate_disabled',
    }


def test_status_endpoint_only_accepts_get():
    from app.routers.fiscal import router

    matching = [r for r in router.routes if getattr(r, "path", None) == "/fiscal/status"]
    assert matching and matching[0].methods == {"GET"}
